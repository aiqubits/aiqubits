#!/usr/bin/env python3
"""Generate the animated SVG used by the aiqubits profile README.

The upper field selects external repositories by commits contained in merged PRs,
then displays the top ten in an unranked random walk. The lower field renders the
last year of GitHub activity and lets Ferris consume each active day.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from html import escape
from pathlib import Path
from typing import Any, Iterable


USER = os.environ.get("PROFILE_USER", "aiqubits")
TOKEN = os.environ.get("GH_TOKEN")
OUT = Path(os.environ.get("OUTPUT", "assets/profile-signal.svg"))
FERRIS_ASSET = Path(os.environ.get("FERRIS_ASSET", "assets/ferris-flat-noshadow.svg"))
OFFLINE_SVG = os.environ.get("OFFLINE_SVG")
OFFLINE_PROJECTS = os.environ.get("OFFLINE_PROJECTS")
README_PATH = Path(os.environ["README_PATH"]) if os.environ.get("README_PATH") else None
# GitHub Raw adds a CSP sandbox that blocks links inside a standalone SVG.
# jsDelivr preserves the SVG document's interactive repository links.
STANDALONE_SVG_URL = (
    "https://cdn.jsdelivr.net/gh/aiqubits/aiqubits@main/assets/profile-signal.svg"
)

WIDTH, HEIGHT = 1200, 760
CALENDAR_DAYS = 365
CRAB_DURATION = 120
SWALLOW_FADE_SECONDS = 0.45
PROJECT_DURATION = 120
PROJECT_SIMULATION_SECONDS = PROJECT_DURATION / 2
PROJECT_TIMESTEPS = (0.1, 0.05)
PROJECT_SIMULATION_ATTEMPTS = 12
PROJECT_GAP = 7.0
PROJECT_X_MIN, PROJECT_X_MAX = 34.0, WIDTH - 34.0
PROJECT_Y_MIN, PROJECT_Y_MAX = 58.0, 368.0
TOP_PROJECT_LIMIT = 10
GRAPHQL_URL = "https://api.github.com/graphql"

CALENDAR_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays { date weekday contributionCount }
        }
      }
    }
  }
}
"""

MERGED_PRS_QUERY = """
query($query: String!, $after: String) {
  search(query: $query, type: ISSUE, first: 100, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        merged
        commits { totalCount }
        repository { nameWithOwner owner { login } }
      }
    }
  }
}
"""


def graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    if not TOKEN:
        raise SystemExit(
            "GH_TOKEN is required (or set OFFLINE_SVG and OFFLINE_PROJECTS)"
        )
    payload = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "aiqubits-profile-signal",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.load(response)
    if body.get("errors"):
        messages = "; ".join(
            error.get("message", "GraphQL error") for error in body["errors"]
        )
        raise SystemExit(messages)
    return body["data"]


def fetch_calendar(today: date) -> tuple[list[dict[str, Any]], int, date, date]:
    end = today
    # GitHub contribution collections use a one-year window. Subtract one less
    # than the inclusive day count because the query includes both endpoints.
    start = end - timedelta(days=CALENDAR_DAYS - 1)
    data = graphql(
        CALENDAR_QUERY,
        {
            "login": USER,
            "from": f"{start.isoformat()}T00:00:00Z",
            "to": f"{end.isoformat()}T23:59:59Z",
        },
    )
    user = data.get("user")
    if not user:
        raise SystemExit(f"GitHub user not found: {USER}")
    calendar = user["contributionsCollection"]["contributionCalendar"]
    weeks = calendar["weeks"]
    returned_days = [
        date.fromisoformat(day["date"])
        for week in weeks
        for day in week["contributionDays"]
    ]
    actual_start = min(returned_days, default=start)
    actual_end = max(returned_days, default=end)
    return weeks, calendar["totalContributions"], actual_start, actual_end


def fetch_merged_prs() -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    cursor = None
    search = f"author:{USER} is:pr is:merged"
    while True:
        page = graphql(MERGED_PRS_QUERY, {"query": search, "after": cursor})["search"]
        nodes.extend(node for node in page["nodes"] if node and node.get("merged"))
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = page["pageInfo"]["endCursor"]


