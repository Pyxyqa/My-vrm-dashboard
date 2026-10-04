"""Diagrama „flux de energie” (SVG animat) pentru valorile live."""
from __future__ import annotations

import math

COL = {"pv": "#F2A900", "cons": "#3B7DD8", "imp": "#D9534F", "exp": "#2E9E5B", "bat": "#7A5AF8",
       "idle": "#9aa3b2"}
HUB = (260, 200)
R = 46
IDLE_W = 15  # sub acest prag (W) linia e considerată inactivă


def _kw(w):
    return "–" if w is None else f"{abs(w) / 1000:.2f}".replace(".", ",") + " kW"


def _edge(cx, cy, tx, ty, r):
    """Punct pe marginea cercului (cx,cy) spre (tx,ty)."""
    d = math.hypot(tx - cx, ty - cy) or 1
    return cx + (tx - cx) * r / d, cy + (ty - cy) * r / d


def _line(node, to_hub: bool, watts, color):
    nx, ny = node
    a = _edge(nx, ny, *HUB, R + 4)
    b = _edge(*HUB, nx, ny, 9)
    p0, p1 = (a, b) if to_hub else (b, a)
    active = watts is not None and abs(watts) >= IDLE_W
    w = 2.5 + min(4.0, abs(watts or 0) / 1500)
    cls = ' class="dash"' if active else ""
    c = color if active else COL["idle"]
    op = "1" if active else ".35"
    return (f'<path{cls} d="M{p0[0]:.1f} {p0[1]:.1f} L{p1[0]:.1f} {p1[1]:.1f}" '
            f'stroke="{c}" stroke-width="{w:.1f}" stroke-linecap="round" fill="none" opacity="{op}"/>')


def _node(pos, color, icon, value, label, extra="", above=False):
    x, y = pos
    ex = (f'<text x="{x}" y="{y + 32}" text-anchor="middle" class="ex">{extra}</text>'
          if extra else "")
    return (f'<circle cx="{x}" cy="{y}" r="{R}" class="nd" stroke="{color}" stroke-width="4"/>'
            f'<text x="{x}" y="{y - 8}" text-anchor="middle" font-size="22">{icon}</text>'
            f'<text x="{x}" y="{y + 15}" text-anchor="middle" class="val">{value}</text>{ex}'
            f'<text x="{x}" y="{y - R - 9 if above else y + R + 20}" text-anchor="middle" class="lb">{label}</text>')


def flow_html(live: dict, hide: list[str] | None = None, stamp: str = "") -> str:
    hide = hide or []
    v = {k: (live.get(k) or {}).get("value") for k in ("pv", "consumption", "grid", "battery", "soc")}
    pos = {"pv": (260, 76), "grid": (100, 310), "consumption": (420, 310), "battery": (440, 110)}
    lines, nodes = [], []

    if "pv" not in hide:
        lines.append(_line(pos["pv"], True, v["pv"], COL["pv"]))
        nodes.append(_node(pos["pv"], COL["pv"], "☀️", _kw(v["pv"]), "Producție PV", above=True))
    if "grid" not in hide:
        g = v["grid"]
        exp = g is not None and g < 0
        c = COL["exp"] if exp else COL["imp"]
        state = "" if g is None or abs(g) < IDLE_W else (" · export" if exp else " · import")
        lines.append(_line(pos["grid"], not exp, g, c))
        nodes.append(_node(pos["grid"], c, "⚡", _kw(g), "Rețea" + state))
    if "consumption" not in hide:
        lines.append(_line(pos["consumption"], False, v["consumption"], COL["cons"]))
        nodes.append(_node(pos["consumption"], COL["cons"], "🏠", _kw(v["consumption"]), "Consum"))
    if "battery" not in hide:
        b = v["battery"]
        chg = b is not None and b > 0
        state = "" if b is None or abs(b) < IDLE_W else (" · se încarcă" if chg else " · se descarcă")
        soc = "" if "soc" in hide or v["soc"] is None else f"{v['soc']:.0f} %"
        lines.append(_line(pos["battery"], not chg, b, COL["bat"]))
        nodes.append(_node(pos["battery"], COL["bat"], "🔋", _kw(b), "Baterie" + state, soc))

    foot = f'<div class="ft">{stamp}</div>' if stamp else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
:root{{--ink:#1d2330;--mute:#6b7280;--card:#ffffff;--hub:#9aa3b2}}
@media (prefers-color-scheme:dark){{:root{{--ink:#e8ebf0;--mute:#9aa3b2;--card:#1b2029;--hub:#6b7280}}}}
html,body{{margin:0;background:transparent;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
svg{{width:100%;max-width:560px;height:auto;display:block;margin:0 auto}}
.nd{{fill:var(--card)}} .val{{font-size:14px;font-weight:700;fill:var(--ink)}}
.lb{{font-size:13px;fill:var(--mute)}} .ex{{font-size:11px;fill:var(--mute)}}
.dash{{stroke-dasharray:7 9;animation:m 0.9s linear infinite}}
@keyframes m{{to{{stroke-dashoffset:-16}}}}
.ft{{text-align:center;font-size:12px;color:var(--mute);margin-top:2px}}
</style></head><body>
<svg viewBox="0 0 520 390" role="img" aria-label="Flux de energie">
{''.join(lines)}
<circle cx="{HUB[0]}" cy="{HUB[1]}" r="8" fill="var(--hub)"/>
{''.join(nodes)}
</svg>{foot}</body></html>"""
