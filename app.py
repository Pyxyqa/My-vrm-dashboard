"""Dashboard fotovoltaic Victron VRM – Streamlit, cu login admin/oaspeți."""
from __future__ import annotations

import re
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

import vrm as V
from flow import flow_html
import base64
import hashlib
import hmac


def verify_password(password: str, stored: str) -> bool:
    """Verifică o parolă față de un hash PBKDF2-SHA256 generat cu make_hash.py."""
    try:
        algo, it, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(it))
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except Exception:
        return False

st.set_page_config(page_title="Fotovoltaic", page_icon="☀️", layout="wide")

LUNI = ["Ianuarie", "Februarie", "Martie", "Aprilie", "Mai", "Iunie", "Iulie",
        "August", "Septembrie", "Octombrie", "Noiembrie", "Decembrie"]
COLORS = {"Producție PV": "#F2A900", "Consum": "#3B7DD8",
          "Import rețea": "#D9534F", "Export rețea": "#2E9E5B"}


def secret(key, default=None):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


def find_secret(key, default=None):
    """Caută o cheie în Secrets la primul nivel sau în orice secțiune."""
    try:
        if key in st.secrets:
            return st.secrets[key]
        for v in st.secrets.values():
            if hasattr(v, "keys") and key in v:
                return v[key]
    except Exception:
        pass
    return default


# ================================================================ AUTENTIFICARE
def load_users() -> dict:
    return {k.lower(): dict(v) for k, v in (secret("users", {}) or {}).items()}


def expired(u: dict) -> bool:
    exp = u.get("expires")
    if not exp:
        return False
    try:
        return date.today() > date.fromisoformat(str(exp)[:10])
    except ValueError:
        return True


def user_profile(uname: str, u: dict) -> dict:
    combined = bool(u.get("combined", False))
    hide = [str(h).lower() for h in u.get("hide", [])]
    hide_base = list(hide)
    if combined and "show_soc" not in hide:
        hide.append("soc")                 # SOC mediu pe mai multe baterii nu are sens
    return {
        "username": uname,
        "name": u.get("name", uname),
        "role": u.get("role", "guest"),
        "sites": [int(s) for s in u.get("sites", [])],
        "combined": combined,
        "title": u.get("title") or u.get("combined_name") or "",
        "hide": hide,
        "hide_base": hide_base,
        # sisteme afișate separat SUB vederea principală (nu intră în total)
        "extra_sites": [dict(x) if hasattr(x, "keys") else str(x)
                        for x in u.get("extra_sites", [])],
    }


def login_gate() -> dict:
    user = st.session_state.get("user")
    if user:
        u = load_users().get(user["username"])
        if u and not expired(u):          # cont șters/expirat între timp -> delogare
            user = user_profile(user["username"], u)   # preia imediat modificările din Secrets
            st.session_state["user"] = user
            return user
        st.session_state.pop("user", None)

    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.markdown("## ☀️ Instalație fotovoltaică")
        with st.form("login"):
            username = st.text_input("Utilizator")
            password = st.text_input("Parolă", type="password")
            ok = st.form_submit_button("Intră", type="primary", use_container_width=True)
        if ok:
            fails = st.session_state.get("fails", 0)
            if fails:
                time.sleep(min(2 ** fails, 15))   # încetinește încercările repetate
            uname = username.strip().lower()
            u = load_users().get(uname)
            if u and verify_password(password, u.get("password_hash", "")) and not expired(u):
                st.session_state["fails"] = 0
                st.session_state["user"] = user_profile(uname, u)
                st.rerun()
            st.session_state["fails"] = fails + 1
            st.error("Utilizator sau parolă greșită, ori acces expirat.")
    st.stop()


# ================================================================ DATE VRM
@st.cache_resource
def client() -> V.VRM:
    return V.VRM(st.secrets["VRM_TOKEN"])


@st.cache_data(ttl=3600, show_spinner=False)
def installations() -> list[dict]:
    return client().installations()


def _year_bounds(year: int, tz: str):
    z = ZoneInfo(tz)
    start = datetime(year, 1, 1, tzinfo=z)
    end = min(datetime(year + 1, 1, 1, tzinfo=z), datetime.now(z))
    return start.timestamp(), end.timestamp()


@st.cache_data(ttl=900, show_spinner=False)
def _flows_current(site: int, year: int, tz: str) -> pd.DataFrame:
    return V.flows_df(client().stats(site, "kwh", "days", *_year_bounds(year, tz)), tz)


@st.cache_data(ttl=86400, show_spinner=False)
def _flows_past(site: int, year: int, tz: str) -> pd.DataFrame:
    return V.flows_df(client().stats(site, "kwh", "days", *_year_bounds(year, tz)), tz)


def flows_all(site: int, first_year: int, tz: str) -> pd.DataFrame:
    now_y = datetime.now(ZoneInfo(tz)).year
    parts = [(_flows_current if y == now_y else _flows_past)(site, y, tz)
             for y in range(first_year, now_y + 1)]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return V.flows_df({}, tz)
    df = pd.concat(parts)
    return df[~df.index.duplicated(keep="last")].sort_index()


def _month_bounds(year: int, month: int, tz: str):
    z = ZoneInfo(tz)
    start = datetime(year, month, 1, tzinfo=z)
    nxt = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=z)
    return start.timestamp(), min(nxt, datetime.now(z)).timestamp()


def _soc_fetch(site, year, month, tz):
    p = client().stats_custom(site, ["bs"], "hours", *_month_bounds(year, month, tz))
    return V.soc_end_of_day(p, tz, "bs")


@st.cache_data(ttl=900, show_spinner=False)
def _soc_current(site: int, year: int, month: int, tz: str) -> pd.Series:
    return _soc_fetch(site, year, month, tz)


