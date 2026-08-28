#!/usr/bin/env python3
"""Regression tests for the profile signal generator."""

from __future__ import annotations

import random
import re
import unittest
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import generate_commit_signal as signal


ROOT = Path(__file__).resolve().parents[2]
SVG_NAMESPACE = {"svg": "http://www.w3.org/2000/svg"}


class CalendarTests(unittest.TestCase):
    def test_calendar_query_stays_inside_one_year_and_uses_returned_dates(self) -> None:
        weeks = [
            {
                "contributionDays": [
                    {
                        "date": "2025-08-29",
                        "weekday": 5,
                        "contributionCount": 1,
                    },
                    {
                        "date": "2026-08-28",
                        "weekday": 5,
                        "contributionCount": 2,
                    },
                ]
            }
        ]
        response = {
            "user": {
                "contributionsCollection": {
                    "contributionCalendar": {
                        "weeks": weeks,
                        "totalContributions": 3,
                    }
                }
            }
        }

        with patch.object(signal, "graphql", return_value=response) as graphql:
            actual_weeks, total, start, end = signal.fetch_calendar(date(2026, 8, 28))

        variables = graphql.call_args.args[1]
        query_start = datetime.fromisoformat(variables["from"].replace("Z", "+00:00"))
        query_end = datetime.fromisoformat(variables["to"].replace("Z", "+00:00"))
        self.assertLess(query_end - query_start, timedelta(days=365))
        self.assertEqual(actual_weeks, weeks)
        self.assertEqual(total, 3)
        self.assertEqual(start, date(2025, 8, 29))
        self.assertEqual(end, date(2026, 8, 28))


class ProjectTests(unittest.TestCase):
    def test_external_projects_are_aggregated_and_ranked_by_commits(self) -> None:
        pull_requests = [
            {
                "repository": {
                    "nameWithOwner": "external/alpha",
                    "owner": {"login": "external"},
                },
                "commits": {"totalCount": 2},
            },
            {
                "repository": {
                    "nameWithOwner": "external/alpha",
                    "owner": {"login": "external"},
                },
                "commits": {"totalCount": 3},
            },
            {
                "repository": {
                    "nameWithOwner": "other/beta",
                    "owner": {"login": "other"},
                },
                "commits": {"totalCount": 4},
            },
            {
                "repository": {
                    "nameWithOwner": "aiqubits/internal",
                    "owner": {"login": "AIQUBITS"},
                },
                "commits": {"totalCount": 99},
            },
        ]

        self.assertEqual(
            signal.aggregate_external_projects(pull_requests),
            [
                {"repository": "external/alpha", "commits": 5, "prs": 2},
                {"repository": "other/beta", "commits": 4, "prs": 1},
            ],
        )

    def test_collision_paths_survive_varied_legal_repository_names(self) -> None:
        for trial in range(40):
            rng = random.Random(trial)
            projects = []
            for index in range(signal.TOP_PROJECT_LIMIT):
                name_length = rng.randint(8, 100)
                name = "".join(
                    rng.choice("abcdefghijklmnopqrstuvwxyz-")
                    for _ in range(name_length)
                )
                projects.append(
                    {
                        "repository": f"org{index}/{name}",
                        "commits": rng.randint(1, 999),
                        "prs": rng.randint(1, 20),
                    }
                )

            paths = signal.simulate_project_paths(projects)
            keyframe_count = len(paths[0])
            self.assertIn(keyframe_count, (1201, 2401))
            self.assertTrue(
                all(
                    len(path) == keyframe_count and path[0] == path[-1]
                    for path in paths
                )
            )

    def test_long_repository_name_and_commit_count_stay_inside_label(self) -> None:
        repository = f"organization/{'long-repository-name-' * 6}"
        project = {"repository": repository, "commits": 12345, "prs": 2}
        node = ET.fromstring(signal.project_motion(project, 0, [(54.0, 62.0)]))

        shell = node.find("rect")
        name = node.find("text[@class='project-name']")
        count = node.find("text[@class='commit-count']")
        self.assertIsNotNone(shell)
        self.assertIsNotNone(name)
        self.assertIsNotNone(count)
        self.assertEqual(name.text, repository)
        self.assertEqual(name.get("lengthAdjust"), "spacingAndGlyphs")
        self.assertEqual(count.get("text-anchor"), "end")
        self.assertLess(float(count.get("x", "inf")), float(shell.get("width", "0")))


