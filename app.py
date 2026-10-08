"""Dashboard fotovoltaic Victron VRM – Streamlit, cu login admin/oaspeți."""
from __future__ import annotations

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
    cfg = {c: st.column_config.NumberColumn(c, format="%.0f %%") if c.endswith("%")
           else st.column_config.NumberColumn(c + " (kWh)", format="%.2f")
           for c in table.columns}
    st.dataframe(table, column_config=cfg, use_container_width=True)
    st.download_button("⬇️ Descarcă CSV", table.to_csv(sep=";", decimal=",").encode("utf-8-sig"),
                       file_name=fname, mime="text/csv", key=key)


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
    st.divider()
    names = {i["idSite"]: i.get("name") or f"Instalația {i['idSite']}" for i in insts}
    if is_admin:
        st.caption("ID-uri pentru `sites`: " + ", ".join(f"{n} = {k}" for k, n in names.items()))
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
def live_panel(sec_sites, sec_hide, tz, key):
    try:
        codes = dict(secret("live_codes", {}) or {})
        live = V.combine_live([V.live_values(diagnostics(s), codes) for s in sec_sites])
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


def render_section(sec_sites, sec_title, sec_hide, key, show_diag=False):
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
    live_panel(sec_sites, sec_hide, tz, key)

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
render_section(sites, user["title"] or title, hide, "main", show_diag=is_admin)

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
                       list(user["hide_base"]), f"x{n}")
        n += 1