@st.cache_data(ttl=86400, show_spinner=False)
def _soc_past(site: int, year: int, month: int, tz: str) -> pd.Series:
    return _soc_fetch(site, year, month, tz)


def soc_month(site: int, year: int, month: int, tz: str) -> pd.Series:
    now = datetime.now(ZoneInfo(tz))
    f = _soc_current if (year, month) == (now.year, now.month) else _soc_past
    try:
        return f(site, year, month, tz)
    except V.VRMError:
        return pd.Series(dtype="float64")


@st.cache_data(ttl=30, show_spinner=False)
def diagnostics(site: int) -> list[dict]:
    return client().diagnostics(site)


# ================================================================ UI helpers
def gauge(title, watts, vmin_w, vmax_w, color, pos_label="", neg_label=""):
    val = None if watts is None else watts / 1000
    g = {"axis": {"range": [vmin_w / 1000, vmax_w / 1000]},
         "bar": {"color": color, "thickness": 0.3}}
    if vmin_w < 0:
        g["steps"] = [{"range": [vmin_w / 1000, 0], "color": "rgba(46,158,91,0.12)"}]
        g["threshold"] = {"line": {"color": "#666", "width": 2}, "value": 0}
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=val if val is not None else 0,
        number={"suffix": " kW", "valueformat": ".2f",
                "font": {"color": color if val is not None else "#999"}},
        title={"text": title + ("" if val is not None else "<br><sub>indisponibil</sub>")},
        gauge=g))
    fig.update_layout(height=240, margin=dict(l=25, r=25, t=60, b=10))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    if val is not None and (pos_label or neg_label):
        st.caption(pos_label if val >= 0 else neg_label)


def soc_gauge(soc):
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=soc if soc is not None else 0,
        number={"suffix": " %", "valueformat": ".0f"},
        title={"text": "Baterie SOC" + ("" if soc is not None else "<br><sub>indisponibil</sub>")},
        gauge={"axis": {"range": [0, 100]}, "bar": {"color": "#7A5AF8", "thickness": 0.3},
               "steps": [{"range": [0, 20], "color": "rgba(217,83,79,0.15)"}]}))
    fig.update_layout(height=240, margin=dict(l=25, r=25, t=60, b=10))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def energy_chart(table: pd.DataFrame, title: str, key: str | None = None):
    t = table.drop(index="TOTAL", errors="ignore")
    fig = go.Figure()
    for col in ["Producție PV", "Consum", "Import rețea", "Export rețea"]:
        fig.add_bar(name=col, x=t.index, y=t[col], marker_color=COLORS[col],
                    hovertemplate="%{y:.1f} kWh")
    fig.update_layout(barmode="group", title=title, height=380, yaxis_title="kWh",
                      legend=dict(orientation="h", y=-0.2), margin=dict(l=10, r=10, t=50, b=10),
                      hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True, key=key)


def energy_table(table: pd.DataFrame, fname: str, key: str | None = None):
    cfg = {c: st.column_config.NumberColumn(c, format="%.0f %%")
           if c.endswith("%") or c == V.SOC_COL
           else st.column_config.NumberColumn(c + " (kWh)", format="%.2f")
           for c in table.columns}
    st.dataframe(table, column_config=cfg, use_container_width=True)
    st.download_button("⬇️ Descarcă CSV", table.to_csv(sep=";", decimal=",").encode("utf-8-sig"),
                       file_name=fname, mime="text/csv", key=key)


# ================================================================ DISPOZITIVE (doar admin)
# Al doilea dashboard: fiecare invertor, MPPT și baterie, cu valorile instantanee din
# /installations/{id}/diagnostics. Se deschide cu butonul din bara laterală sau cu ?pagina=dispozitive.
@st.cache_data(ttl=3600, show_spinner=False)
def system_overview(site: int) -> list[dict]:
    """Nume personalizate, model, firmware – doar pentru etichete; pagina merge și fără."""
    recs = client()._get(f"/installations/{site}/system-overview").get("records") or {}
    devs = recs.get("devices") if isinstance(recs, dict) else recs
    return [d for d in (devs or []) if isinstance(d, dict)]


# Grupează înregistrările din /diagnostics pe dispozitiv (Device + instance) și extrage
# mărimile principale pentru fiecare invertor, MPPT și baterie.
CATEGORY_ORDER = ["inverter", "mppt", "battery", "other"]