def aggregate_external_projects(prs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    projects: dict[str, dict[str, Any]] = defaultdict(lambda: {"commits": 0, "prs": 0})
    for pr in prs:
        repository = pr.get("repository") or {}
        owner = (repository.get("owner") or {}).get("login", "")
        name = repository.get("nameWithOwner")
        if not name or owner.casefold() == USER.casefold():
            continue
        projects[name]["commits"] += int((pr.get("commits") or {}).get("totalCount", 0))
        projects[name]["prs"] += 1
    ranked = [
        {"repository": repository, "commits": values["commits"], "prs": values["prs"]}
        for repository, values in projects.items()
    ]
    return sorted(
        ranked, key=lambda item: (-item["commits"], item["repository"].casefold())
    )


def load_offline_calendar(path: Path) -> tuple[list[dict[str, Any]], int, date, date]:
    """Read calendar titles from the previously generated SVG for local previews."""
    source = path.read_text(encoding="utf-8")
    matches = re.findall(
        r"<g class=\"day\"[^>]*><title>(\d{4}-\d{2}-\d{2}): (\d+) contribution(?:s)?</title>",
        source,
    )
    if not matches:
        raise SystemExit(f"No contribution calendar found in {path}")

    days = [(date.fromisoformat(day), int(count)) for day, count in matches]
    grouped: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for day, count in days:
        weekday = (day.weekday() + 1) % 7  # GitHub uses Sunday = 0.
        sunday = day - timedelta(days=weekday)
        grouped[sunday].append(
            {"date": day.isoformat(), "weekday": weekday, "contributionCount": count}
        )
    weeks = [
        {"contributionDays": sorted(grouped[sunday], key=lambda item: item["date"])}
        for sunday in sorted(grouped)
    ]
    returned_dates = [day for day, _ in days]
    return (
        weeks,
        sum(count for _, count in days),
        min(returned_dates),
        max(returned_dates),
    )


def load_offline_projects(raw: str) -> list[dict[str, Any]]:
    path = Path(raw)
    payload = json.loads(path.read_text(encoding="utf-8") if path.exists() else raw)
    projects = [
        {
            "repository": str(item["repository"]),
            "commits": int(item["commits"]),
            "prs": int(item.get("prs", 1)),
        }
        for item in payload
    ]
    return sorted(
        projects, key=lambda item: (-item["commits"], item["repository"].casefold())
    )


def update_readme_cache_key(path: Path, svg: str) -> None:
    """Change the image URL whenever SVG content changes, bypassing GitHub's cache."""
    source = path.read_text(encoding="utf-8")
    cache_key = sha256(svg.encode()).hexdigest()[:12]
    standalone_url = re.escape(STANDALONE_SVG_URL)
    updated, replacements = re.subn(
        rf'((?:href|src)="(?:{standalone_url}|\./assets/profile-signal\.svg))(?:\?v=[^"]+)?(")',
        rf"\1?v={cache_key}\2",
        source,
    )
    if replacements != 2:
        raise SystemExit(
            f"Expected linked profile-signal.svg preview in {path}, found {replacements} URLs"
        )
    if updated != source:
        path.write_text(updated, encoding="utf-8")


def fmt(value: float) -> str:
    return f"{value:.1f}"


def contribution_level(count: int, peak: int) -> int:
    if count <= 0:
        return 0
    if count <= max(1, peak * 0.20):
        return 1
    if count <= max(2, peak * 0.45):
        return 2
    if count <= max(3, peak * 0.70):
        return 3
    return 4


def ferris_symbol(path: Path) -> str:
    """Embed the archived rust-lang Ferris SVG without redrawing its paths."""
    source = path.read_text(encoding="utf-8")
    svg = re.search(
        r"<svg\b(?P<attributes>[^>]*)>(?P<body>.*)</svg>\s*$", source, re.DOTALL
    )
    if not svg:
        raise SystemExit(f"Invalid Ferris SVG: {path}")
    view_box = re.search(r'viewBox="([^"]+)"', svg.group("attributes"))
    root_style = re.search(r'style="([^"]+)"', svg.group("attributes"))
    if not view_box:
        raise SystemExit(f"Ferris SVG has no viewBox: {path}")
    style = f' style="{escape(root_style.group(1), quote=True)}"' if root_style else ""
    return (
        f'<symbol id="ferris-artwork" viewBox="{escape(view_box.group(1), quote=True)}"{style}>'
        f"{svg.group('body')}</symbol>"
    )


def project_width(project: dict[str, Any]) -> float:
    repository = project["repository"]
    commits = project["commits"]
    count_width = len(f"+{commits}") * 7.8
    return round(
        min(535.0, max(210.0, 36 + len(repository) * 7.35 + count_width)),
        1,
    )


def projects_overlap(
    first: tuple[float, float],
    first_width: float,
    second: tuple[float, float],
    second_width: float,
    gap: float = PROJECT_GAP,
) -> bool:
    first_x, first_y = first
    second_x, second_y = second
    return (
        first_x < second_x + second_width + gap
        and first_x + first_width + gap > second_x
        and first_y < second_y + 34.0 + gap
        and first_y + 34.0 + gap > second_y
    )


def separate_projects(
    negative: dict[str, float],
    positive: dict[str, float],
    axis: str,
    penetration: float,
) -> None:
    size_key = "width" if axis == "x" else "height"
    velocity_key = "vx" if axis == "x" else "vy"
    lower = PROJECT_X_MIN if axis == "x" else PROJECT_Y_MIN
    upper = PROJECT_X_MAX if axis == "x" else PROJECT_Y_MAX
    needed = penetration + 0.25
    negative_space = negative[axis] - lower
    positive_space = upper - (positive[axis] + positive[size_key])
    negative_move = min(needed / 2, negative_space)
    positive_move = min(needed - negative_move, positive_space)
    remaining = needed - negative_move - positive_move
    if remaining > 0:
        extra_negative = min(remaining, negative_space - negative_move)
        negative_move += extra_negative
        remaining -= extra_negative
    if remaining > 1e-6:
        raise RuntimeError("project labels cannot be separated inside the motion field")
    negative[axis] -= negative_move
    positive[axis] += positive_move

    negative_velocity = negative[velocity_key]
    positive_velocity = positive[velocity_key]
    if negative_velocity > positive_velocity:
        negative[velocity_key], positive[velocity_key] = (
            positive_velocity,
            negative_velocity,
        )
    else:
        center_velocity = (negative_velocity + positive_velocity) / 2
        separation_speed = max(10.0, abs(negative_velocity - positive_velocity))
        negative[velocity_key] = center_velocity - separation_speed / 2
        positive[velocity_key] = center_velocity + separation_speed / 2


def _simulate_project_paths_once(
    projects: list[dict[str, Any]], timestep: float, attempt: int
) -> list[list[tuple[float, float]]]:
    """Run one deterministic collision simulation and validate its SVG path."""
    states: list[dict[str, float]] = []
    for index, project in enumerate(projects):
        width = project_width(project)
        column, row = index % 2, index // 2
        seed = (
            sum(
                (position + 1) * ord(char)
                for position, char in enumerate(project["repository"])
            )
            + project["commits"] * 97
            + attempt * 104729 * (index + 1)
        )
        rng = random.Random(seed)
        speed = rng.uniform(16.0, 27.0)
        angle = rng.uniform(0, math.tau)
        vx, vy = math.cos(angle) * speed, math.sin(angle) * speed
        if abs(vx) < 8:
            vx = math.copysign(8, vx or 1)
        if abs(vy) < 6:
            vy = math.copysign(6, vy or 1)
        states.append(
            {
                "x": min(PROJECT_X_MAX - width, 54.0 + column * 590),
                "y": 62.0 + row * 66,
                "width": width,
                "height": 34.0,
                "vx": vx,
                "vy": vy,
            }
        )

    frames = [[(state["x"], state["y"]) for state in states]]
    steps = round(PROJECT_SIMULATION_SECONDS / timestep)
    for _ in range(steps):
        for state in states:
            state["x"] += state["vx"] * timestep
            state["y"] += state["vy"] * timestep
            if state["x"] < PROJECT_X_MIN:
                state["x"] = PROJECT_X_MIN
                state["vx"] = abs(state["vx"])
            elif state["x"] + state["width"] > PROJECT_X_MAX:
                state["x"] = PROJECT_X_MAX - state["width"]
                state["vx"] = -abs(state["vx"])
            if state["y"] < PROJECT_Y_MIN:
                state["y"] = PROJECT_Y_MIN
                state["vy"] = abs(state["vy"])
            elif state["y"] + state["height"] > PROJECT_Y_MAX:
                state["y"] = PROJECT_Y_MAX - state["height"]
                state["vy"] = -abs(state["vy"])

        for _ in range(12):
            collision_found = False
            for first_index, first in enumerate(states):
                for second in states[first_index + 1 :]:
                    overlap_x = min(
                        first["x"] + first["width"] + PROJECT_GAP,
                        second["x"] + second["width"] + PROJECT_GAP,
                    ) - max(first["x"], second["x"])
                    overlap_y = min(
                        first["y"] + first["height"] + PROJECT_GAP,
                        second["y"] + second["height"] + PROJECT_GAP,
                    ) - max(first["y"], second["y"])
                    if overlap_x <= 0 or overlap_y <= 0:
                        continue
                    collision_found = True
                    if overlap_x < overlap_y:
                        negative, positive = sorted(
                            (first, second),
                            key=lambda state: state["x"] + state["width"] / 2,
                        )
                        separate_projects(negative, positive, "x", overlap_x)
                    else:
                        negative, positive = sorted(
                            (first, second),
                            key=lambda state: state["y"] + state["height"] / 2,
                        )
                        separate_projects(negative, positive, "y", overlap_y)
            if not collision_found:
                break
        else:
            raise RuntimeError("project collision solver did not converge")
        frames.append([(state["x"], state["y"]) for state in states])

    # Validate the quantized coordinates that are actually serialized into SVG.
    frames = [[(round(x, 1), round(y, 1)) for x, y in frame] for frame in frames]
    widths = [state["width"] for state in states]
    for frame in frames:
        for first_index, first in enumerate(frame):
            for second_index in range(first_index + 1, len(frame)):
                if projects_overlap(
                    first,
                    widths[first_index],
                    frame[second_index],
                    widths[second_index],
                ):
                    raise RuntimeError(
                        "project collision solver produced an overlapping frame"
                    )

    # The browser linearly interpolates between samples. For each axis, compute the
    # exact open time interval where two expanded rectangles overlap, then
    # reject the segment if the horizontal and vertical intervals intersect.
    def overlap_window(
        start: float, finish: float, lower: float, upper: float
    ) -> tuple[float, float] | None:
        delta = finish - start
        if abs(delta) < 1e-12:
            return (0.0, 1.0) if lower < start < upper else None
        first = (lower - start) / delta
        second = (upper - start) / delta
        return max(0.0, min(first, second)), min(1.0, max(first, second))

    for frame_index, (current, following) in enumerate(zip(frames, frames[1:])):
        for first_index, first in enumerate(current):
            for second_index in range(first_index + 1, len(current)):
                second = current[second_index]
                first_finish = following[first_index]
                second_finish = following[second_index]
                horizontal = overlap_window(
                    second[0] - first[0],
                    second_finish[0] - first_finish[0],
                    -(widths[second_index] + PROJECT_GAP),
                    widths[first_index] + PROJECT_GAP,
                )
                vertical = overlap_window(
                    second[1] - first[1],
                    second_finish[1] - first_finish[1],
                    -(34.0 + PROJECT_GAP),
                    34.0 + PROJECT_GAP,
                )
                if (
                    horizontal
                    and vertical
                    and max(horizontal[0], vertical[0]) + 1e-10
                    < min(horizontal[1], vertical[1])
                ):
                    raise RuntimeError(
                        "project collision solver produced an overlap between frames "
                        f"{frame_index} and {frame_index + 1}: labels "
                        f"{first_index} and {second_index}"
                    )

    loop_frames = frames + frames[-2::-1]
    return [[frame[index] for frame in loop_frames] for index in range(len(projects))]


def simulate_project_paths(
    projects: list[dict[str, Any]],
) -> list[list[tuple[float, float]]]:
    """Build a collision-safe loop, retrying deterministically when needed.

    Discrete collision resolution can occasionally make the straight SMIL
    interpolation between two valid samples cross another label. Every attempt
    is analytically validated; alternate deterministic velocities and a finer
    fallback timestep keep future project-name combinations from breaking the
    scheduled generator while never emitting an overlapping path.
    """
    last_error: RuntimeError | None = None
    for timestep in PROJECT_TIMESTEPS:
        for attempt in range(PROJECT_SIMULATION_ATTEMPTS):
            try:
                return _simulate_project_paths_once(projects, timestep, attempt)
            except RuntimeError as error:
                last_error = error
    raise RuntimeError(
        "could not generate collision-safe project paths after "
        f"{len(PROJECT_TIMESTEPS) * PROJECT_SIMULATION_ATTEMPTS} attempts"
    ) from last_error


def project_motion(
    project: dict[str, Any], index: int, positions: list[tuple[float, float]]
) -> str:
    """Return an unranked project label linked to its GitHub repository."""
    repository = project["repository"]
    commits = project["commits"]
    pr_count = project["prs"]
    node_width = project_width(project)
    node_height = 34.0
    base_x, base_y = positions[0]
    count_width = len(f"+{commits}") * 7.8
    count_x = node_width - 14
    available_name_width = node_width - 36 - count_width
    name_fit = (
        f' textLength="{fmt(available_name_width)}" lengthAdjust="spacingAndGlyphs"'
        if len(repository) * 7.35 > available_name_width
        else ""
    )
    repository_url = f"https://github.com/{repository}"
    node = f'''<a class="project-link" href="{escape(repository_url, quote=True)}" xlink:href="{escape(repository_url, quote=True)}" target="_blank" rel="noopener noreferrer" aria-label="Open {escape(repository, quote=True)} on GitHub">
<g class="project project-{index}" transform="translate({fmt(base_x)} {fmt(base_y)})">
  <title>{escape(repository)}: {commits} commit{"s" if commits != 1 else ""} in {pr_count} merged PR{"s" if pr_count != 1 else ""}</title>
  <rect class="project-shell" width="{fmt(node_width)}" height="{fmt(node_height)}" rx="9"/>
  <text class="project-name" x="14" y="21.5"{name_fit}>{escape(repository)}</text><text class="commit-count" x="{fmt(count_x)}" y="21.5" text-anchor="end">+{commits}</text>
</g>
</a>'''
    return node


def project_animation(index: int, positions: list[tuple[float, float]]) -> str:
    """Return pauseable CSS keyframes with the simulation's original timing."""
    last_index = max(1, len(positions) - 1)
    keyframes = "".join(
        f"{frame_index * 100 / last_index:.6f}%"
        f"{{transform:translate({fmt(x)}px,{fmt(y)}px)}}"
        for frame_index, (x, y) in enumerate(positions)
    )
    return (
        f"@keyframes project-motion-{index}{{{keyframes}}}"
        f".project-{index}{{animation:project-motion-{index} "
        f"{PROJECT_DURATION}s linear -17s infinite}}"
    )


def build_crab_route(
    active: list[dict[str, Any]], grid_end: float
) -> tuple[str, str, str, list[float]]:
    if not active:
        return "", "", "", []

    # Use exactly the coordinates serialized into the path so every keyPoint
    # lands on the visual center of its activity cell.
    centers = [(round(item["cx"], 2), round(item["cy"], 2)) for item in active]
    entry = (36.0, centers[0][1])
    exit_point = (min(WIDTH - 24.0, grid_end + 28.0), centers[-1][1])
    points = [(round(x, 2), round(y, 2)) for x, y in [entry, *centers, exit_point]]
    cumulative = [0.0]
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        cumulative.append(cumulative[-1] + math.hypot(x2 - x1, y2 - y1))
    total_distance = cumulative[-1] or 1.0
    geometric_points = [distance / total_distance for distance in cumulative]

    first_active_distance = cumulative[1]
    last_active_distance = cumulative[-2]
    active_span = max(1.0, last_active_distance - first_active_distance)
    arrival_times = [
        0.03 + 0.88 * (cumulative[index + 1] - first_active_distance) / active_span
        for index in range(len(active))
    ]
    key_points = ";".join(f"{point:.6f}" for point in [*geometric_points, 1.0])
    key_times = ";".join(f"{point:.6f}" for point in [0.0, *arrival_times, 0.94, 1.0])
    path = "M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in points)
    return path, key_points, key_times, arrival_times


def render_svg(
    weeks: list[dict[str, Any]],
    total: int,
    start: date,
    end: date,
    projects: list[dict[str, Any]],
) -> str:
    project_count = len(projects)
    total_external_prs = sum(project["prs"] for project in projects)
    projects = list(projects[:TOP_PROJECT_LIMIT])
    display_rng = random.Random(
        "|".join(sorted(project["repository"] for project in projects))
    )
    display_rng.shuffle(projects)
    ferris_artwork = ferris_symbol(FERRIS_ASSET)
    left, top = 64.0, 512.0
    cell, gap = 16.0, 5.0
    step = cell + gap
    usable = WIDTH - left - 44
    scale = min(1.0, usable / max(1, len(weeks) * step))
    step *= scale
    cell *= scale
    grid_end = left + max(1, len(weeks) - 1) * step + cell

    counts = [
        day["contributionCount"] for week in weeks for day in week["contributionDays"]
    ]
    peak = max(counts or [1])
    colors = {0: "#161b22", 1: "#0e4429", 2: "#006d32", 3: "#26a641", 4: "#39d353"}

    project_paths = simulate_project_paths(projects)
    project_nodes = []
    project_animations = []
    for index, project in enumerate(projects):
        project_nodes.append(project_motion(project, index, project_paths[index]))
        project_animations.append(project_animation(index, project_paths[index]))
    # Important author declarations outrank CSS animations in the SVG cascade.
    # Pin each existing node to its first collision-safe position for users who
    # request reduced motion, without duplicating graphics or accessibility text.
    reduced_project_rules = "".join(
        f".project-{index}{{transform:translate({fmt(path[0][0])}px,{fmt(path[0][1])}px)!important}}"
        for index, path in enumerate(project_paths)
    )

    day_records: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    month_labels: list[str] = []
    previous_month = None
    last_month_x = -100.0
    for week_index, week in enumerate(weeks):
        x = left + week_index * step
        days = week["contributionDays"]
        for day in days:
            day_date = date.fromisoformat(day["date"])
            if day_date.month != previous_month and x - last_month_x > 48:
                month_labels.append(
                    f'<text class="month" x="{fmt(x)}" y="499">{day_date.strftime("%b").upper()}</text>'
                )
                previous_month = day_date.month
                last_month_x = x
            # Cells are emitted at one-decimal precision. Reuse those exact
            # bounds for Ferris' route instead of their pre-serialization floats.
            rendered_x = round(x, 1)
            rendered_y = round(top + day["weekday"] * step, 1)
            rendered_cell = round(cell, 1)
            record = {
                "x": rendered_x,
                "y": rendered_y,
                "cx": rendered_x + rendered_cell / 2,
                "cy": rendered_y + rendered_cell / 2,
                "count": int(day["contributionCount"]),
                "date": day["date"],
            }
            day_records.append(record)
            if record["count"] > 0:
                active.append(record)

    route, key_points, key_times, arrivals = build_crab_route(active, grid_end)
    cells = []
    active_index = 0
    for record in day_records:
        count = record["count"]
        title = escape(
            f"{record['date']}: {count} contribution{'s' if count != 1 else ''}"
        )
        activity = ""
        if count > 0:
            level = contribution_level(count, peak)
            arrival = arrivals[active_index]
            fade_start = max(0.0, arrival - SWALLOW_FADE_SECONDS / CRAB_DURATION)
            activity = (
                f'<rect class="activity eat-{active_index}" width="{fmt(cell)}" height="{fmt(cell)}" '
                f'rx="2.5" fill="{colors[level]}">'
                f'<animate id="eat-{active_index}" attributeName="opacity" '
                f'values="1;1;0;0;1" keyTimes="0;{fade_start:.6f};{arrival:.6f};.965;1" '
                f'dur="{CRAB_DURATION}s" begin="0s" repeatCount="indefinite" calcMode="linear"/>'
                "</rect>"
            )
            active_index += 1
        cells.append(
            f'<g class="day" transform="translate({fmt(record["x"])} {fmt(record["y"])})">'
            f'<title>{title}</title><rect class="slot" width="{fmt(cell)}" height="{fmt(cell)}" rx="2.5"/>{activity}</g>'
        )

    crab = ""
    if route:
        crab = f'''<g class="ferris" aria-hidden="true">
  <animateMotion id="ferris-motion" dur="{CRAB_DURATION}s" begin="0s" repeatCount="indefinite" calcMode="linear" path="{route}" keyPoints="{key_points}" keyTimes="{key_times}" rotate="0"/>
  <animate attributeName="opacity" values="0;1;1;0;0" keyTimes="0;.012;.94;.955;1" dur="{CRAB_DURATION}s" repeatCount="indefinite"/>
  <g class="ferris-bob"><use href="#ferris-artwork" xlink:href="#ferris-artwork" x="-22.5" y="-15" width="45" height="30"/></g>
</g>'''

    route_markup = f'<path class="route" d="{route}"/>' if route else ""
    project_summary = (
        f"{len(projects)} SHOWN FROM {project_count} PROJECTS · {total_external_prs} MERGED PRS"
        if projects
        else "NO MERGED EXTERNAL PRS FOUND"
    )
    top_project_desc = ", ".join(
        f"{project['repository']} plus {project['commits']}" for project in projects
    )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:serif="http://www.serif.com/" width="100%" height="100%" viewBox="0 0 {WIDTH} {HEIGHT}" preserveAspectRatio="xMidYMid meet" style="display:block;background:#0d1117" role="img" aria-labelledby="title desc">
<title id="title">AIQUBITS open-source orbit and contribution crab</title>
<desc id="desc">Top external projects by commits in merged pull requests: {escape(top_project_desc)}. Hover or focus a project to pause the orbit; activate it to open the GitHub repository. Below, a crab travels between and consumes active days in the GitHub contribution calendar for {escape(USER)}.</desc>
<defs>
  <linearGradient id="background" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0d1117"/><stop offset="1" stop-color="#07130e"/></linearGradient>
  <filter id="green-glow" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="2.6" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <!-- Unmodified paths from rust-lang/rust-artwork mascot/ferris-flat-noshadow.svg (CC0). -->
  {ferris_artwork}
  <style>
    text{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
    .head{{fill:#39ff88;font-size:15px;letter-spacing:1.2px}}.sub{{fill:#7d8590;font-size:11px}}.month{{fill:#7d8590;font-size:10px}}.weekday{{fill:#7d8590;font-size:10px}}
    .field-line{{stroke:#21262d;stroke-width:1}}.project-link,.project{{cursor:pointer}}.project-shell{{fill:#121b19;fill-opacity:.94;stroke:#2a4136;stroke-width:1}}.project:hover .project-shell,.project-link:focus .project-shell{{stroke:#7ee787;filter:url(#green-glow)}}
    .project-name{{fill:#d8dee4;font-size:13px}}.commit-count{{fill:#ffb86b;font-weight:600}}
    {"".join(project_animations)}
    .project-field:hover .project,.project-field:focus-within .project{{animation-play-state:paused}}
    .slot{{fill:#161b22;stroke:#21262d;stroke-width:.7}}.day:hover .slot{{stroke:#7ee787}}.day:hover .activity{{filter:url(#green-glow)}}
    .route{{fill:none;stroke:#f0883e;stroke-width:.7;stroke-dasharray:1 9;stroke-linecap:round;opacity:.075}}
    .ferris-bob{{animation:ferris-bob 1.4s ease-in-out infinite alternate}}@keyframes ferris-bob{{to{{transform:translateY(-1.5px)}}}}
    @media (prefers-reduced-motion:reduce){{.activity{{opacity:1!important}}.project{{animation:none!important}}.ferris,.route{{display:none}}.ferris-bob{{animation:none!important}}{reduced_project_rules}}}
  </style>
</defs>
<rect width="{WIDTH}" height="{HEIGHT}" rx="18" fill="url(#background)" stroke="#30363d"/>

<text x="30" y="35" class="head">/MERGED/ORBIT</text><text x="1170" y="35" text-anchor="end" class="sub">EXTERNAL OPEN SOURCE · COMMIT COUNT IN MERGED PRS · TOP {TOP_PROJECT_LIMIT}</text>
<g class="project-field" aria-label="Top external projects">{"".join(project_nodes)}</g>
<text x="30" y="414" class="sub">RANDOM WALK · HOVER TO PAUSE · CLICK PROJECT TO OPEN · +N = COMMITS</text><text x="1170" y="414" text-anchor="end" class="sub">{project_summary}</text>
<path class="field-line" d="M30 432H1170"/>

<text x="30" y="464" class="head">/CONTRIBUTION/CRAB</text><text x="1170" y="464" text-anchor="end" class="sub">{start.isoformat()} → {end.isoformat()} · {total} CONTRIBUTIONS</text>
<g aria-hidden="true">{"".join(month_labels)}<text class="weekday" x="30" y="{fmt(top + step + cell / 2 + 3)}">MON</text><text class="weekday" x="30" y="{fmt(top + 3 * step + cell / 2 + 3)}">WED</text><text class="weekday" x="30" y="{fmt(top + 5 * step + cell / 2 + 3)}">FRI</text></g>
<g aria-label="GitHub contribution calendar">{"".join(cells)}</g>
{route_markup}{crab}
<g transform="translate(64 712)" aria-hidden="true"><text class="sub" y="10">LESS</text><rect class="slot" x="38" width="11" height="11" rx="2"/><rect x="54" width="11" height="11" rx="2" fill="#0e4429"/><rect x="70" width="11" height="11" rx="2" fill="#006d32"/><rect x="86" width="11" height="11" rx="2" fill="#26a641"/><rect x="102" width="11" height="11" rx="2" fill="#39d353"/><text class="sub" x="121" y="10">MORE</text></g>
<text x="1140" y="722" text-anchor="end" class="sub">ORIGINAL FERRIS ARTWORK · EATS ACTIVE DAYS · {CRAB_DURATION}S LOOP</text>
</svg>'''


def main() -> None:
    if OFFLINE_SVG or OFFLINE_PROJECTS:
        if not (OFFLINE_SVG and OFFLINE_PROJECTS):
            raise SystemExit("OFFLINE_SVG and OFFLINE_PROJECTS must be set together")
        weeks, total, start, end = load_offline_calendar(Path(OFFLINE_SVG))
        projects = load_offline_projects(OFFLINE_PROJECTS)
    else:
        weeks, total, start, end = fetch_calendar(datetime.now(timezone.utc).date())
        projects = aggregate_external_projects(fetch_merged_prs())

    svg = render_svg(weeks, total, start, end, projects)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(svg, encoding="utf-8")
    if README_PATH:
        update_readme_cache_key(README_PATH, svg)
    print(
        f"generated {OUT}: {len(weeks)} weeks, {total} contributions, "
        f"{min(TOP_PROJECT_LIMIT, len(projects))} external projects"
    )


if __name__ == "__main__":
    main()
