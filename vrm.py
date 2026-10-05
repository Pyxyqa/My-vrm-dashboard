"""Client minimal pentru API-ul Victron VRM + transformări de date (fără Streamlit)."""
from __future__ import annotations

import re
import time
from datetime import datetime

import pandas as pd
import requests

BASE = "https://vrmapi.victronenergy.com/v2"


class VRMError(Exception):
    pass


class VRM:
    def __init__(self, token: str, timeout: int = 25):
        self.s = requests.Session()
        self.s.headers.update({"X-Authorization": f"Token {token}", "Accept": "application/json"})
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            r = self.s.get(BASE + path, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise VRMError(f"Nu pot contacta VRM: {e}") from e
        if r.status_code == 401:
            raise VRMError("Token VRM invalid sau expirat (401).")
        if r.status_code == 403:
            raise VRMError("Token-ul nu are acces la această instalație (403).")
        if r.status_code == 429:
            raise VRMError("Prea multe cereri către VRM (429). Reîncearcă în câteva minute.")
        if not r.ok:
            raise VRMError(f"VRM a răspuns {r.status_code}: {r.text[:200]}")
        data = r.json()
        if data.get("success") is False:
            raise VRMError(f"VRM: {data.get('errors') or data}")
        return data

    def user_id(self) -> int:
        return self._get("/users/me")["user"]["id"]

    def installations(self) -> list[dict]:
        return self._get(f"/users/{self.user_id()}/installations")["records"]

    def diagnostics(self, site: int) -> list[dict]:
        return self._get(f"/installations/{site}/diagnostics", {"count": 1000})["records"]

    def stats(self, site: int, type_: str, interval: str, start: float, end: float) -> dict:
        return self._get(
            f"/installations/{site}/stats",
            {"type": type_, "interval": interval, "start": int(start), "end": int(end)},
        )


# ---------------------------------------------------------------- energie (kWh)
# Coduri VRM pentru fluxuri de energie (stats?type=kwh):
#   Pc PV->consumatori  Pb PV->baterie  Pg PV->rețea
#   Gc rețea->consumatori  Gb rețea->baterie
#   Bc baterie->consumatori  Bg baterie->rețea
# Px / Bx: energie PV / baterie spre rețea „mascată” prin corecții (export afișat 0),
# păstrată în producție / descărcare baterie. VRM nu întoarce aceste coduri.
FLOW_CODES = ["Pc", "Pb", "Pg", "Gc", "Gb", "Bc", "Bg", "Px", "Bx"]


def flows_df(payload: dict, tz: str) -> pd.DataFrame:
    """Transformă răspunsul stats?type=kwh într-un DataFrame cu fluxurile pe interval."""
    recs = payload.get("records") or {}
    series = {}
    for code, pts in recs.items():
        if not isinstance(pts, list):
            continue  # VRM întoarce `false` când nu există date
        acc: dict = {}
        for p in pts:
            if isinstance(p, (list, tuple)) and len(p) >= 2 and p[1] is not None:
                try:
                    acc[p[0]] = acc.get(p[0], 0.0) + float(p[1])
                except (TypeError, ValueError):
                    pass
        if acc:
            series[code] = pd.Series(acc, dtype="float64")
    if not series:
        idx = pd.DatetimeIndex([], tz=tz)
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in FLOW_CODES}, index=idx)
    df = pd.DataFrame(series).fillna(0.0)
    ts = df.index.astype("int64")
    unit = "ms" if ts.max() > 1e11 else "s"
    df.index = pd.to_datetime(ts, unit=unit, utc=True).tz_convert(tz)
    for c in FLOW_CODES:
        if c not in df:
            df[c] = 0.0
    return df.sort_index()