# (eticheta afișată, căi D-Bus, descrieri VRM în engleză ca rezervă, mod: first | sum | join)
HEADLINES = {
    "inverter": [
        ("Stare", [r"/State$", r"/StatusCode$"], [r"^(ve\.bus )?state$", r"^status$"], "first"),
        ("Putere ieșire AC", [r"/Ac/Out/L\d/P$", r"^/Ac/Power$"],
         [r"^output power( l?\d)?$"], "sum"),
        ("Putere intrare AC", [r"/Ac/ActiveIn/L\d/P$"], [r"^input power( l?\d)?$"], "sum"),
        ("Putere PV", [r"/Yield/Power$"], [r"^pv power$"], "first"),
        ("Tensiune ieșire", [r"/Ac/Out/L1/V$", r"/Ac/L1/Voltage$"],
         [r"^output voltage( phase)? ?(l)?1$"], "first"),
        ("Curent ieșire", [r"/Ac/Out/L1/I$", r"/Ac/L1/Current$"],
         [r"^output current( phase)? ?(l)?1$"], "first"),
        ("Tensiune intrare", [r"/Ac/ActiveIn/L1/V$"], [r"^input voltage( phase)? ?(l)?1$"], "first"),
        ("Frecvență ieșire", [r"/Ac/Out/L1/F$"], [r"^output frequency"], "first"),
        ("Tensiune DC", [r"/Dc/0/Voltage$"], [r"^(battery|dc) voltage$"], "first"),
        ("Curent DC", [r"/Dc/0/Current$"], [r"^(battery|dc) current$"], "first"),
        ("SOC", [r"/Soc$"], [r"^(state of charge|soc)$"], "first"),
        ("Temperatură", [r"/Dc/0/Temperature$"], [r"temperature$"], "first"),
        ("Producție totală", [r"/Ac/Energy/Forward$"], [r"^energy"], "first"),
    ],
    "mppt": [
        ("Stare", [r"/State$"], [r"^(charge )?state$"], "first"),
        ("Putere PV", [r"/Yield/Power$", r"/Pv/\d+/P$"], [r"^pv power$"], "first"),
        ("Tensiune PV", [r"/Pv/V$", r"/Pv/\d+/V$"], [r"^pv voltage"], "join"),
        ("Curent PV", [r"/Pv/I$", r"/Pv/\d+/I$"], [r"^pv current"], "join"),
        ("Tensiune baterie", [r"/Dc/0/Voltage$"], [r"^(battery|charger) voltage$"], "first"),
        ("Curent încărcare", [r"/Dc/0/Current$"], [r"^(battery|charge) current$"], "first"),
        ("Producție azi", [r"/History/Daily/0/Yield$"], [r"^yield today$"], "first"),
        ("Putere max. azi", [r"/History/Daily/0/MaxPower$"], [r"^maximum charge power today$"], "first"),
        ("Mod MPP", [r"/MppOperationMode$"], [r"tracker operation mode|mpp"], "first"),
        ("Temperatură", [r"/Dc/0/Temperature$"], [r"temperature$"], "first"),
        ("Eroare", [r"/ErrorCode$"], [r"^error( code)?$"], "first"),
    ],
    "battery": [
        ("SOC", [r"/Soc$"], [r"^(state of charge|soc)$"], "first"),
        ("Tensiune", [r"/Dc/0/Voltage$"], [r"^(battery )?voltage$"], "first"),
        ("Curent", [r"/Dc/0/Current$"], [r"^(battery )?current$"], "first"),
        ("Putere", [r"/Dc/0/Power$"], [r"^(battery )?power$"], "first"),
        ("Temperatură", [r"/Dc/0/Temperature$"], [r"^(battery )?temperature$"], "first"),
        ("SOH", [r"/Soh$"], [r"^(state of health|soh)$"], "first"),
        ("Stare", [r"/State$", r"/Info/ChargeRequest$"], [r"^state$"], "first"),
        ("Celulă min.", [r"/System/MinCellVoltage$"], [r"^min(imum)? cell voltage$"], "first"),
        ("Celulă max.", [r"/System/MaxCellVoltage$"], [r"^max(imum)? cell voltage$"], "first"),
        ("Temp. celule min.", [r"/System/MinCellTemperature$"], [r"^min(imum)? cell temp"], "first"),
        ("Temp. celule max.", [r"/System/MaxCellTemperature$"], [r"^max(imum)? cell temp"], "first"),
        ("Curent max. încărcare", [r"/Info/MaxChargeCurrent$"], [r"^max(imum)? charge current"], "first"),
        ("Curent max. descărcare", [r"/Info/MaxDischargeCurrent$"],
         [r"^max(imum)? discharge current"], "first"),
        ("Tensiune max. încărcare", [r"/Info/MaxChargeVoltage$"], [r"^max(imum)? charge voltage"], "first"),
        ("Timp rămas", [r"/TimeToGo$"], [r"^time to go$"], "first"),
        ("Module online", [r"/System/NrOfModulesOnline$"], [r"modules online"], "first"),
    ],
    "other": [
        ("Stare", [r"^/?State$"], [r"^state$"], "first"),
        ("Putere", [r"/Ac/Power$", r"/Dc/0/Power$"], [r"^power$"], "first"),
        ("Tensiune", [r"/Dc/0/Voltage$", r"/Ac/L1/Voltage$"], [r"^voltage$"], "first"),
        ("Curent", [r"/Dc/0/Current$", r"/Ac/L1/Current$"], [r"^current$"], "first"),
        ("Temperatură", [r"/Temperature$"], [r"^temperature$"], "first"),
    ],
}

# tipul mărimii, după unitatea din formattedValue / formatWithUnit (Wh înaintea lui W, VA înaintea lui V)
_UNIT_KIND = [
    (r"\b(k?Wh|Ah)$", "Energie"),
    (r"\b(k?W|k?VA)$", "Putere"),
    (r"\bV$", "Tensiune"),
    (r"\bA$", "Curent"),
    (r"(°\s?[CF]|\bK)$", "Temperatură"),
    (r"%$", "Procent / SOC"),
    (r"\bHz$", "Frecvență"),
]
_STATE_DESC = r"state|status|mode|error|alarm|warning|relay|connected|allowed|request|active"
KIND_ORDER = ["Stare", "Putere", "Tensiune", "Curent", "Procent / SOC", "Temperatură",
              "Frecvență", "Energie", "Altele"]


def _txt(v) -> str:
    return "" if v is None else str(v).strip()


def device_category(service: str, device: str) -> str:
    s, d = service.lower(), device.lower()
    if s == "pvinverter" or "pv inverter" in d:
        return "inverter"
    if s == "solarcharger" or "solar charger" in d or "mppt" in d:
        return "mppt"
    if s in ("vebus", "inverter", "multi", "acsystem") or \
            any(w in d for w in ("ve.bus", "inverter", "multi", "quattro")):
        return "inverter"
    if s == "battery" or any(w in d for w in ("battery", "bms", "shunt", "lynx")):
        return "battery"
    return "other"


