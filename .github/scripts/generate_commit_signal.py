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
OUT = os.environ.get("OUTPUT", "assets/commit-history.svg")

end = datetime.now(timezone.utc).date()
start = end - timedelta(days=370)
query = '''query($login:String!,$from:DateTime!,$to:DateTime!){user(login:$login){contributionsCollection(from:$from,to:$to){contributionCalendar{totalContributions weeks{contributionDays{date weekday contributionCount color}}}}}}'''
payload = json.dumps({"query": query, "variables": {"login": USER, "from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"}}).encode()
req = urllib.request.Request(
    "https://api.github.com/graphql",
    data=payload,
    headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "aiqubits-profile",
    },
)
with urllib.request.urlopen(req, timeout=30) as r:
    body = json.load(r)
if body.get("errors"):
    raise SystemExit(body["errors"])

cal = body["data"]["user"]["contributionsCollection"]["contributionCalendar"]
weeks = cal["weeks"]
total = cal["totalContributions"]

W, H = 1200, 300
left, top = 64, 80
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
    if c <= 0:
        return 0
    if c <= max(1, mx * 0.20):
        return 1
    if c <= max(2, mx * 0.45):
        return 2
    if c <= max(3, mx * 0.70):
        return 3
    return 4

# Weekly energy is the signal spine. It is derived entirely from real counts.
week_totals = [sum(d["contributionCount"] for d in w["contributionDays"]) for w in weeks]
week_peak = max(week_totals or [1])
week_energy = [(v / week_peak) if week_peak else 0 for v in week_totals]

def fmt(v):
    return f"{v:.1f}"

# Build a contribution-driven path: quiet weeks sit low; busy weeks rise and bend.
path_pts = []
for i, energy in enumerate(week_energy):
    x = left + i * step + cell / 2
    y = top + 3 * step - energy * 46 + math.sin(i * 0.55) * (3 + 7 * energy)
    path_pts.append((x, y))

if path_pts:
    path_d = "M" + " L".join(f"{fmt(x)} {fmt(y)}" for x, y in path_pts)
else:
    path_d = f"M{left} {top + 3 * step} L{grid_end} {top + 3 * step}"

# Orb properties also react to real weekly contribution intensity.
orb_r_values = ";".join(fmt(3.2 + e * 7.8) for e in week_energy) or "4;4"
orb_opacity_values = ";".join(fmt(0.35 + e * 0.65) for e in week_energy) or "0.5;0.5"
key_times = ";".join(fmt(i / max(1, len(week_energy) - 1)) for i in range(len(week_energy))) if len(week_energy) > 1 else "0;1"

# Deterministic micro-fireworks: generated from date+count, shown only on hover.
def burst_markup(date, count):
    if count <= 0:
        return ""
    seed = int(date.replace("-", "")) ^ (count * 7919)
    rng = random.Random(seed)
    n = 3 if count < mx * 0.45 else 5
    bits = []
    for j in range(n):
        angle = (2 * math.pi * j / n) + rng.uniform(-0.35, 0.35)
        dist = rng.uniform(8, 14) + min(7, count * 0.18)
        x2 = cell / 2 + math.cos(angle) * dist
        y2 = cell / 2 + math.sin(angle) * dist
        bits.append(
            f'<line x1="{fmt(cell/2)}" y1="{fmt(cell/2)}" x2="{fmt(x2)}" y2="{fmt(y2)}"/>'
        )
        bits.append(f'<circle cx="{fmt(x2)}" cy="{fmt(y2)}" r="1.5"/>')
    return '<g class="burst">' + ''.join(bits) + '</g>'

cells = []
hot_cells = []
for wi, w in enumerate(weeks):
    x = left + wi * step
    for d in w["contributionDays"]:
        y = top + d["weekday"] * step
        c = d["contributionCount"]
        lvl = level(c)
        date = d["date"]
        title = escape(f"{date}: {c} contribution{'s' if c != 1 else ''}")
        burst = burst_markup(date, c)
        cells.append(
            f'<g class="day level-{lvl}" transform="translate({fmt(x)} {fmt(y)})">'
            f'<title>{title}</title>'
            f'<rect width="{fmt(cell)}" height="{fmt(cell)}" rx="2.5" fill="{colors[lvl]}" data-count="{c}"/>'
            f'{burst}</g>'
        )
        if c >= max(1, mx * 0.72):
            hot_cells.append((x + cell / 2, y + cell / 2, c, date))

# High-contribution days create asynchronous local shockwaves. This replaces the old mechanical scan.
shockwaves = []
for idx, (x, y, c, date) in enumerate(hot_cells[-12:]):
    strength = c / mx if mx else 0
    delay = (idx * 0.61) % 4.8
    max_r = 7 + 13 * strength
    shockwaves.append(
        f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="2" fill="none" stroke="#7ee787" stroke-width="1.2" opacity="0">'
        f'<animate attributeName="r" values="2;{fmt(max_r)};2" dur="2.1s" begin="{delay:.2f}s" repeatCount="indefinite"/>'
        f'<animate attributeName="opacity" values="0;.65;0" dur="2.1s" begin="{delay:.2f}s" repeatCount="indefinite"/>'
        '</circle>'
    )

