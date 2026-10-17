#!/usr/bin/env python3
"""Regression tests for the profile signal generator."""

from __future__ import annotations

import json
import random
import re
import unittest
import urllib.error
from datetime import date, datetime, timedelta
from hashlib import sha256
from http.client import IncompleteRead
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree import ElementTree as ET

import generate_commit_signal as signal


ROOT = Path(__file__).resolve().parents[2]
SVG_NAMESPACE = {"svg": "http://www.w3.org/2000/svg"}


class ApiRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sleep = self.enterContext(patch.object(signal.time, "sleep"))
        self.enterContext(patch.object(signal, "TOKEN", "test-token"))
        self.enterContext(patch.object(signal.sys, "stderr", StringIO()))

    @staticmethod
    def response(body: dict, headers: dict | None = None) -> BytesIO:
        response = BytesIO(json.dumps(body).encode())
        response.headers = headers or {}
        return response

    @staticmethod
    def http_error(code: int, headers: dict | None = None, message: str = "error") -> urllib.error.HTTPError:
        return urllib.error.HTTPError(
            signal.REST_URL, code, message, headers or {},
            BytesIO(json.dumps({"message": message}).encode()),
        )

    def test_rest_and_graphql_retry_server_failure(self) -> None:
        for api in ("rest", "graphql"):
            with self.subTest(api=api):
                self.sleep.reset_mock()
                body = {"items": []} if api == "rest" else {"data": {"ok": True}}
                with patch.object(signal.urllib.request, "urlopen", side_effect=[
                    self.http_error(503), self.response(body),
                ]) as urlopen:
                    if api == "rest":
                        result = signal.rest_get("/search/commits", {"q": "test"})
                        self.assertEqual(result, body)
                    else:
                        self.assertEqual(signal.graphql("query { viewer { login } }", {}), body["data"])
                self.assertEqual(urlopen.call_count, 2)
                first, second = [call.args[0] for call in urlopen.call_args_list]
                self.assertIs(first, second)
                self.sleep.assert_called_once_with(2)

    def test_incomplete_search_retries_the_same_page_without_using_partial_items(self) -> None:
        def item(sha: str) -> dict:
            return {"repository": {"full_name": "external/project"}, "sha": sha}

        with patch.object(signal.urllib.request, "urlopen", side_effect=[
            self.response({
                "total_count": 2, "incomplete_results": True,
                "items": [item("partial")],
            }),
            self.response({
                "total_count": 1, "incomplete_results": False,
                "items": [item("complete")],
            }),
        ]) as urlopen:
            self.assertEqual(
                signal.search_coauthored_commits(),
                [("external/project", "complete")],
            )
        self.assertEqual(urlopen.call_count, 2)
        self.assertIs(urlopen.call_args_list[0].args[0], urlopen.call_args_list[1].args[0])
        self.sleep.assert_called_once_with(2)

    def test_incomplete_search_stops_after_retry_budget(self) -> None:
        responses = [self.response({
            "total_count": 1, "incomplete_results": True, "items": [],
        }) for _ in range(signal.API_MAX_ATTEMPTS)]
        with patch.object(signal.urllib.request, "urlopen", side_effect=responses) as urlopen:
            with self.assertRaisesRegex(SystemExit, "failed after 4 attempts: incomplete GitHub search results"):
                signal.search_coauthored_commits()
        self.assertEqual(urlopen.call_count, 4)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [2, 4, 8])

    def test_network_failures_back_off_and_stop_at_attempt_limit(self) -> None:
        with patch.object(signal.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")) as urlopen:
            with self.assertRaisesRegex(SystemExit, "failed after 4 attempts"):
                signal.rest_get("/search/commits")
        self.assertEqual(urlopen.call_count, 4)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [2, 4, 8])

    def test_timeouts_and_truncated_responses_can_recover(self) -> None:
        with patch.object(signal.urllib.request, "urlopen", side_effect=[
            TimeoutError("timed out"), IncompleteRead(b"partial"),
            self.response({"items": []}),
        ]) as urlopen:
            self.assertEqual(signal.rest_get("/search/commits"), {"items": []})
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [2, 4])

    def test_permanent_http_errors_fail_without_retry(self) -> None:
        for code in (401, 403, 404, 422):
            with self.subTest(code=code):
                with patch.object(signal.urllib.request, "urlopen", side_effect=self.http_error(code)) as urlopen:
                    with self.assertRaises(urllib.error.HTTPError):
                        signal.rest_get("/search/commits")
                self.assertEqual(urlopen.call_count, 1)
        self.sleep.assert_not_called()

    def test_rate_limits_honor_headers_or_wait_one_minute(self) -> None:
        cases = [
            (429, {"Retry-After": "9"}, "error", 9),
            (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "105"}, "error", 6),
            (403, {}, "You have exceeded a secondary rate limit.", 60),
            (503, {"Retry-After": "Thu, 01 Jan 1970 00:02:40 GMT"}, "error", 60),
        ]
        for code, headers, message, delay in cases:
            with self.subTest(code=code, headers=headers):
                self.sleep.reset_mock()
                with patch.object(signal.time, "time", return_value=100):
                    with patch.object(signal.urllib.request, "urlopen", side_effect=[
                        self.http_error(code, headers, message), self.response({"items": []}),
                    ]):
                        self.assertEqual(signal.rest_get("/search/commits"), {"items": []})
                self.sleep.assert_called_once_with(delay)

    def test_http_200_graphql_rate_limit_retries_instead_of_using_partial_data(self) -> None:
        with patch.object(signal.urllib.request, "urlopen", side_effect=[
            self.response({
                "data": {"partial": True},
                "errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}],
            }, {"Retry-After": "12"}),
            self.response({"data": {"ok": True}}),
        ]) as urlopen:
            self.assertEqual(signal.graphql("query { viewer { login } }", {}), {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
        self.sleep.assert_called_once_with(12)

    def test_graphql_rate_limit_message_without_type_is_retried(self) -> None:
        body = {"errors": [{"message": "You have exceeded a secondary rate limit."}]}
        for status in (200, 403):
            with self.subTest(status=status):
                self.sleep.reset_mock()
                failure = self.response(body) if status == 200 else urllib.error.HTTPError(
                    signal.GRAPHQL_URL, 403, "Forbidden", {},
                    BytesIO(json.dumps(body).encode()),
                )
                with patch.object(signal.urllib.request, "urlopen", side_effect=[
                    failure, self.response({"data": {"ok": True}}),
                ]) as urlopen:
                    self.assertEqual(signal.graphql("query { viewer { login } }", {}), {"ok": True})
                self.assertEqual(urlopen.call_count, 2)
                self.sleep.assert_called_once_with(60)

    def test_graphql_internal_error_retries_but_validation_errors_do_not(self) -> None:
        with patch.object(signal.urllib.request, "urlopen", side_effect=[
            self.response({"errors": [{"type": "INTERNAL", "message": "temporary"}]}),
            self.response({"data": {"ok": True}}),
        ]):
            self.assertEqual(signal.graphql("query { viewer { login } }", {}), {"ok": True})
        self.sleep.assert_called_once_with(2)
        self.sleep.reset_mock()
        with patch.object(signal.urllib.request, "urlopen", return_value=self.response({
            "errors": [{"message": "Field does not exist"}],
        })) as urlopen:
            with self.assertRaisesRegex(SystemExit, "Field does not exist"):
                signal.graphql("query { missingField }", {})
        self.assertEqual(urlopen.call_count, 1)
        self.sleep.assert_not_called()

    def test_long_rate_limit_wait_fails_without_retrying_early(self) -> None:
        with patch.object(signal.urllib.request, "urlopen", side_effect=self.http_error(429, {"Retry-After": "3600"})) as urlopen:
            with self.assertRaisesRegex(SystemExit, "exceeding the 300s retry budget"):
                signal.rest_get("/search/commits")
        self.assertEqual(urlopen.call_count, 1)
        self.sleep.assert_not_called()


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
            {
                "repository": {
                    "nameWithOwner": "keycompute/keycompute",
                    "owner": {"login": "KEYCOMPUTE"},
                },
                "commits": {"totalCount": 100},
            },
            {
                "repository": {
                    "nameWithOwner": "OneBlockPlus/hidden",
                    "owner": {"login": "OneBlockPlus"},
                },
                "commits": {"totalCount": 98},
            },
            {
                "repository": {
                    "nameWithOwner": "conflux-fans/hidden",
                    "owner": {"login": "conflux-fans"},
                },
                "commits": {"totalCount": 97},
            },
            {
                "repository": {
                    "nameWithOwner": "IntensiveCoLearning/hidden",
                    "owner": {"login": "IntensiveCoLearning"},
                },
                "commits": {"totalCount": 96},
            },
        ]

        self.assertEqual(
            signal.aggregate_external_projects(pull_requests),
            [
                {"repository": "external/alpha", "commits": 5, "prs": 2},
                {"repository": "other/beta", "commits": 4, "prs": 1},
            ],
        )

    def test_excluded_project_owners_are_filtered_case_insensitively(self) -> None:
        projects = [
            {"repository": "external/alpha", "commits": 50, "prs": 8},
            {"repository": "OneBlockPlus/hackathon", "commits": 99, "prs": 9},
            {"repository": "CONFLUX-FANS/bounties", "commits": 98, "prs": 9},
            {"repository": "ETHPANDA-ORG/events", "commits": 96, "prs": 9},
            {"repository": "MOVE-CN/letsmove", "commits": 95, "prs": 9},
            {"repository": "OPENBUILDXYZ/bootcamp", "commits": 94, "prs": 9},
            {"repository": "KEYCOMPUTE/keycompute", "commits": 93, "prs": 9},
            {"repository": "AIQUBITS/AINS", "commits": 92, "prs": 9},
            {
                "repository": "intensivecolearning/fellowship",
                "commits": 97,
                "prs": 9,
            },
        ]

        self.assertEqual(
            signal.filter_excluded_projects(projects),
            [{"repository": "external/alpha", "commits": 50, "prs": 8}],
        )

    def test_offline_projects_filter_excluded_owners(self) -> None:
        for owner in ("OneBlockPlus", "KEYCOMPUTE", "AIQUBITS"):
            with self.subTest(owner=owner):
                payload = json.dumps([
                    {"repository": "external/alpha", "commits": 2},
                    {"repository": f"{owner}/hidden", "commits": 99},
                ])
                self.assertEqual(
                    signal.load_offline_projects(payload),
                    [{"repository": "external/alpha", "commits": 2, "prs": 1}],
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
            self.assertGreater(signal.PROJECT_Y_MAX, 400)
            for project, path in zip(projects, paths):
                width = signal.project_width(project)
                for x, y in path:
                    self.assertGreaterEqual(x, signal.PROJECT_X_MIN)
                    self.assertLessEqual(x + width, signal.PROJECT_X_MAX)
                    self.assertGreaterEqual(y, signal.PROJECT_Y_MIN)
                    self.assertLessEqual(y + 34.0, signal.PROJECT_Y_MAX)

    def test_long_repository_name_and_commit_count_stay_inside_label(self) -> None:
        repository = f"organization/{'long-repository-name-' * 6}"
        project = {"repository": repository, "commits": 12345, "prs": 2}
        node = ET.fromstring(
            '<svg xmlns:xlink="http://www.w3.org/1999/xlink">'
            f"{signal.project_motion(project, 0, [(54.0, 62.0)])}"
            "</svg>"
        )

        shell = node.find(".//rect")
        name = node.find(".//text[@class='project-name']")
        count = node.find(".//text[@class='commit-count']")
        self.assertIsNotNone(shell)
        self.assertIsNotNone(name)
        self.assertIsNotNone(count)
        self.assertEqual(name.text, repository)
        self.assertEqual(name.get("lengthAdjust"), "spacingAndGlyphs")
        self.assertEqual(count.get("text-anchor"), "end")
        self.assertLess(float(count.get("x", "inf")), float(shell.get("width", "0")))


class CoauthorTests(unittest.TestCase):
    @staticmethod
    def repository(name: str = "external/project") -> dict:
        return {"nameWithOwner": name, "owner": {"login": name.split("/")[0]}}

    @staticmethod
    def connection(nodes: list, has_next: bool = False, cursor: str | None = None) -> dict:
        return {
            "nodes": nodes,
            "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        }

    def test_search_paginates_deduplicates_and_excludes_hidden_owners(self) -> None:
        def item(repository: str, sha: str) -> dict:
            return {"repository": {"full_name": repository}, "sha": sha}

        responses = [
            {"total_count": 101, "incomplete_results": False, "items": [
                item("external/project", "first"),
                item("AIQUBITS/internal", "hidden"),
                item("MOVE-CN/course", "hidden"),
                item("KEYCOMPUTE/keycompute", "hidden"),
            ]},
            {"total_count": 101, "incomplete_results": False, "items": [
                item("external/project", "first"),
                item("external/project", "second"),
            ]},
        ]
        with patch.object(signal, "rest_get", side_effect=responses) as rest:
            commits = signal.search_coauthored_commits()

        self.assertEqual(commits, [("external/project", "first"), ("external/project", "second")])
        self.assertEqual([call.args[1]["page"] for call in rest.call_args_list], [1, 2])
        self.assertEqual(rest.call_args.args[1]["q"], '"Co-authored-by: aiqubits"')

    def test_search_rejects_results_above_github_limit(self) -> None:
        with patch.object(signal, "rest_get", return_value={
            "total_count": 1001, "incomplete_results": False, "items": [],
        }) as rest:
            with self.assertRaisesRegex(SystemExit, "1000-result limit"):
                signal.search_coauthored_commits()
        rest.assert_called_once()

    def test_verification_requires_account_and_merged_pr_in_same_repository(self) -> None:
        merged = {"id": "merged-pr", "merged": True, "repository": self.repository()}
        open_pr = {"id": "open-pr", "merged": False, "repository": self.repository()}
        upstream = {"id": "upstream-pr", "merged": True, "repository": self.repository("upstream/project")}
        for login, prs, expected in [
            ("AIQUBITS", [merged, open_pr, upstream], ["merged-pr"]),
            ("another-user", [merged], []),
            (None, [merged], []),
            ("aiqubits", [open_pr, upstream], []),
        ]:
            with self.subTest(login=login, prs=prs):
                response = {"repository": {
                    **self.repository(),
                    "object": {
                        "authors": self.connection([{"user": {"login": login}}] if login else [{"user": None}]),
                        "associatedPullRequests": self.connection(prs),
                    },
                }}
                with patch.object(signal, "search_coauthored_commits", return_value=[("external/project", "sha")]):
                    with patch.object(signal, "graphql", return_value=response):
                        commits = signal.fetch_coauthored_commits()
                self.assertEqual([pr for commit in commits for pr in commit["pull_requests"]], expected)

    def test_verification_paginates_authors_and_associated_prs(self) -> None:
        responses = []
        for login, pr_id, has_next, cursor in [
            ("maintainer", "first-pr", True, "next"),
            ("aiqubits", "second-pr", False, None),
        ]:
            responses.append({"repository": {
                **self.repository(),
                "object": {
                    "authors": self.connection([{"user": {"login": login}}], has_next, cursor),
                    "associatedPullRequests": self.connection([
                        {"id": pr_id, "merged": True, "repository": self.repository()},
                    ], has_next, cursor),
                },
            }})
        with patch.object(signal, "search_coauthored_commits", return_value=[("external/project", "sha")]):
            with patch.object(signal, "graphql", side_effect=responses) as graphql:
                commits = signal.fetch_coauthored_commits()
        self.assertEqual(commits[0]["pull_requests"], ["first-pr", "second-pr"])
        self.assertEqual(graphql.call_args.args[1]["authorsAfter"], "next")
        self.assertEqual(graphql.call_args.args[1]["prsAfter"], "next")

    def test_counts_normal_and_coauthored_commits_without_double_counting(self) -> None:
        repository = self.repository()
        authored = [{
            "id": "authored-pr", "repository": repository,
            "commits": {"totalCount": 3}, "mergeCommit": {"oid": "squash"},
        }]
        coauthored = [
            {"repository": repository, "oid": "original", "pull_requests": ["authored-pr"]},
            {"repository": repository, "oid": "squash", "pull_requests": ["authored-pr"]},
            {"repository": repository, "oid": "extra", "pull_requests": ["other-pr"]},
            {"repository": repository, "oid": "extra", "pull_requests": ["other-pr"]},
            {"repository": repository, "oid": "extra-2", "pull_requests": ["other-pr"]},
            {"repository": self.repository("OneBlockPlus/hidden"), "oid": "hidden", "pull_requests": ["hidden-pr"]},
            {"repository": self.repository("aiqubits/internal"), "oid": "internal", "pull_requests": ["internal-pr"]},
            {"repository": self.repository("keycompute/keycompute"), "oid": "own-org", "pull_requests": ["own-org-pr"]},
            {"repository": repository, "oid": "unmerged", "pull_requests": []},
        ]
        self.assertEqual(signal.aggregate_external_projects(authored, coauthored), [
            {"repository": "external/project", "commits": 5, "prs": 2},
        ])

    def test_shared_authored_commit_counts_new_prs_without_counting_commit_again(self) -> None:
        repository = self.repository()
        authored = [{
            "id": "authored-pr", "repository": repository,
            "commits": {"totalCount": 3}, "mergeCommit": {"oid": "squash"},
        }]
        for oid in ("original", "squash"):
            for repeat in (False, True):
                with self.subTest(oid=oid, repeat=repeat):
                    coauthored = [{
                        "repository": repository, "oid": oid,
                        "pull_requests": ["authored-pr", "other-pr"],
                    }]
                    if repeat:
                        coauthored.append({
                            "repository": repository, "oid": oid,
                            "pull_requests": ["other-pr"],
                        })
                    self.assertEqual(
                        signal.aggregate_external_projects(authored, coauthored),
                        [{"repository": "external/project", "commits": 3, "prs": 2}],
                    )

    def test_coauthored_only_project_uses_normal_display(self) -> None:
        projects = signal.aggregate_external_projects([], [{
            "repository": self.repository("agent-substrate/substrate"),
            "oid": "46a602d", "pull_requests": ["2573"],
        }])
        self.assertEqual(projects, [{"repository": "agent-substrate/substrate", "commits": 1, "prs": 1}])
        rows = signal.readme_project_rows(projects)
        self.assertIn("`+1` merged-PR commit", rows)
        self.assertNotIn("co-author", rows)


class ReadmeCacheTests(unittest.TestCase):
    def test_readme_tracks_svg_content_and_projects(self) -> None:
        with TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                '<a href="https://cdn.jsdelivr.net/gh/aiqubits/aiqubits@main/'
                'assets/profile-signal.svg?v=old">\n'
                '  <img src="./assets/profile-signal.svg?v=old" alt="signal">\n'
                "</a>\n"
                "<!-- profile-signal-projects:start -->\n"
                "- old project\n"
                "<!-- profile-signal-projects:end -->\n",
                encoding="utf-8",
            )
            svg = "<svg>new content</svg>"
            projects = [
                {"repository": "external/alpha", "commits": 1, "prs": 1},
                {"repository": "other/beta", "commits": 2, "prs": 1},
            ]

            signal.update_readme(readme, svg, projects)

            expected = sha256(svg.encode()).hexdigest()[:12]
            updated = readme.read_text(encoding="utf-8")
            self.assertIn(
                f'href="{signal.standalone_svg_url(expected)}"', updated
            )
            self.assertIn(f'profile-signal.svg?v={expected}', updated)
            self.assertNotIn("?v=old", updated)
            self.assertNotIn("old project", updated)
            self.assertIn(
                "[external/alpha](https://github.com/external/alpha)", updated
            )
            self.assertIn("`+1` merged-PR commit", updated)
            self.assertIn("`+2` merged-PR commits", updated)

    def test_standalone_asset_retains_only_requested_previous_version(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "profile-signal.svg"
            previous = Path(directory) / "profile-signal-000000000000.svg"
            stale = Path(directory) / "profile-signal-111111111111.svg"
            unrelated = Path(directory) / "profile-signal-preview.svg"
            previous.write_text("old", encoding="utf-8")
            stale.write_text("stale", encoding="utf-8")
            unrelated.write_text("keep", encoding="utf-8")
            svg = "<svg>new content</svg>"

            standalone = signal.write_standalone_asset(
                output, svg, retain_cache_keys=["000000000000"]
            )

            expected = sha256(svg.encode()).hexdigest()[:12]
            self.assertEqual(standalone.name, f"profile-signal-{expected}.svg")
            self.assertEqual(standalone.read_text(encoding="utf-8"), svg)
            self.assertTrue(previous.exists())
            self.assertFalse(stale.exists())
            self.assertTrue(unrelated.exists())

    def test_previous_standalone_key_is_read_from_readme(self) -> None:
        with TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                f'<a href="{signal.standalone_svg_url("123456789abc")}">signal</a>',
                encoding="utf-8",
            )

            self.assertEqual(
                signal.readme_standalone_cache_key(readme), "123456789abc"
            )


class CheckedInAssetTests(unittest.TestCase):
    def test_readme_uses_canonical_links_and_relative_signal(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("# Hi, I'm aiqubits 👋", readme)
        self.assertIn("https://github.com/keycompute/keycompute", readme)
        self.assertIn("https://github.com/aiqubits/rust-agent", readme)
        self.assertNotIn("Open-source contributions", readme)
        summary = (
            "<summary>I am also a contributor to the following open-source "
            "projects.</summary>"
        )
        self.assertEqual(readme.count(summary), 1)
        self.assertNotIn("Meanwhile,", readme)
        self.assertNotIn("Open-source projects shown in the animation", readme)
        embedded_keys = re.findall(
            r"profile-signal\.svg\?v=([0-9a-f]{12})", readme
        )
        standalone_keys = re.findall(
            re.escape(signal.STANDALONE_SVG_URL_PREFIX) + r"([0-9a-f]{12})\.svg",
            readme,
        )
        self.assertEqual(len(embedded_keys), 1)
        self.assertEqual(len(standalone_keys), 1)
        self.assertNotIn("raw.githubusercontent.com", readme)
        self.assertNotIn('target="_blank"', readme)
        self.assertNotIn('rel="noopener noreferrer"', readme)
        self.assertNotIn('class="profile-signal-', readme)
        self.assertIn('<p align="center">', readme)
        self.assertIn('<img src="./assets/', readme)
        self.assertIn(
            'alt="Diagram showing external projects ranked by all-time '
            'merged-PR commits above a contribution calendar traversed by Ferris."',
            readme,
        )
        self.assertIn("<details>", readme)
        self.assertLess(readme.index("<details>"), readme.index('<p align="center">'))
        self.assertEqual(readme.count("<!-- profile-signal-projects:start -->"), 1)
        self.assertEqual(readme.count("<!-- profile-signal-projects:end -->"), 1)
        readme_projects = re.findall(
            r"- \[(?P<repository>[^]]+)\]"
            r"\(https://github\.com/(?P=repository)\) — `\+(?P<commits>\d+)` "
            r"merged-PR commits?",
            readme,
        )
        self.assertTrue(
            all(
                repository.split("/", 1)[0].casefold()
                not in signal.EXCLUDED_PROJECT_OWNERS
                for repository, _ in readme_projects
            )
        )
        svg_root = ET.parse(ROOT / "assets/profile-signal.svg").getroot()
        svg_projects = []
        for group in svg_root.findall(".//svg:g", SVG_NAMESPACE):
            if not (group.get("class") or "").startswith("project project-"):
                continue
            repository = group.find(".//svg:text[@class='project-name']", SVG_NAMESPACE)
            commits = group.find(".//svg:text[@class='commit-count']", SVG_NAMESPACE)
            self.assertIsNotNone(repository)
            self.assertIsNotNone(commits)
            svg_projects.append((repository.text, commits.text.removeprefix("+")))
        self.assertEqual(readme_projects, svg_projects)
        expected_key = sha256(
            (ROOT / "assets/profile-signal.svg").read_bytes()
        ).hexdigest()[:12]
        self.assertEqual(
            set(embedded_keys + standalone_keys),
            {expected_key},
        )
        standalone = ROOT / "assets" / f"profile-signal-{expected_key}.svg"
        self.assertTrue(standalone.is_file())
        self.assertLessEqual(
            len(list((ROOT / "assets").glob("profile-signal-????????????.svg"))),
            2,
        )
        self.assertEqual(
            standalone.read_bytes(),
            (ROOT / "assets/profile-signal.svg").read_bytes(),
        )

    def test_workflow_commits_content_addressed_asset(self) -> None:
        workflow = (ROOT / ".github/workflows/commit-signal.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("'assets/profile-signal-*.svg'", workflow)
        self.assertIn(
            "git add -A -- assets/profile-signal.svg "
            "'assets/profile-signal-*.svg' README.md",
            workflow,
        )

    def test_svg_animation_contract(self) -> None:
        svg_path = ROOT / "assets/profile-signal.svg"
        root = ET.parse(svg_path).getroot()
        self.assertEqual(root.get("width"), "100%")
        self.assertEqual(root.get("height"), "100%")
        self.assertEqual(root.get("preserveAspectRatio"), "xMidYMid meet")
        projects = [
            group
            for group in root.findall(".//svg:g", SVG_NAMESPACE)
            if (group.get("class") or "").startswith("project project-")
        ]
        self.assertLessEqual(len(projects), signal.TOP_PROJECT_LIMIT)
        expected_project_count = len(projects)

        links = root.findall(".//svg:a", SVG_NAMESPACE)
        self.assertEqual(len(links), expected_project_count)
        for link in links:
            repository = link.find(".//svg:text[@class='project-name']", SVG_NAMESPACE)
            self.assertIsNotNone(repository)
            expected_url = f"https://github.com/{repository.text}"
            self.assertEqual(link.get("href"), expected_url)
            self.assertEqual(
                link.get("{http://www.w3.org/1999/xlink}href"), expected_url
            )
            self.assertEqual(link.get("target"), "_blank")

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
        self.assertEqual(source.count("@keyframes project-motion-"), expected_project_count)
        self.assertIn(
            ".project-field:hover .project{animation-play-state:paused}", source
        )
        self.assertIn(
            ".project-field.resume-after-click .project{animation-play-state:running}",
            source,
        )
        self.assertIn('field.classList.add("resume-after-click")', source)
        self.assertIn('field.classList.remove("resume-after-click")', source)
        self.assertNotIn("focus-within", source)
        self.assertIn("ALL-TIME COMMITS IN MERGED PRS · TOP 10", source)
        self.assertNotIn("GITHUB ACTIVE IN LAST 2Y", source)
        self.assertNotIn("RANDOM WALK · HOVER TO PAUSE", source)
        self.assertNotIn("SHOWN FROM", source)
        self.assertNotIn("<animateTransform", source)
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