def value_kind(r: dict) -> str:
    for src in (_txt(r.get("formattedValue")), _txt(r.get("formatWithUnit"))):
        for pat, kind in _UNIT_KIND:
            if src and re.search(pat, src):
                return kind
    if r.get("dataAttributeEnumValues") or re.search(_STATE_DESC, _txt(r.get("description")).lower()):
        return "Stare"
    return "Altele"


def _unit(text: str) -> str:
    m = re.search(r"[\d.,]\s*([^\d\s.,][^\d]*)$", text)
    return m.group(1).strip() if m else ""


def _fmt(value: float, unit: str) -> str:
    dec = 0 if unit in ("W", "VA", "%") else 2 if unit == "V" else 1
    return f"{value:.{dec}f}" + (f" {unit}" if unit else "")


def _find(recs: list[dict], paths: list[str], descs: list[str]) -> list[dict]:
    hits = [r for r in recs if any(re.search(p, _txt(r.get("dbusPath"))) for p in paths)]
    if not hits and descs:
        hits = [r for r in recs if any(re.search(p, _txt(r.get("description")).lower()) for p in descs)]
    return hits


def headline(category: str, recs: list[dict]) -> dict:
    """{eticheta: {"text": valoare formatată, "value": float|None}} pentru mărimile găsite."""
    out: dict = {}
    for label, paths, descs, mode in HEADLINES.get(category, []):
        hits = _find(recs, paths, descs)
        if not hits:
            continue
        nums = [V._num(r.get("rawValue")) for r in hits]
        if mode == "sum" and len(hits) > 1 and all(n is not None for n in nums):
            v = sum(nums)
            out[label] = {"text": _fmt(v, _unit(_txt(hits[0].get("formattedValue")))), "value": v}
        elif mode == "join" and len(hits) > 1:
            out[label] = {"text": " / ".join(_txt(r.get("formattedValue")) for r in hits),
                          "value": None}
        else:
            out[label] = {"text": _txt(hits[0].get("formattedValue")) or _txt(hits[0].get("rawValue")),
                          "value": nums[0]}
    return out


def _overview_index(overview: list[dict] | None) -> dict:
    idx: dict = {}
    for d in overview or []:
        inst = d.get("instance")
        for n in (d.get("name"), d.get("productName"), d.get("customName")):
            if n and inst is not None:
                idx.setdefault((_txt(n).lower(), str(inst)), d)
    return idx


def group_devices(records: list[dict], overview: list[dict] | None = None,
                  stale_s: int = 1800) -> list[dict]:
    """Dispozitivele din /diagnostics, fiecare cu categorie, nume, vechimea datelor și valori."""
    groups: dict = {}
    for r in records:
        dev = _txt(r.get("Device") or r.get("device")) or "Dispozitiv necunoscut"
        groups.setdefault((dev, _txt(r.get("instance"))), []).append(r)
    all_ts = [r.get("timestamp") for r in records if isinstance(r.get("timestamp"), (int, float))]
    newest = max(all_ts) if all_ts else None
    ov = _overview_index(overview)

    out = []
    for (dev, inst), recs in groups.items():
        service = next((_txt(r.get("dbusServiceType")) for r in recs if r.get("dbusServiceType")), "")
        cat = device_category(service, dev)
        tss = [r.get("timestamp") for r in recs if isinstance(r.get("timestamp"), (int, float))]
        ts = max(tss) if tss else None
        info = ov.get((dev.lower(), inst), {})
        # nume personalizat: din system-overview sau din atributele /CustomName, /ProductName
        own = {p: _txt(r.get("formattedValue")) for r in recs
               for p in ("/CustomName", "/ProductName") if _txt(r.get("dbusPath")).endswith(p)}
        custom = _txt(info.get("customName")) or own.get("/CustomName", "")
        product = _txt(info.get("productName")) or own.get("/ProductName", "")
        out.append({
            "key": f"{dev}|{inst}",
            "device": dev,
            "instance": inst,
            "service": service,
            "category": cat,
            "name": custom or product or dev,
            "product": product if product and product != (custom or dev) else "",
            "firmware": _txt(info.get("firmwareVersion")),
            "ts": ts,
            "stale": bool(ts and newest and ts < newest - stale_s),
            "headline": headline(cat, recs),
            "records": recs,
        })
    out.sort(key=lambda d: (CATEGORY_ORDER.index(d["category"]), d["name"].lower(),
                            int(d["instance"]) if d["instance"].isdigit() else 10**9, d["instance"]))
    return out


def device_table(recs: list[dict], now: float | None = None) -> pd.DataFrame:
    """Toate mărimile unui dispozitiv, ordonate pe tip (stare, putere, tensiune, curent…)."""
    now = time.time() if now is None else now
    rows = []
    for r in recs:
        t = r.get("timestamp")
        rows.append({
            "Mărime": value_kind(r),
            "Descriere": _txt(r.get("description")),
            "Valoare": _txt(r.get("formattedValue")) or _txt(r.get("rawValue")),
            "Cale D-Bus": _txt(r.get("dbusPath")),
            "Cod": _txt(r.get("code")),
            "Vechime (min)": round((now - t) / 60, 1) if isinstance(t, (int, float)) else None,
        })
    df = pd.DataFrame(rows, columns=["Mărime", "Descriere", "Valoare", "Cale D-Bus", "Cod",
                                     "Vechime (min)"])
    if df.empty:
        return df
    df["_o"] = df["Mărime"].map({k: i for i, k in enumerate(KIND_ORDER)})
    return df.sort_values(["_o", "Descriere"]).drop(columns="_o").reset_index(drop=True)