def group_flows(flows: pd.DataFrame, freq: str) -> pd.DataFrame:
    """freq: 'D' zile, 'M' luni, 'Y' ani. Indexul rezultat e naiv (ora locală)."""
    if flows.empty:
        return flows[FLOW_CODES].copy()
    local = flows[FLOW_CODES].copy()
    local.index = local.index.tz_localize(None)
    key = {"D": local.index.normalize(),
           "M": local.index.to_period("M").to_timestamp(),
           "Y": local.index.to_period("Y").to_timestamp()}[freq]
    return local.groupby(key).sum()


def summarize(f: pd.DataFrame) -> pd.DataFrame:
    """Mărimi derivate din fluxuri (kWh și %)."""
    out = pd.DataFrame(index=f.index)
    prod = f.Pc + f.Pb + f.Pg + f.Px
    cons = f.Pc + f.Gc + f.Bc
    out["Producție PV"] = prod
    out["Consum"] = cons
    out["Import rețea"] = f.Gc + f.Gb
    out["Export rețea"] = f.Pg + f.Bg
    out["Încărcare baterie"] = f.Pb + f.Gb
    out["Descărcare baterie"] = f.Bc + f.Bg + f.Bx
    out["Autoconsum %"] = ((f.Pc + f.Pb) / prod.where(prod > 0) * 100).fillna(0.0)
    out["Autonomie %"] = ((f.Pc + f.Bc) / cons.where(cons > 0) * 100).fillna(0.0)
    return out


def apply_corrections(flows: pd.DataFrame, corrections, site: int, site_name: str = "") -> pd.DataFrame:
    """Aplică corecțiile din Secrets ([[corrections]]) pe fluxurile zilnice ale unui sistem.

    Suportat: metric = "export" -> exportul în rețea devine 0 în perioada dată
    (producția PV și descărcarea bateriei rămân cele reale).
    Câmpuri: metric, from, to (AAAA-LL-ZZ, inclusiv), sites (opțional: ID-uri sau nume).
    """
    if flows.empty or not corrections:
        return flows
    f = flows.copy()
    days = f.index.tz_localize(None).normalize()
    for c in corrections:
        c = dict(c)
        if str(c.get("metric", "")).lower() != "export":
            continue
        only = {str(x).strip().lower() for x in c.get("sites", [])}
        if only and str(site) not in only and site_name.strip().lower() not in only:
            continue
        try:
            start = pd.Timestamp(str(c["from"])[:10])
            end = pd.Timestamp(str(c["to"])[:10])
        except (KeyError, ValueError):
            continue
        m = (days >= start) & (days <= end)
        f.loc[m, "Px"] += f.loc[m, "Pg"]
        f.loc[m, "Pg"] = 0.0
        f.loc[m, "Bx"] += f.loc[m, "Bg"]
        f.loc[m, "Bg"] = 0.0
    return f


def with_total(f: pd.DataFrame, labels: list[str]) -> pd.DataFrame:
    """Tabel sumarizat cu etichete text + rând TOTAL (procente recalculate corect)."""
    t = summarize(f)
    t.index = labels
    tot = summarize(f.sum().to_frame().T)
    tot.index = ["TOTAL"]
    return pd.concat([t, tot])


# ---------------------------------------------------------------- live (W)
# Potrivire după calea D-Bus a serviciului "system" (cea mai stabilă),
# cu rezervă după descrierea în engleză.
LIVE_RULES = {
    "pv": {"paths": [r"^/Dc/Pv/Power$", r"^/Ac/PvOn(Output|Grid|Genset)/L[123]/Power$"],
           "desc": [r"^pv - dc-coupled$", r"^pv - ac-coupled on .* l[123]$"], "mode": "sum"},
    "consumption": {"paths": [r"^/Ac/Consumption/L[123]/Power$"],
                    "desc": [r"^ac consumption l[123]$"], "mode": "sum"},
    "grid": {"paths": [r"^/Ac/Grid/L[123]/Power$"], "desc": [r"^grid l[123]$"], "mode": "sum"},
    "battery": {"paths": [r"^/Dc/Battery/Power$"], "desc": [r"^battery power$"], "mode": "first"},
    "soc": {"paths": [r"^/Dc/Battery/Soc$"],
            "desc": [r"^battery soc$", r"state of charge"], "mode": "first"},
}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _is_system(r: dict) -> bool:
    return (r.get("dbusServiceType") == "system"
            or str(r.get("Device") or r.get("device") or "").lower() == "system overview")


