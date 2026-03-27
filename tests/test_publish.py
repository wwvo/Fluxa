from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fluxa.models import AppConfig, FeedDefaults, PublishError, RunSummary
from fluxa.publish import (
    _run_cnb,
    _run_cnb_list_json,
    _run_gh,
    publish_summaries,
    publish_summary,
    upsert_run_issue,
)


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


class _FixedPublishDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return datetime(2026, 3, 27, 9, 15, 0, tzinfo=tz)


class PublishIssueLookupTests(unittest.TestCase):
    def test_github_upsert_updates_issue_found_on_later_page(self) -> None:
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
                    "github",
                    "owner/repo",
                    issue_title="Fluxa Digest | 2026-03-27 | run run-123",
                    run_marker="fluxa-run:run-123",
                    issue_body_path=issue_body_path,
                    run_id="run-123",
                )

        self.assertEqual(issue_number, 222)
        self.assertEqual(mock_run_gh_json.call_count, 2)
        mock_run_gh.assert_called_once()
        first_call_args = mock_run_gh_json.call_args_list[0].args[0]
        self.assertIn("--method", first_call_args)
        self.assertIn("GET", first_call_args)

    def test_github_upsert_creates_issue_when_no_existing_match(self) -> None:
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
                    "github",
                    "owner/repo",
                    issue_title="Fluxa Digest | 2026-03-27 | run run-456",
                    run_marker="fluxa-run:run-456",
                    issue_body_path=issue_body_path,
                    run_id="run-456",
                )

        self.assertEqual(issue_number, 333)
        self.assertEqual(mock_run_gh_json.call_count, 2)
        mock_run_gh.assert_not_called()

    def test_cnb_upsert_updates_issue_found_by_run_marker(self) -> None:
        list_payload = [
            {"number": "13", "title": "Fluxa Digest | 2026-03-27 | run cnb-1"}
        ]
        view_payload = {
            "number": "13",
            "title": "Fluxa Digest | 2026-03-27 | run cnb-1",
            "body": "<!-- fluxa-run:cnb-1 -->\n# body",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            issue_body_path = Path(temp_dir) / "issue.md"
            issue_body_path.write_text("# body\n", encoding="utf-8")

            with (
                patch(
                    "fluxa.publish._run_cnb_list_json",
                    side_effect=[list_payload],
                ) as mock_run_cnb_list_json,
                patch(
                    "fluxa.publish._run_cnb_json",
                    side_effect=[view_payload],
                ) as mock_run_cnb_json,
                patch("fluxa.publish._run_cnb", return_value="") as mock_run_cnb,
            ):
                issue_number = upsert_run_issue(
                    "cnb",
                    "owner/repo",
                    issue_title="Fluxa Digest | 2026-03-27 | run cnb-1",
                    run_marker="fluxa-run:cnb-1",
                    issue_body_path=issue_body_path,
                    run_id="cnb-1",
                )

        self.assertEqual(issue_number, 13)
        mock_run_cnb_list_json.assert_called_once()
        mock_run_cnb_json.assert_called_once()
        mock_run_cnb.assert_called_once()
        list_command = mock_run_cnb_list_json.call_args.args[0]
        self.assertIn("--sort=-updated_at", list_command)
        self.assertNotIn("--keyword", list_command)

    def test_cnb_upsert_creates_issue_when_no_existing_match(self) -> None:
        create_payload = {
            "number": "19",
            "title": "Fluxa Digest | 2026-03-27 | run cnb-2",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            issue_body_path = Path(temp_dir) / "issue.md"
            issue_body_path.write_text("# body\n", encoding="utf-8")

            with (
                patch(
                    "fluxa.publish._run_cnb_list_json",
                    side_effect=[[], []],
                ) as mock_run_cnb_list_json,
                patch(
                    "fluxa.publish._run_cnb_json",
                    side_effect=[create_payload],
                ) as mock_run_cnb_json,
            ):
                issue_number = upsert_run_issue(
                    "cnb",
                    "owner/repo",
                    issue_title="Fluxa Digest | 2026-03-27 | run cnb-2",
                    run_marker="fluxa-run:cnb-2",
                    issue_body_path=issue_body_path,
                    run_id="cnb-2",
                )

        self.assertEqual(issue_number, 19)
        self.assertEqual(mock_run_cnb_list_json.call_count, 2)
        mock_run_cnb_json.assert_called_once()

    def test_cnb_upsert_create_passes_labels_and_assignees_from_env(self) -> None:
        create_payload = {
            "number": "21",
            "title": "Fluxa Digest | 2026-03-27 | run cnb-3",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            issue_body_path = Path(temp_dir) / "issue.md"
            issue_body_path.write_text("# body\n", encoding="utf-8")

            with (
                patch.dict(
                    "os.environ",
                    {
                        "CNB_ISSUE_LABELS": "RSS",
                        "CNB_ISSUE_ASSIGNEES": "illegal_name_cnb.by9cbmyhqda,illegal_name_cnb.by9ca6eibfa",
                    },
                    clear=False,
                ),
                patch(
                    "fluxa.publish._run_cnb_list_json",
                    side_effect=[[], []],
                ) as mock_run_cnb_list_json,
                patch(
                    "fluxa.publish._run_cnb_json",
                    side_effect=[create_payload],
                ) as mock_run_cnb_json,
            ):
                issue_number = upsert_run_issue(
                    "cnb",
                    "owner/repo",
                    issue_title="Fluxa Digest | 2026-03-27 | run cnb-3",
                    run_marker="fluxa-run:cnb-3",
                    issue_body_path=issue_body_path,
                    run_id="cnb-3",
                )

        self.assertEqual(issue_number, 21)
        self.assertEqual(mock_run_cnb_list_json.call_count, 2)
        mock_run_cnb_json.assert_called_once()
        command = mock_run_cnb_json.call_args.args[0]
        self.assertIn("--labels", command)
        self.assertIn("RSS", command)
        self.assertIn("--assignees", command)
        self.assertIn(
            "illegal_name_cnb.by9cbmyhqda,illegal_name_cnb.by9ca6eibfa",
            command,
        )

    def test_publish_summary_allows_dry_run_without_repo(self) -> None:
        summary = _build_summary()

        with (
            patch("fluxa.publish.render_run_issue", return_value="# dry-run issue"),
            patch("fluxa.publish.datetime", _FixedPublishDatetime),
        ):
            with patch.dict("os.environ", {}, clear=True):
                result = publish_summary(
                    summary,
                    Path("."),
                    publisher="github",
                    repo=None,
                    timezone_name="Asia/Shanghai",
                    run_id="dry-run-123",
                    display_key=None,
                    dry_run=True,
                )

        self.assertEqual(result.publisher, "github")
        self.assertIsNone(result.repo)
        self.assertIsNone(result.issue_number)
        self.assertEqual(result.issue_title, "Fluxa Digest | 2026-03-27 | 08:00-10:00")

    def test_publish_summary_supports_cnb_dry_run_without_repo(self) -> None:
        summary = _build_summary()

        with (
            patch("fluxa.publish.render_run_issue", return_value="# dry-run issue"),
            patch("fluxa.publish.datetime", _FixedPublishDatetime),
        ):
            with patch.dict("os.environ", {}, clear=True):
                result = publish_summary(
                    summary,
                    Path("."),
                    publisher="cnb",
                    repo=None,
                    timezone_name="Asia/Shanghai",
                    run_id="dry-run-cnb",
                    display_key=None,
                    dry_run=True,
                )

        self.assertEqual(result.publisher, "cnb")
        self.assertIsNone(result.repo)
        self.assertIsNone(result.issue_number)
        self.assertEqual(result.issue_title, "Fluxa Digest | 2026-03-27 | 08:00-10:00")

    def test_publish_summary_supports_explicit_display_key(self) -> None:
        summary = _build_summary()

        with (
            patch("fluxa.publish.render_run_issue", return_value="# dry-run issue"),
            patch("fluxa.publish.datetime", _FixedPublishDatetime),
        ):
            with patch.dict("os.environ", {}, clear=True):
                result = publish_summary(
                    summary,
                    Path("."),
                    publisher="github",
                    repo=None,
                    timezone_name="Asia/Shanghai",
                    run_id="dry-run-display",
                    display_key="Morning Window",
                    dry_run=True,
                )

        self.assertEqual(
            result.issue_title, "Fluxa Digest | 2026-03-27 | Morning Window"
        )

    def test_publish_summaries_supports_multiple_dry_run_publishers(self) -> None:
        summary = _build_summary()

        with (
            patch("fluxa.publish.render_run_issue", return_value="# dry-run issue"),
            patch("fluxa.publish.datetime", _FixedPublishDatetime),
        ):
            with patch.dict(
                "os.environ",
                {
                    "GITHUB_REPOSITORY": "wwvo/Fluxa",
                    "CNB_REPO": "wwvo/Issuo",
                },
                clear=True,
            ):
                results = publish_summaries(
                    summary,
                    Path("."),
                    publishers=("github", "cnb"),
                    repo=None,
                    timezone_name="Asia/Shanghai",
                    run_id="dual-dry-run",
                    display_key=None,
                    dry_run=True,
                )

        self.assertEqual([result.publisher for result in results], ["github", "cnb"])
        self.assertEqual(
            [result.repo for result in results], ["wwvo/Fluxa", "wwvo/Issuo"]
        )
        self.assertTrue(all(result.issue_number is None for result in results))

    def test_publish_summaries_rejects_repo_override_for_multiple_publishers(
        self,
    ) -> None:
        summary = _build_summary()

        with self.assertRaises(PublishError):
            publish_summaries(
                summary,
                Path("."),
                publishers=("github", "cnb"),
                repo="owner/repo",
                timezone_name="Asia/Shanghai",
                run_id="dual-invalid",
                display_key=None,
                dry_run=True,
            )

    def test_publish_summary_raises_publish_error_when_template_missing(self) -> None:
        summary = _build_summary()

        with tempfile.TemporaryDirectory() as temp_dir:
            templates_dir = Path(temp_dir)
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(PublishError):
                    publish_summary(
                        summary,
                        templates_dir,
                        publisher="github",
                        repo=None,
                        timezone_name="Asia/Shanghai",
                        run_id="dry-run-456",
                        display_key=None,
                        dry_run=True,
                    )

    def test_run_gh_raises_publish_error_on_timeout(self) -> None:
        with patch(
            "fluxa.publish.subprocess.run",
            side_effect=subprocess.TimeoutExpired(
                cmd=["gh", "issue", "list"], timeout=60
            ),
        ):
            with self.assertRaises(PublishError):
                _run_gh(["issue", "list"])

    def test_run_cnb_raises_publish_error_on_timeout(self) -> None:
        with patch(
            "fluxa.publish.subprocess.run",
            side_effect=subprocess.TimeoutExpired(
                cmd=["cnb-rs", "issue", "list"], timeout=60
            ),
        ):
            with self.assertRaises(PublishError):
                _run_cnb(["issue", "list"])

    def test_run_cnb_list_json_handles_empty_text_response(self) -> None:
        with patch(
            "fluxa.publish._run_cnb",
            return_value="没有找到符合条件的 Issue\n",
        ):
            issues = _run_cnb_list_json(["issue", "list"])

        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