CAT_LABEL = {"inverter": "🔌 Invertoare", "mppt": "☀️ MPPT", "battery": "🔋 Baterii",
             "other": "🧩 Altele"}
CAT_SHORT = {"inverter": "invertoare", "mppt": "MPPT", "battery": "baterii", "other": "altele"}


def _age(ts, now) -> str:
    if not ts:
        return "—"
    s = max(0, now - ts)
    if s < 90:
        return f"acum {s:.0f} s"
    if s < 5400:
        return f"acum {s / 60:.0f} min"
    if s < 172800:
        return f"acum {s / 3600:.1f} h"
    return f"acum {s / 86400:.0f} zile"


def _tz(inst: dict) -> str:
    tz = inst.get("timezone") or "Europe/Bucharest"
    try:
        ZoneInfo(tz)
    except Exception:
        tz = "Europe/Bucharest"
    return tz


def _matches(d: dict, q: str) -> list[dict]:
    """Înregistrările unui dispozitiv care conțin textul căutat (descriere, cale, cod sau nume)."""
    if not q:
        return d["records"]
    if q in d["name"].lower() or q in d["device"].lower():
        return d["records"]
    return [r for r in d["records"]
            if any(q in str(r.get(k) or "").lower() for k in ("description", "dbusPath", "code"))]


def _device_card(d: dict, recs: list[dict], now: float, tz: str, expand: bool):
    with st.container(border=True):
        badge = " · ⚠️ date vechi" if d["stale"] else ""
        st.markdown(f"**{d['name']}**  ·  instanța {d['instance'] or '—'}{badge}")
        bits = [d["device"]] if d["name"] != d["device"] else []
        bits += [b for b in (d["product"], d["firmware"] and f"firmware {d['firmware']}") if b]
        if d["ts"]:
            loc = datetime.fromtimestamp(d["ts"], ZoneInfo(tz))
            bits.append(f"actualizat {loc:%d.%m %H:%M:%S} ({_age(d['ts'], now)})")
        st.caption(" · ".join(bits))
        head = list(d["headline"].items())
        for i in range(0, len(head), 4):
            cols = st.columns(4)
            for col, (label, v) in zip(cols, head[i:i + 4]):
                col.metric(label, v["text"] or "—")
        with st.expander(f"Toate valorile ({len(recs)})", expanded=expand):
            st.dataframe(device_table(recs, now), use_container_width=True, hide_index=True,
                         column_config={"Vechime (min)": st.column_config.NumberColumn(format="%.1f")})


def _summary(devs: list[dict], now: float) -> pd.DataFrame:
    """Un rând pe dispozitiv, cu mărimile principale – bun pentru comparat MPPT-uri între ele."""
    rows = []
    for d in devs:
        row = {"Dispozitiv": d["name"], "Instanța": d["instance"]}
        row.update({k: v["text"] for k, v in d["headline"].items()})
        row["Actualizat"] = _age(d["ts"], now) + (" ⚠️" if d["stale"] else "")
        rows.append(row)
    return pd.DataFrame(rows)


def _site_body(site: int, name: str, tz: str, get_diag, get_overview, query: str):
    now = time.time()
    try:
        records = get_diag(site)
    except V.VRMError as e:
        st.warning(f"Date indisponibile pentru {name}: {e}")
        return
    try:
        overview = get_overview(site)
    except V.VRMError:
        overview = []                     # doar etichete; pagina merge și fără
    devs = group_devices(records, overview)
    if not devs:
        st.info("VRM nu a întors date de diagnostic pentru acest sistem.")
        return

    tss = [d["ts"] for d in devs if d["ts"]]
    newest = max(tss) if tss else None
    if newest is None or newest < now - 7200:
        st.warning("⚠️ Sistemul nu a mai trimis date în ultimele 2 ore (GX offline?). "
                   "Valorile de mai jos sunt ultimele primite de VRM.")
    else:
        st.caption(f"Ultimele date VRM: {datetime.fromtimestamp(newest, ZoneInfo(tz)):%d.%m.%Y %H:%M:%S}"
                   f" ({_age(newest, now)}) · {len(records)} mărimi")

    by_cat = {c: [d for d in devs if d["category"] == c] for c in CATEGORY_ORDER}
    cats = [c for c in CATEGORY_ORDER if by_cat[c]]
    st.caption(" · ".join(f"{len(by_cat[c])} {CAT_SHORT[c]}" for c in cats))
    cat = st.radio("Categorie", cats, horizontal=True, key=f"devcat_{site}",
                   format_func=lambda c: f"{CAT_LABEL[c]} ({len(by_cat[c])})",
                   label_visibility="collapsed")
    shown = [(d, _matches(d, query)) for d in by_cat.get(cat, [])]
    shown = [(d, recs) for d, recs in shown if recs]
    if not shown:
        st.info("Niciun dispozitiv nu conține textul căutat." if query else "Niciun dispozitiv.")
        return

    # totaluri utile pe categorie (doar din dispozitive cu date recente)
    fresh = [d for d, _ in shown if not d["stale"]]
    tot = {"mppt": [("Putere PV totală MPPT", "Putere PV", "W")],
           "inverter": [("Putere ieșire AC totală", "Putere ieșire AC", "W")],
           "battery": [("Curent total baterii", "Curent", "A")]}.get(cat, [])
    for label, key, unit in tot:
        vals = [d["headline"][key]["value"] for d in fresh
                if key in d["headline"] and d["headline"][key]["value"] is not None]
        if len(vals) > 1:
            st.metric(label, f"{sum(vals):.0f} {unit}" if unit == "W" else f"{sum(vals):.1f} {unit}")

    if len(shown) > 1:
        st.dataframe(_summary([d for d, _ in shown], now), use_container_width=True, hide_index=True)
    for d, recs in shown:
        _device_card(d, recs, now, tz, expand=bool(query))

    csv = pd.concat([device_table(d["records"], now).assign(Dispozitiv=d["name"],
                                                               Instanța=d["instance"])
                     for d in devs], ignore_index=True)
    st.download_button("⬇️ Toate mărimile (CSV)", csv.to_csv(sep=";", index=False).encode("utf-8-sig"),
                       file_name=f"dispozitive_{site}_{datetime.now(ZoneInfo(tz)):%Y%m%d_%H%M}.csv",
                       mime="text/csv", key=f"devcsv_{site}")