def live_values(records: list[dict], code_overrides: dict | None = None,
                stale_s: int = 1800, offline_s: int = 7200, now: float | None = None) -> dict:
    """Întoarce {cheie: {"value": float|None, "matched": [descrieri]}} + "_ts"."""
    code_overrides = code_overrides or {}
    # VRM păstrează ultima valoare a unui dispozitiv care nu mai trimite date (ex. invertorul
    # PV noaptea). Ignorăm valorile mult mai vechi decât cele mai noi date ale instalației.
    all_ts = [r.get("timestamp") for r in records if isinstance(r.get("timestamp"), (int, float))]
    newest = max(all_ts) if all_ts else None
    stale_keys: set = set()

    now = time.time() if now is None else now
    # instalație care nu a mai trimis nimic de mult (GX offline / fără internet):
    # toate valorile ei sunt vechi și nu trebuie afișate sau adunate în total
    offline = newest is None or newest < now - offline_s

    def fresh(r):
        if offline:
            return False
        t = r.get("timestamp")
        return not isinstance(t, (int, float)) or t >= newest - stale_s

    system = [r for r in records if _is_system(r)]
    out: dict = {}
    for key, rule in LIVE_RULES.items():
        hits: list[dict] = []
        if key in code_overrides:
            codes = set(code_overrides[key])
            hits = [r for r in records if r.get("code") in codes]
        else:
            for pool in (system, records):
                hits = [r for r in pool
                        if any(re.search(p, str(r.get("dbusPath") or "")) for p in rule["paths"])]
                if not hits:
                    hits = [r for r in pool
                            if any(re.search(p, str(r.get("description") or "").lower().strip())
                                   for p in rule["desc"])]
                if hits:
                    break
        live_hits = [r for r in hits if fresh(r)]
        if hits and not live_hits:
            stale_keys.add(key)
        hits_used = live_hits
        vals = [v for v in (_num(r.get("rawValue")) for r in hits_used) if v is not None]
        if not vals:
            # PV fără date recente = invertor oprit (noapte) -> 0 W
            value = 0.0 if key == "pv" and key in stale_keys else None
        elif rule["mode"] == "sum":
            value = sum(vals)
        else:
            value = vals[0]
        out[key] = {"value": value,
                    "matched": [f'{r.get("Device")}: {r.get("description")} [{r.get("code")}]'
                                + ("" if fresh(r) else " (date vechi, ignorat)") for r in hits]}
    out["_ts"] = datetime.fromtimestamp(newest) if newest else None
    out["_offline"] = offline
    return out


def combine_live(lives: list[dict]) -> dict:
    """Însumează valorile live ale mai multor sisteme (SOC = media)."""
    if len(lives) == 1:
        return lives[0]
    out: dict = {}
    for key, rule in LIVE_RULES.items():
        vals = [lv[key]["value"] for lv in lives if lv[key]["value"] is not None]
        if not vals:
            value = None
        elif key == "soc":
            value = sum(vals) / len(vals)
        else:
            value = sum(vals)
        out[key] = {"value": value,
                    "matched": [m for lv in lives for m in lv[key]["matched"]]}
    online = [lv for lv in lives if not lv.get("_offline")]
    tss = [lv["_ts"] for lv in online if lv["_ts"]]
    out["_ts"] = min(tss) if tss else None   # cea mai veche dintre sistemele online
    out["_offline"] = not online
    out["_offline_count"] = len(lives) - len(online)
    return out