class CheckedInAssetTests(unittest.TestCase):
    def test_readme_uses_canonical_links_and_relative_signal(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("# Hi, I'm aiqubits 👋", readme)
        self.assertIn("https://github.com/keycompute/keycompute", readme)
        self.assertIn("https://github.com/aiqubits/rust-agent", readme)
        self.assertIn("(./assets/profile-signal.svg)", readme)
        self.assertNotIn("raw.githubusercontent.com/aiqubits/aiqubits", readme)

    def test_svg_animation_contract(self) -> None:
        svg_path = ROOT / "assets/profile-signal.svg"
        root = ET.parse(svg_path).getroot()
        projects = [
            group
            for group in root.findall(".//svg:g", SVG_NAMESPACE)
            if (group.get("class") or "").startswith("project project-")
        ]
        paths = []
        for project in projects:
            motion = project.find(".//svg:animateTransform", SVG_NAMESPACE)
            self.assertIsNotNone(motion)
            values = motion.get("values", "").split(";")
            self.assertIn(len(values), (1201, 2401))
            self.assertEqual(values[0], values[-1])
            self.assertEqual(motion.get("dur"), "120s")
            paths.append(values)
        self.assertEqual(len(projects), signal.TOP_PROJECT_LIMIT)
        self.assertEqual(len({len(path) for path in paths}), 1)

        eats = sorted(
            (
                animation
                for animation in root.findall(".//svg:animate", SVG_NAMESPACE)
                if (animation.get("id") or "").startswith("eat-")
            ),
            key=lambda animation: int(animation.get("id", "eat-0")[4:]),
        )
        ferris_motion = next(
            animation
            for animation in root.findall(".//svg:animateMotion", SVG_NAMESPACE)
            if animation.get("id") == "ferris-motion"
        )
        arrivals = [
            float(value) for value in ferris_motion.get("keyTimes", "").split(";")
        ][1:-2]
        self.assertGreater(len(eats), 0)
        self.assertEqual(len(eats), len(arrivals))
        for eat, arrival in zip(eats, arrivals):
            eat_arrival = float(eat.get("keyTimes", "").split(";")[2])
            self.assertEqual(eat_arrival, arrival)

        route_points = [
            tuple(map(float, point.split()))
            for point in re.sub(r"^M", "", ferris_motion.get("path", "")).split(" L")
        ][1:-1]
        active_centers = []
        for day in root.findall(".//svg:g", SVG_NAMESPACE):
            if day.get("class") != "day":
                continue
            activity = next(
                (
                    rect
                    for rect in day.findall("svg:rect", SVG_NAMESPACE)
                    if (rect.get("class") or "").startswith("activity ")
                ),
                None,
            )
            if activity is None:
                continue
            x, y = map(
                float,
                re.fullmatch(
                    r"translate\(([^ ]+) ([^)]+)\)", day.get("transform", "")
                ).groups(),
            )
            active_centers.append(
                (
                    x + float(activity.get("width", "0")) / 2,
                    y + float(activity.get("height", "0")) / 2,
                )
            )
        self.assertEqual(len(route_points), len(active_centers))
        for route_point, active_center in zip(route_points, active_centers):
            self.assertAlmostEqual(route_point[0], active_center[0], places=6)
            self.assertAlmostEqual(route_point[1], active_center[1], places=6)

        source = svg_path.read_text(encoding="utf-8")
        self.assertIn(".ferris,.route{display:none}", source)
        self.assertIn(".project-0{transform:translate(", source)
        self.assertIn("px)!important}", source)
        self.assertNotIn("project-static", source)

    def test_ferris_asset_is_the_vendored_rust_artwork(self) -> None:
        digest = sha256(
            (ROOT / "assets/ferris-flat-noshadow.svg").read_bytes()
        ).hexdigest()
        self.assertEqual(
            digest,
            "2bb0ad704e8c7a97bd54a1da2ad9a6feac46048762475888ddad5cc41e3b49b8",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