def devices_page(insts: list[dict], default_sites: list, get_diag, get_overview, refresh_s: int):
    names = {i["idSite"]: i.get("name") or f"Instalația {i['idSite']}" for i in insts}
    with st.sidebar:
        default = [s for s in default_sites if s in names] or list(names)
        sites = st.multiselect("Sisteme", list(names), default=default, format_func=names.get,
                               key="dev_sites")
        auto = st.toggle(f"Reîmprospătare automată ({refresh_s} s)", value=True, key="dev_auto")
        if st.button("🔄 Citește acum din VRM", use_container_width=True):
            for f in (get_diag, get_overview):
                if hasattr(f, "clear"):
                    f.clear()
            st.rerun()

    st.title("🔧 Dispozitive")
    st.caption("Valorile instantanee pe fiecare invertor, MPPT și baterie, din diagnosticul VRM. "
               "Prospețimea lor depinde de intervalul de raportare al GX-ului către VRM.")
    query = st.text_input("Caută o mărime (descriere, cale D-Bus, cod sau nume dispozitiv)",
                          key="dev_q", placeholder="ex.: temperature, /Pv/V, cell").strip().lower()
    if not sites:
        st.info("Alege cel puțin un sistem din bara laterală.")
        return

    for site in sites:
        inst = next(i for i in insts if i["idSite"] == site)
        st.header(names[site])
        body = st.fragment(run_every=refresh_s if auto else None)(_site_body)
        body(site, names[site], _tz(inst), get_diag, get_overview, query)


# ================================================================ PAGINA
user = login_gate()
is_admin = user["role"] == "admin"

if not secret("VRM_TOKEN"):
    st.error("Lipsește VRM_TOKEN din Secrets.")
    st.stop()

try:
    insts = installations()
    all_insts = list(insts)
except V.VRMError as e:
    st.error(str(e))
    st.stop()

if secret("SITE_ID"):
    insts = [i for i in insts if int(i["idSite"]) == int(secret("SITE_ID"))]
if user["sites"] or not is_admin:   # oaspeții văd DOAR instalațiile din `sites`
    insts = [i for i in insts if int(i["idSite"]) in user["sites"]]
if not insts:
    st.warning("Nicio instalație disponibilă pentru acest cont.")
    st.stop()

with st.sidebar:
    st.markdown(f"**{user['name']}**  \n{'Administrator' if is_admin else 'Oaspete'}")
    if st.button("Ieșire", use_container_width=True):
        st.session_state.pop("user", None)
        st.rerun()
    # al doilea dashboard („Dispozitive”) – doar admin; pentru oaspeți ?pagina= e ignorat
    pagina = st.query_params.get("pagina", "") if is_admin else ""
    if is_admin:
        c1, c2 = st.columns(2)
        if c1.button("☀️ Dashboard", use_container_width=True,
                     type="secondary" if pagina == "dispozitive" else "primary"):
            st.query_params.pop("pagina", None)
            st.rerun()
        if c2.button("🔧 Dispozitive", use_container_width=True,
                     type="primary" if pagina == "dispozitive" else "secondary"):
            st.query_params["pagina"] = "dispozitive"
            st.rerun()
    st.divider()
    names = {i["idSite"]: i.get("name") or f"Instalația {i['idSite']}" for i in insts}
    if is_admin:
        st.caption("ID-uri pentru `sites`: " + ", ".join(f"{n} = {k}" for k, n in names.items()))

if is_admin and pagina == "dispozitive":
    _wanted = {str(x).strip().lower() for x in (find_secret("TOTAL_SITES") or [])}
    devices_page(insts, [k for k in names if str(k) in _wanted or names[k].strip().lower() in _wanted],
                 diagnostics, system_overview, int(secret("REFRESH_SECONDS", 60)))
    st.stop()

