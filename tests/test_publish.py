from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fluxa.models import AppConfig, FeedDefaults, PublishError, RunSummary
from fluxa.publish import _run_gh, publish_summary, upsert_run_issue


def _build_summary() -> RunSummary:
    return RunSummary(
        config=AppConfig(
            path=Path("feeds/feeds.yml"),
            defaults=FeedDefaults(),
            feeds=(),
        ),
        bootstrap_mode=False,
        results=[],
    )


class PublishIssueLookupTests(unittest.TestCase):
    def test_upsert_updates_issue_found_on_later_page(self) -> None:
        page_one = [
            {"number": index, "body": f"issue-{index}"} for index in range(1, 101)
        ]
        page_two = [
            {"number": 222, "body": "<!-- fluxa-run:run-123 -->"},
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            issue_body_path = Path(temp_dir) / "issue.md"
            issue_body_path.write_text("# body\n", encoding="utf-8")

            with (
                patch(
                    "fluxa.publish._run_gh_json",
                    side_effect=[page_one, page_two],
                ) as mock_run_gh_json,
                patch("fluxa.publish._run_gh", return_value="") as mock_run_gh,
            ):
                issue_number = upsert_run_issue(
                    "owner/repo",
                    issue_title="Fluxa Digest | 2026-03-27 | run run-123",
                    run_marker="fluxa-run:run-123",
                    issue_body_path=issue_body_path,
                )

        self.assertEqual(issue_number, 222)
        self.assertEqual(mock_run_gh_json.call_count, 2)
        mock_run_gh.assert_called_once()

    def test_upsert_creates_issue_when_no_existing_match(self) -> None:
        first_page = [
            {"number": 1, "body": "other"},
            {"number": 2, "body": "another"},
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            issue_body_path = Path(temp_dir) / "issue.md"
            issue_body_path.write_text("# body\n", encoding="utf-8")

            with (
                patch(
                    "fluxa.publish._run_gh_json",
                    side_effect=[first_page, {"number": 333}],
                ) as mock_run_gh_json,
                patch("fluxa.publish._run_gh", return_value="") as mock_run_gh,
            ):
                issue_number = upsert_run_issue(
                    "owner/repo",
                    issue_title="Fluxa Digest | 2026-03-27 | run run-456",
                    run_marker="fluxa-run:run-456",
                    issue_body_path=issue_body_path,
                )

        self.assertEqual(issue_number, 333)
        self.assertEqual(mock_run_gh_json.call_count, 2)
        mock_run_gh.assert_not_called()

    def test_publish_summary_allows_dry_run_without_repo(self) -> None:
        summary = _build_summary()

        with patch("fluxa.publish.render_run_issue", return_value="# dry-run issue"):
            with patch.dict("os.environ", {}, clear=True):
                result = publish_summary(
                    summary,
                    Path("."),
                    repo=None,
                    timezone_name="Asia/Shanghai",
                    run_id="dry-run-123",
                    dry_run=True,
                )

        self.assertIsNone(result.repo)
        self.assertIsNone(result.issue_number)

    def test_run_gh_raises_publish_error_on_timeout(self) -> None:
        with patch(
            "fluxa.publish.subprocess.run",
            side_effect=subprocess.TimeoutExpired(
                cmd=["gh", "issue", "list"], timeout=60
            ),
        ):
            with self.assertRaises(PublishError):
                _run_gh(["issue", "list"])


if __name__ == "__main__":
    unittest.main()
