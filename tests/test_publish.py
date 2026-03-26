from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fluxa.publish import upsert_run_issue


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


if __name__ == "__main__":
    unittest.main()