with st.sidebar:
    hide = list(user["hide"])
    total_view = False          # True = vederea „total”, sub care apar sistemele EXTRA
    if user.get("combined") and len(insts) > 1:
        # vedere combinată: suma tuturor sistemelor, fără nume și fără selecție
        sites = [i["idSite"] for i in insts]
        title = "Total sisteme"
        total_view = True
    else:
        TOTAL = "__total__"
        total_name = str(find_secret("TOTAL_NAME", "Sistem fotovoltaic_Team Montage SRL_PTJ"))
        options = list(names)
        if is_admin and len(insts) > 1:
            options = [TOTAL] + options
        sel = st.selectbox("Instalație", options,
                           format_func=lambda k: total_name if k == TOTAL else names[k]) \
            if len(options) > 1 else options[0]
        if sel == TOTAL:
            total_view = True
            # TOTAL_SITES (ID-uri sau nume exacte) e căutat oriunde în Secrets,
            # chiar dacă a ajuns din greșeală sub o secțiune [..]
            raw = find_secret("TOTAL_SITES") or []
            wanted = {str(x).strip().lower() for x in raw}
            default = [k for k in names
                       if str(k) in wanted or names[k].strip().lower() in wanted]
            if not default:
                if wanted:
                    st.warning("Numele/ID-urile din TOTAL_SITES nu se potrivesc cu sistemele din VRM.")
                default = list(names)
            sites = st.multiselect("Sisteme incluse în total", list(names), default=default,
                                   format_func=names.get, key="total_sites")
            if not sites:
                st.info("Alege cel puțin un sistem.")
                st.stop()
            if is_admin and set(sites) != set(default):
                st.caption("Ca alegerea să rămână salvată, pune linia de mai jos în Secrets, "
                           "**deasupra** primei linii care începe cu `[`:")
                st.code("TOTAL_SITES = [" + ", ".join(str(k) for k in sites) + "]", language="toml")
            title = total_name
            if "soc" not in hide:
                hide.append("soc")
        else:
            sites = [sel]
            title = names[sel]
    if is_admin and st.button("🔄 Reîncarcă datele", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

gcfg = dict(secret("gauges", {}) or {})
REFRESH = int(secret("REFRESH_SECONDS", 60))
all_names = {i["idSite"]: i.get("name") or f"Instalația {i['idSite']}" for i in all_insts}


def site_tz(sec_sites):
    inst = next(i for i in all_insts if i["idSite"] == sec_sites[0])
    tz = inst.get("timezone") or secret("TIMEZONE", "Europe/Bucharest")
    try:
        ZoneInfo(tz)
    except Exception:
        tz = "Europe/Bucharest"
    return tz


@st.fragment(run_every=REFRESH)
def live_panel(sec_sites, sec_hide, tz, key, soc_site=None):
    try:
        codes = dict(secret("live_codes", {}) or {})
        lives = {s: V.live_values(diagnostics(s), codes) for s in sec_sites}
        live = V.combine_live(list(lives.values()))
        if soc_site in lives:                       # SOC instant doar de la sistemul ales (Ekrano)
            live["soc"] = lives[soc_site]["soc"]
    except V.VRMError as e:
        st.warning(f"Date live indisponibile: {e}")
        return
    ts = live["_ts"]
    stamp = (f"Date VRM din {datetime.fromtimestamp(ts.timestamp(), ZoneInfo(tz)):%d.%m.%Y %H:%M:%S}"
             if ts else "")
    if live.get("_offline"):
        stamp = "⚠️ Instalația nu a mai trimis date în ultimele 2 ore (GX offline?)"
    elif live.get("_offline_count"):
        stamp += f" · {live['_offline_count']} sistem(e) fără date recente, excluse din total"
    st.session_state["_live_debug_" + key] = live
    if str(secret("GAUGE_STYLE", "flow")).lower() == "flow":
        components.html(flow_html(live, sec_hide, stamp), height=450)
        return
    gm, bm = gcfg.get("grid_max_w", 10000), gcfg.get("battery_max_w", 5000)
    panels = {
        "pv": lambda: gauge("Producție PV", live["pv"]["value"], 0,
                            gcfg.get("pv_max_w", 10000), "#F2A900"),
        "consumption": lambda: gauge("Consum", live["consumption"]["value"], 0,
                                     gcfg.get("consumption_max_w", 10000), "#3B7DD8"),
        "grid": lambda: gauge("Rețea", live["grid"]["value"], -gm, gm, "#D9534F",
                              "↓ import din rețea", "↑ export în rețea"),
        "battery": lambda: gauge("Baterie", live["battery"]["value"], -bm, bm, "#7A5AF8",
                                 "↑ se încarcă", "↓ se descarcă"),
        "soc": lambda: soc_gauge(live["soc"]["value"]),
    }
    shown = [k for k in panels if k not in sec_hide]
    for col, k in zip(st.columns(max(len(shown), 1)), shown):
        with col:
            panels[k]()
    st.caption(stamp, help=f"Se reîmprospătează automat la {REFRESH} s.")


def render_section(sec_sites, sec_title, sec_hide, key, show_diag=False, soc_site=None):
    """Un dashboard complet (flux live + KPI + tabele) pentru unul sau mai multe sisteme."""
    tz = site_tz(sec_sites)
    now = datetime.now(ZoneInfo(tz))
    if secret("START_YEAR"):
        first_year = int(secret("START_YEAR"))
    else:
        created = [int(i["syscreated"]) for i in all_insts
                   if i["idSite"] in sec_sites and i.get("syscreated")]
        first_year = (datetime.fromtimestamp(min(created), ZoneInfo(tz)).year
                      if created else now.year - 4)
    first_year = max(2010, min(first_year, now.year))

    st.title(f"☀️ {sec_title}")
    live_panel(sec_sites, sec_hide, tz, key, soc_site)

    try:
        with st.spinner("Încarc istoricul de energie din VRM…"):
            corr = [dict(c) for c in (find_secret("corrections") or [])]
            flows = pd.concat([V.apply_corrections(flows_all(s, first_year, tz), corr, s,
                                                   all_names.get(s, ""))
                               for s in sec_sites]).sort_index()
    except V.VRMError as e:
        st.error(str(e))
        return


    daily = V.group_flows(flows, "D")
    if daily.empty:
        st.info("VRM nu a întors date de energie pentru această instalație.")
        return

    def _sum(mask):
        return V.summarize(daily[mask].sum().to_frame().T).iloc[0]

    idx = daily.index
    periods = {
        "Azi": idx == pd.Timestamp(now.date()),
        LUNI[now.month - 1]: (idx.year == now.year) & (idx.month == now.month),
        str(now.year): idx.year == now.year,
        "Total": idx == idx,
    }
    st.subheader("Energie")
    for col, (label, mask) in zip(st.columns(len(periods)), periods.items()):
        s = _sum(mask)
        with col:
            st.metric(f"{label} · producție", f"{s['Producție PV']:,.1f} kWh".replace(",", " "))
            st.caption(f"Consum {s['Consum']:,.1f} · import {s['Import rețea']:,.1f} · "
                       f"export {s['Export rețea']:,.1f} kWh".replace(",", " "))

    tabs = ["📅 Zilnic", "🗓️ Lunar", "📈 Anual"] + (["🔧 Diagnostic"] if show_diag else [])
    t_day, t_month, t_year, *t_diag = st.tabs(tabs)
    years = sorted(set(idx.year), reverse=True)
    fn = "" if key == "main" else f"_{key}"

    with t_day:
        a, b = st.columns(2)
        y = a.selectbox("An", years, key=f"d_y_{key}")
        months = sorted(set(idx[idx.year == y].month), reverse=True)
        m = b.selectbox("Lună", months, format_func=lambda k: LUNI[k - 1], key=f"d_m_{key}")
        sel = daily[(idx.year == y) & (idx.month == m)]
        table = V.with_total(sel, [d.strftime("%d.%m.%Y") for d in sel.index])
        if soc_site:
            soc = soc_month(soc_site, y, m, tz)
            table[V.SOC_COL] = [soc.get(d) for d in sel.index] + [None]   # rândul TOTAL rămâne gol
        energy_chart(table.rename(index=lambda s: s[:5]), f"{LUNI[m - 1]} {y}", key=f"c_d_{key}")
        energy_table(table, f"energie_zilnic{fn}_{y}-{m:02d}.csv", key=f"dl_d_{key}")

    with t_month:
        y = st.selectbox("An", years, key=f"m_y_{key}")
        mon = V.group_flows(flows[flows.index.year == y], "M")
        table = V.with_total(mon, [LUNI[d.month - 1] for d in mon.index])
        energy_chart(table, f"Pe luni – {y}", key=f"c_m_{key}")
        energy_table(table, f"energie_lunar{fn}_{y}.csv", key=f"dl_m_{key}")

    with t_year:
        yr = V.group_flows(flows, "Y")
        table = V.with_total(yr, [str(d.year) for d in yr.index])
        energy_chart(table, "Pe ani", key=f"c_y_{key}")
        energy_table(table, f"energie_anual{fn}.csv", key=f"dl_y_{key}")

    if t_diag:
        with t_diag[0]:
            st.markdown("**Ce valori s-au folosit pentru diagrama live**")
            dbg = st.session_state.get("_live_debug_" + key, {})
            for k, v in dbg.items():
                if not k.startswith("_"):
                    st.write(f"`{k}` → {v['value']}  ·  "
                             f"{', '.join(v['matched']) or '— nimic găsit —'}")
            st.caption("Dacă o valoare e greșită, alege codurile corecte din tabelul de mai jos și "
                       "pune-le în Secrets la [live_codes], ex.: pv = [\"Pdc\"].")
            try:
                df = pd.DataFrame(diagnostics(sec_sites[0]))
                keep = [c for c in ["Device", "instance", "description", "code", "formattedValue",
                                    "rawValue", "dbusServiceType", "dbusPath", "idDataAttribute"]
                        if c in df]
                st.dataframe(df[keep], use_container_width=True, height=500)
            except V.VRMError as e:
                st.error(str(e))
            st.markdown(f"**Fus orar:** {tz} · **Istoric din:** {first_year} · "
                        f"**Zile cu date:** {len(daily)}")
            st.markdown("**Utilizatori configurați**")
            st.dataframe(pd.DataFrame([{"utilizator": k, "nume": u.get("name", ""),
                                        "rol": u.get("role", "guest"),
                                        "expiră": str(u.get("expires", "")),
                                        "activ": not expired(u)}
                                       for k, u in load_users().items()]),
                         use_container_width=True, hide_index=True)


def resolve_sites(keys) -> list:
    """ID-uri sau nume exacte -> idSite din contul VRM."""
    wanted = {str(x).strip().lower() for x in keys}
    return [i["idSite"] for i in all_insts
            if str(i["idSite"]) in wanted or str(i.get("name", "")).strip().lower() in wanted]


# ---------------------------------------------------------------- PAGINA
# SOC pentru tabelul zilnic: la un singur sistem -> al lui; la total -> SOC_SITE din Secrets
# (ID sau nume), altfel sistemul din total care are „ekrano” în nume
if len(sites) == 1:
    main_soc = sites[0]
else:
    cand = resolve_sites([find_secret("SOC_SITE")]) if find_secret("SOC_SITE") else []
    cand = [c for c in cand if c in sites] or \
        [s for s in sites if "ekrano" in all_names.get(s, "").lower()]
    main_soc = cand[0] if cand else None
    if main_soc and "soc" not in user["hide_base"]:
        hide = [h for h in hide if h != "soc"]      # la total arătăm SOC-ul Ekrano în cercul bateriei
render_section(sites, user["title"] or title, hide, "main", show_diag=is_admin, soc_site=main_soc)

# sisteme afișate separat, SUB vederea principală (nu intră în total)
# EXTRA_SITES acceptă: "nume" / ID  sau  {site = "nume sau ID", title = "titlu afișat"}
# doar sub vederea „total”; la un sistem ales individual nu se afișează nimic în plus
extra_keys = ((find_secret("EXTRA_SITES") or []) if is_admin else user["extra_sites"]) \
    if total_view else []
n = 0
for entry in extra_keys:
    e = dict(entry) if hasattr(entry, "keys") else {"site": entry}
    for ex in resolve_sites([e.get("site", "")]):
        if sites == [ex]:
            continue           # e deja afișat ca vedere principală
        st.divider()
        render_section([ex], str(e.get("title") or all_names[ex]),
                       list(user["hide_base"]), f"x{n}", soc_site=ex)
        n += 1