# Busy weeks get faint vertical energy columns; low-activity weeks stay almost invisible.
columns = []
for i, e in enumerate(week_energy):
    if e < 0.18:
        continue
    x = left + i * step + cell / 2
    height = 14 + 48 * e
    opacity = 0.05 + 0.16 * e
    columns.append(
        f'<line x1="{fmt(x)}" y1="{fmt(top + 3*step - height/2)}" x2="{fmt(x)}" y2="{fmt(top + 3*step + height/2)}" '
        f'stroke="#39ff88" stroke-width="{fmt(0.7 + 1.1*e)}" opacity="{opacity:.3f}">'
        f'<animate attributeName="opacity" values="{opacity:.3f};{min(.5, opacity*2.5):.3f};{opacity:.3f}" dur="{2.6 + (1-e)*2.4:.2f}s" begin="{(i%7)*.19:.2f}s" repeatCount="indefinite"/>'
        '</line>'
    )

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">
<title id="title">AIQUBITS live contribution signal</title>
<desc id="desc">Real GitHub contribution counts for {USER}. Signal motion, energy, and local pulses are derived from actual contribution density.</desc>
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0d1117"/><stop offset="1" stop-color="#091410"/></linearGradient>
  <filter id="glow"><feGaussianBlur stdDeviation="2.8" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <filter id="soft"><feGaussianBlur stdDeviation="1.4"/></filter>
  <style>
    text{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
    .head{{fill:#39ff88;font-size:15px}}.sub{{fill:#6e7681;font-size:11px}}
    .day{{cursor:crosshair}}
    .day rect{{stroke:#21262d;stroke-width:.7;transform-box:fill-box;transform-origin:center;transition:transform .14s ease,stroke .14s ease,filter .14s ease}}
    .day:hover rect{{stroke:#b7ffc9;filter:url(#glow);transform:scale(1.18)}}
    .burst{{opacity:0;pointer-events:none;transition:opacity .12s ease;stroke:#7ee787;fill:#b7ffc9;stroke-width:1.1}}
    .day:hover .burst{{opacity:.95;animation:burst .42s ease-out both}}
    @keyframes burst{{0%{{transform:scale(.25);opacity:0}}45%{{opacity:1}}100%{{transform:scale(1);opacity:.15}}}}
    .spine{{fill:none;stroke:#39ff88;stroke-width:1.4;stroke-linecap:round;stroke-linejoin:round;opacity:.32}}
    .echo{{fill:none;stroke:#58a6ff;stroke-width:.8;stroke-linecap:round;stroke-linejoin:round;opacity:.12}}
    @media (prefers-reduced-motion: reduce){{.day:hover .burst{{animation:none}}}}
  </style>
</defs>
<rect width="{W}" height="{H}" rx="18" fill="url(#bg)" stroke="#30363d"/>
<text x="30" y="38" class="head">/COMMIT/SIGNAL</text>
<text x="1170" y="38" text-anchor="end" class="sub">LIVE · {start.isoformat()} → {end.isoformat()} · {total} CONTRIBUTIONS</text>

<g aria-hidden="true">{''.join(columns)}</g>
<path class="echo" d="{path_d}" transform="translate(0 5)"/>
<path class="spine" d="{path_d}">
  <animate attributeName="stroke-dasharray" values="2 10;10 5;3 9" dur="5.2s" repeatCount="indefinite"/>
  <animate attributeName="opacity" values=".18;.48;.18" dur="4.4s" repeatCount="indefinite"/>
</path>

{''.join(cells)}
{''.join(shockwaves)}

<g filter="url(#glow)" aria-hidden="true">
  <circle r="4.5" fill="#b7ffc9">
    <animateMotion dur="8.5s" repeatCount="indefinite" rotate="auto" path="{path_d}"/>
    <animate attributeName="r" values="{orb_r_values}" keyTimes="{key_times}" dur="8.5s" repeatCount="indefinite" calcMode="linear"/>
    <animate attributeName="opacity" values="{orb_opacity_values}" keyTimes="{key_times}" dur="8.5s" repeatCount="indefinite" calcMode="linear"/>
  </circle>
  <circle r="2.2" fill="#58a6ff" opacity=".65">
    <animateMotion dur="8.5s" begin="-.7s" repeatCount="indefinite" rotate="auto" path="{path_d}"/>
  </circle>
</g>

<text x="{left}" y="268" class="sub">REAL GITHUB CONTRIBUTIONS · SIGNAL AMPLITUDE = WEEKLY DENSITY</text>
<text x="1140" y="268" text-anchor="end" class="sub">HOVER CELL → MICRO BURST · CLICK FIELD → HISTORY</text>
</svg>'''

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    f.write(svg)
print(f"generated {OUT}: {len(weeks)} weeks, {total} contributions, {len(hot_cells)} hot days")
