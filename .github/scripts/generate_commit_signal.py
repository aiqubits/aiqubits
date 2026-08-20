#!/usr/bin/env python3
import json
import math
import os
import random
import urllib.request
from datetime import datetime, timedelta, timezone
from html import escape

USER = os.environ.get("PROFILE_USER", "aiqubits")
TOKEN = os.environ["GH_TOKEN"]
OUT = os.environ.get("OUTPUT", "assets/profile-signal.svg")

end = datetime.now(timezone.utc).date()
start = end - timedelta(days=370)
query = '''query($login:String!,$from:DateTime!,$to:DateTime!){user(login:$login){contributionsCollection(from:$from,to:$to){contributionCalendar{totalContributions weeks{contributionDays{date weekday contributionCount}}}}}}'''
payload = json.dumps({"query": query, "variables": {"login": USER, "from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"}}).encode()
req = urllib.request.Request("https://api.github.com/graphql", data=payload, headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", "User-Agent": "aiqubits-profile"})
with urllib.request.urlopen(req, timeout=30) as r:
    body = json.load(r)
if body.get("errors"):
    raise SystemExit(body["errors"])

cal = body["data"]["user"]["contributionsCollection"]["contributionCalendar"]
weeks = cal["weeks"]
total = cal["totalContributions"]

W, H = 1200, 760
left, top = 64, 500
cell, gap = 16, 5
step = cell + gap
usable = W - left - 44
scale = min(1.0, usable / max(1, len(weeks) * step))
step *= scale
cell *= scale
grid_end = left + max(1, len(weeks) - 1) * step + cell
colors = {0: "#161b22", 1: "#0e4429", 2: "#006d32", 3: "#26a641", 4: "#39d353"}
counts = [d["contributionCount"] for w in weeks for d in w["contributionDays"]]
mx = max(counts or [1])

def level(c):
    if c <= 0: return 0
    if c <= max(1, mx * .20): return 1
    if c <= max(2, mx * .45): return 2
    if c <= max(3, mx * .70): return 3
    return 4

def fmt(v):
    return f"{v:.1f}"

week_totals = [sum(d["contributionCount"] for d in w["contributionDays"]) for w in weeks]
week_peak = max(week_totals or [1])
energies = [(v / week_peak) if week_peak else 0 for v in week_totals]
path_pts = []
for i, e in enumerate(energies):
    x = left + i * step + cell / 2
    y = top + 3 * step - e * 44 + math.sin(i * .55) * (3 + 7 * e)
    path_pts.append((x, y))
path_d = "M" + " L".join(f"{fmt(x)} {fmt(y)}" for x, y in path_pts)
orb_r = ";".join(fmt(3.2 + e * 7.8) for e in energies) or "4;4"
orb_op = ";".join(fmt(.35 + e * .65) for e in energies) or ".5;.5"
key_times = ";".join(fmt(i / max(1, len(energies)-1)) for i in range(len(energies))) if len(energies) > 1 else "0;1"

def burst_markup(date, count):
    if count <= 0:
        return ""
    rng = random.Random(int(date.replace("-", "")) ^ (count * 7919))
    n = 3 if count < mx * .45 else 5
    bits = []
    for j in range(n):
        angle = 2 * math.pi * j / n + rng.uniform(-.35, .35)
        dist = rng.uniform(8, 14) + min(7, count * .18)
        x2 = cell/2 + math.cos(angle) * dist
        y2 = cell/2 + math.sin(angle) * dist
        bits.append(f'<line x1="{fmt(cell/2)}" y1="{fmt(cell/2)}" x2="{fmt(x2)}" y2="{fmt(y2)}"/>')
        bits.append(f'<circle cx="{fmt(x2)}" cy="{fmt(y2)}" r="1.5"/>')
    return '<g class="burst">' + ''.join(bits) + '</g>'

cells, hot = [], []
for wi, w in enumerate(weeks):
    x = left + wi * step
    for d in w["contributionDays"]:
        y = top + d["weekday"] * step
        c, date = d["contributionCount"], d["date"]
        title = escape(f"{date}: {c} contribution{'s' if c != 1 else ''}")
        cells.append(f'<g class="day" transform="translate({fmt(x)} {fmt(y)})"><title>{title}</title><rect width="{fmt(cell)}" height="{fmt(cell)}" rx="2.5" fill="{colors[level(c)]}"/>{burst_markup(date,c)}</g>')
        if c >= max(1, mx * .72):
            hot.append((x + cell/2, y + cell/2, c))

shockwaves = []
for idx, (x, y, c) in enumerate(hot[-12:]):
    s = c / mx if mx else 0
    delay = (idx * .61) % 4.8
    shockwaves.append(f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="2" fill="none" stroke="#7ee787" stroke-width="1.2" opacity="0"><animate attributeName="r" values="2;{fmt(7+13*s)};2" dur="2.1s" begin="{delay:.2f}s" repeatCount="indefinite"/><animate attributeName="opacity" values="0;.65;0" dur="2.1s" begin="{delay:.2f}s" repeatCount="indefinite"/></circle>')

columns = []
for i, e in enumerate(energies):
    if e < .18: continue
    x = left + i * step + cell / 2
    h = 14 + 48 * e
    opacity = .05 + .16 * e
    columns.append(f'<line x1="{fmt(x)}" y1="{fmt(top+3*step-h/2)}" x2="{fmt(x)}" y2="{fmt(top+3*step+h/2)}" stroke="#39ff88" stroke-width="{fmt(.7+1.1*e)}" opacity="{opacity:.3f}"><animate attributeName="opacity" values="{opacity:.3f};{min(.5,opacity*2.5):.3f};{opacity:.3f}" dur="{2.6+(1-e)*2.4:.2f}s" begin="{(i%7)*.19:.2f}s" repeatCount="indefinite"/></line>')

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">
<title id="title">AIQUBITS system map and live contribution signal</title>
<desc id="desc">AIQUBITS future computing system map combined with real GitHub contribution data for {USER}.</desc>
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0d1117"/><stop offset="1" stop-color="#091410"/></linearGradient>
  <filter id="glow"><feGaussianBlur stdDeviation="2.8" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <style>
    text{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
    .head{{fill:#39ff88;font-size:15px}}.sub{{fill:#6e7681;font-size:11px}}.muted{{fill:#8b949e;font-size:12px}}.label{{fill:#f0f6fc;font-size:15px}}
    .day{{cursor:crosshair}}.day rect{{stroke:#21262d;stroke-width:.7;transform-box:fill-box;transform-origin:center;transition:transform .14s ease,stroke .14s ease,filter .14s ease}}
    .day:hover rect{{stroke:#b7ffc9;filter:url(#glow);transform:scale(1.18)}}
    .burst{{opacity:0;pointer-events:none;transition:opacity .12s ease;stroke:#7ee787;fill:#b7ffc9;stroke-width:1.1;transform-box:fill-box;transform-origin:center}}
    .day:hover .burst{{opacity:.95;animation:burst .42s ease-out both}}@keyframes burst{{0%{{transform:scale(.2);opacity:0}}45%{{opacity:1}}100%{{transform:scale(1.05);opacity:.15}}}}
    .spine{{fill:none;stroke:#39ff88;stroke-width:1.4;stroke-linecap:round;stroke-linejoin:round;opacity:.32}}.echo{{fill:none;stroke:#58a6ff;stroke-width:.8;opacity:.12}}
    @media (prefers-reduced-motion:reduce){{.day:hover .burst{{animation:none}}}}
  </style>
</defs>
<rect width="{W}" height="{H}" rx="18" fill="url(#bg)" stroke="#30363d"/>

<text x="30" y="38" class="head">/SYS/MAP</text><text x="1170" y="38" text-anchor="end" class="sub">FUTURE COMPUTING TOPOLOGY</text>
<g fill="none" stroke="#30363d" stroke-width="2"><path d="M600 110V150M600 150H330V205M600 150H870V205M870 285V325M870 325H700V365M870 325V365M870 325H1040V365"/></g>
<g text-anchor="middle"><rect x="470" y="65" width="260" height="55" rx="10" fill="#161b22" stroke="#39ff88"/><text x="600" y="99" fill="#39ff88" font-size="20">AIQUBITS</text><rect x="205" y="205" width="250" height="80" rx="12" fill="#161b22" stroke="#58a6ff"/><text x="330" y="237" fill="#58a6ff" font-size="16">AI COMPUTING</text><text x="330" y="263" class="muted">KeyCompute · Infrastructure</text><rect x="745" y="205" width="250" height="80" rx="12" fill="#161b22" stroke="#39ff88"/><text x="870" y="237" fill="#39ff88" font-size="16">AI NATIVE SYSTEMS</text><text x="870" y="263" class="muted">AINS · rust-agent</text><g fill="#161b22" stroke="#30363d"><rect x="620" y="365" width="160" height="62" rx="10"/><rect x="790" y="365" width="160" height="62" rx="10"/><rect x="960" y="365" width="160" height="62" rx="10"/></g><text x="700" y="392" class="label">MEMORY</text><text x="870" y="392" class="label">TOOLS</text><text x="1040" y="392" class="label">RUNTIME</text><text x="700" y="414" class="muted">WASM</text><text x="870" y="414" class="muted">NATIVE</text><text x="1040" y="414" class="muted">AGENT</text></g>
<circle cx="600" cy="150" r="4" fill="#39ff88"><animate attributeName="opacity" values=".2;1;.2" dur="2s" repeatCount="indefinite"/></circle><circle cx="870" cy="325" r="4" fill="#58a6ff"><animate attributeName="opacity" values="1;.2;1" dur="2.5s" repeatCount="indefinite"/></circle>
<path d="M30 462H1170" stroke="#30363d"/>

<text x="30" y="490" class="head">/COMMIT/SIGNAL</text><text x="1170" y="490" text-anchor="end" class="sub">LIVE · {start.isoformat()} → {end.isoformat()} · {total} CONTRIBUTIONS</text>
<g aria-hidden="true">{''.join(columns)}</g><path class="echo" d="{path_d}" transform="translate(0 5)"/><path class="spine" d="{path_d}"><animate attributeName="stroke-dasharray" values="2 10;10 5;3 9" dur="5.2s" repeatCount="indefinite"/><animate attributeName="opacity" values=".18;.48;.18" dur="4.4s" repeatCount="indefinite"/></path>
{''.join(cells)}{''.join(shockwaves)}
<g filter="url(#glow)" aria-hidden="true"><circle r="4.5" fill="#b7ffc9"><animateMotion dur="8.5s" repeatCount="indefinite" path="{path_d}"/><animate attributeName="r" values="{orb_r}" keyTimes="{key_times}" dur="8.5s" repeatCount="indefinite"/><animate attributeName="opacity" values="{orb_op}" keyTimes="{key_times}" dur="8.5s" repeatCount="indefinite"/></circle><circle r="2.2" fill="#58a6ff" opacity=".65"><animateMotion dur="8.5s" begin="-.7s" repeatCount="indefinite" path="{path_d}"/></circle></g>
<text x="{left}" y="730" class="sub">REAL GITHUB CONTRIBUTIONS · SIGNAL AMPLITUDE = WEEKLY DENSITY</text><text x="1140" y="730" text-anchor="end" class="sub">HOVER ACTIVE CELL → MICRO BURST</text>
</svg>'''

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    f.write(svg)
print(f"generated {OUT}: {len(weeks)} weeks, {total} contributions")
