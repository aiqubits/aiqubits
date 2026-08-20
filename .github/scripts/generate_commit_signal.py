#!/usr/bin/env python3
import json, os, urllib.request
from datetime import datetime, timedelta, timezone
from html import escape

USER = os.environ.get("PROFILE_USER", "aiqubits")
TOKEN = os.environ["GH_TOKEN"]
OUT = os.environ.get("OUTPUT", "assets/commit-history.svg")

end = datetime.now(timezone.utc).date()
start = end - timedelta(days=370)
query = '''query($login:String!,$from:DateTime!,$to:DateTime!){user(login:$login){contributionsCollection(from:$from,to:$to){contributionCalendar{totalContributions weeks{contributionDays{date weekday contributionCount color}}}}}}'''
payload = json.dumps({"query": query, "variables": {"login": USER, "from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"}}).encode()
req = urllib.request.Request("https://api.github.com/graphql", data=payload, headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", "User-Agent": "aiqubits-profile"})
with urllib.request.urlopen(req, timeout=30) as r:
    body = json.load(r)
if body.get("errors"):
    raise SystemExit(body["errors"])
cal = body["data"]["user"]["contributionsCollection"]["contributionCalendar"]
weeks = cal["weeks"]
total = cal["totalContributions"]

W,H = 1200,292
left,top = 64,76
cell,gap = 16,5
step = cell+gap
usable = W-left-44
scale = min(1.0, usable / max(1, len(weeks)*step))
step *= scale; cell *= scale
colors = {0:"#161b22",1:"#0e4429",2:"#006d32",3:"#26a641",4:"#39d353"}
counts = [d["contributionCount"] for w in weeks for d in w["contributionDays"]]
mx = max(counts or [1])
def level(c):
    if c <= 0: return 0
    if c <= max(1, mx*0.20): return 1
    if c <= max(2, mx*0.45): return 2
    if c <= max(3, mx*0.70): return 3
    return 4

def rect(x,y,d):
    c=d["contributionCount"]; lvl=level(c); date=d["date"]
    title=escape(f"{date}: {c} contribution{'s' if c != 1 else ''}")
    return f'<g class="day"><title>{title}</title><rect x="{x:.1f}" y="{y:.1f}" width="{cell:.1f}" height="{cell:.1f}" rx="2.5" fill="{colors[lvl]}" data-count="{c}"/></g>'

cells=[]; hot=[]
for wi,w in enumerate(weeks):
    x=left+wi*step
    for d in w["contributionDays"]:
        y=top+d["weekday"]*step
        cells.append(rect(x,y,d))
        if d["contributionCount"] and d["contributionCount"] >= max(1, mx*0.75):
            hot.append((x+cell/2,y+cell/2))

pulse=''.join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#7ee787"><animate attributeName="r" values="2;6;2" dur="2.4s" repeatCount="indefinite"/><animate attributeName="opacity" values=".25;1;.25" dur="2.4s" repeatCount="indefinite"/></circle>' for x,y in hot[-8:])
scan_w=max(42,step*3)
grid_end=left+len(weeks)*step
svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">
<title id="title">AIQUBITS live contribution signal</title><desc id="desc">Last 371 days of real GitHub contribution counts for {USER}, generated from GitHub GraphQL data.</desc>
<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0d1117"/><stop offset="1" stop-color="#0a1512"/></linearGradient><linearGradient id="scan" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#39ff88" stop-opacity="0"/><stop offset=".5" stop-color="#39ff88" stop-opacity=".75"/><stop offset="1" stop-color="#39ff88" stop-opacity="0"/></linearGradient><filter id="glow"><feGaussianBlur stdDeviation="2.5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter><style>text{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}.day rect{{stroke:#21262d;stroke-width:.7;transform-box:fill-box;transform-origin:center;transition:.15s ease}}.day:hover rect{{stroke:#b7ffc9;filter:url(#glow);transform:scale(1.25)}}.head{{fill:#39ff88;font-size:15px}}.sub{{fill:#6e7681;font-size:11px}}.trace{{fill:none;stroke:#39ff88;stroke-width:1.4;stroke-dasharray:4 9;opacity:.28}} </style></defs>
<rect width="{W}" height="{H}" rx="18" fill="url(#bg)" stroke="#30363d"/>
<text x="30" y="38" class="head">/COMMIT/SIGNAL</text><text x="1170" y="38" text-anchor="end" class="sub">LIVE · {start.isoformat()} → {end.isoformat()} · {total} CONTRIBUTIONS</text>
<path class="trace" d="M{left} {top+3*step:.1f} C{W*.28:.1f} {top-step:.1f} {W*.46:.1f} {top+6*step:.1f} {W*.62:.1f} {top+2*step:.1f} S{W*.84:.1f} {top:.1f} {grid_end:.1f} {top+4*step:.1f}"><animate attributeName="stroke-dashoffset" values="0;-52" dur="3.8s" repeatCount="indefinite"/></path>
{''.join(cells)}
<rect x="{left-scan_w:.1f}" y="{top-5:.1f}" width="{scan_w:.1f}" height="{7*step:.1f}" fill="url(#scan)" opacity=".14"><animate attributeName="x" values="{left-scan_w:.1f};{grid_end:.1f}" dur="7s" repeatCount="indefinite"/></rect>
{pulse}
<text x="{left}" y="260" class="sub">REAL GITHUB CONTRIBUTIONS · AUTO-SYNCED</text><text x="1140" y="260" text-anchor="end" class="sub">CLICK → CONTRIBUTION HISTORY</text>
</svg>'''
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT,"w",encoding="utf-8") as f: f.write(svg)
print(f"generated {OUT}: {len(weeks)} weeks, {total} contributions")
