from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from fluxa.models import AppConfig, FeedDefaults, PublishError, PublishState, RunSummary
from fluxa.publish import (
    _request_cnb_json,
    _run_gh,
    publish_summaries,
    upsert_run_issue,
)
from fluxa.state import load_publish_state


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


class PublishIssueTests(unittest.TestCase):
    def test_github_upsert_updates_issue_from_publish_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            issue_body_path = Path(temp_dir) / "issue.md"
            issue_body_path.write_text("# body\n", encoding="utf-8")

            with patch("fluxa.publish._run_gh", return_value="") as mock_run_gh:
                issue_number = upsert_run_issue(
                    "github",
                    "owner/repo",
                    issue_title="Fluxa Digest | 2026-03-27 | run run-123",
                    issue_body_path=issue_body_path,
                    existing_issue_number=222,
                )

        self.assertEqual(issue_number, 222)
        mock_run_gh.assert_called_once()
        command = mock_run_gh.call_args.args[0]
        self.assertEqual(
            command[:4], ["api", "repos/owner/repo/issues/222", "--method", "PATCH"]
        )

    def test_github_upsert_creates_issue_when_publish_state_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            issue_body_path = Path(temp_dir) / "issue.md"
            issue_body_path.write_text("# body\n", encoding="utf-8")

            with (
                patch(
                    "fluxa.publish._run_gh_json", return_value={"number": 333}
                ) as mock_run_gh_json,
                patch("fluxa.publish._run_gh", return_value="") as mock_run_gh,
            ):
                issue_number = upsert_run_issue(
                    "github",
                    "owner/repo",
                    issue_title="Fluxa Digest | 2026-03-27 | run run-456",
                    issue_body_path=issue_body_path,
                    existing_issue_number=None,
                )

        self.assertEqual(issue_number, 333)
        mock_run_gh_json.assert_called_once()
        mock_run_gh.assert_not_called()

    def test_cnb_upsert_updates_issue_from_publish_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            issue_body_path = Path(temp_dir) / "issue.md"
            issue_body_path.write_text("# body\n", encoding="utf-8")

            with (
                patch.dict("os.environ", {"CNB_TOKEN": "token-123"}, clear=False),
                patch(
                    "fluxa.publish._request_cnb_empty", return_value=None
                ) as mock_request_cnb_empty,
            ):
                issue_number = upsert_run_issue(
                    "cnb",
                    "owner/repo",
                    issue_title="Fluxa Digest | 2026-03-27 | run cnb-1",
                    issue_body_path=issue_body_path,
                    existing_issue_number=13,
                )

        self.assertEqual(issue_number, 13)
        mock_request_cnb_empty.assert_called_once()
        self.assertEqual(
            mock_request_cnb_empty.call_args.args[:2], ("PATCH", "owner/repo")
        )
        self.assertEqual(mock_request_cnb_empty.call_args.kwargs["issue_number"], 13)

    def test_cnb_upsert_creates_issue_when_publish_state_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            issue_body_path = Path(temp_dir) / "issue.md"
            issue_body_path.write_text("# body\n", encoding="utf-8")

            with (
                patch.dict("os.environ", {"CNB_TOKEN": "token-123"}, clear=False),
                patch(
                    "fluxa.publish._request_cnb_json", return_value={"number": "19"}
                ) as mock_request_cnb_json,
            ):
                issue_number = upsert_run_issue(
                    "cnb",
                    "owner/repo",
                    issue_title="Fluxa Digest | 2026-03-27 | run cnb-2",
                    issue_body_path=issue_body_path,
                    existing_issue_number=None,
                )

        self.assertEqual(issue_number, 19)
        mock_request_cnb_json.assert_called_once()

    def test_cnb_upsert_create_passes_labels_and_assignees_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            issue_body_path = Path(temp_dir) / "issue.md"
            issue_body_path.write_text("# body\n", encoding="utf-8")

            with (
                patch.dict(
                    "os.environ",
                    {
                        "CNB_TOKEN": "token-123",
                        "CNB_ISSUE_LABELS": "RSS",
                        "CNB_ISSUE_ASSIGNEES": "illegal_name_cnb.by9cbmyhqda,illegal_name_cnb.by9ca6eibfa",
                    },
                    clear=False,
                ),
                patch(
                    "fluxa.publish._request_cnb_json",
                    return_value={"number": "21"},
                ) as mock_request_cnb_json,
            ):
                issue_number = upsert_run_issue(
                    "cnb",
                    "owner/repo",
                    issue_title="Fluxa Digest | 2026-03-27 | run cnb-3",
                    issue_body_path=issue_body_path,
                    existing_issue_number=None,
                )

        self.assertEqual(issue_number, 21)
        payload = mock_request_cnb_json.call_args.kwargs["payload"]
        self.assertEqual(payload["labels"], ["RSS"])
        self.assertEqual(
            payload["assignees"],
            [
                "illegal_name_cnb.by9cbmyhqda",
                "illegal_name_cnb.by9ca6eibfa",
            ],
        )

    def test_request_cnb_json_sets_user_agent_and_auth_header(self) -> None:
        captured: dict[str, object] = {}

        class _FakeResponse:
            status_code = 200
            text = ""
            request = SimpleNamespace(url="https://api.cnb.cool/wwvo/Issuo/-/issues")

            @staticmethod
            def json() -> object:
                return {"number": "21"}

        class _FakeClient:
            def __init__(self, *, timeout: float, headers: dict[str, str]) -> None:
                captured["timeout"] = timeout
                captured["headers"] = headers

            def __enter__(self) -> "_FakeClient":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def request(
                self, method: str, url: str, *, json: dict[str, object]
            ) -> _FakeResponse:
                captured["method"] = method
                captured["url"] = url
                captured["json"] = json
                return _FakeResponse()

        with (
            patch.dict("os.environ", {"CNB_TOKEN": "token-123"}, clear=True),
            patch("fluxa.publish.httpx.Client", _FakeClient),
        ):
            payload = _request_cnb_json(
                "POST",
                "wwvo/Issuo",
                payload={"title": "hello"},
            )

        self.assertEqual(payload["number"], "21")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(
            captured["url"],
            "https://api.cnb.cool/wwvo/Issuo/-/issues",
        )
        headers = captured["headers"]
        self.assertEqual(headers["Authorization"], "Bearer token-123")
        self.assertTrue(headers["User-Agent"].startswith("Fluxa/"))
        self.assertEqual(headers["Accept"], "application/vnd.cnb.api+json")

    def test_request_cnb_json_raises_publish_error_on_timeout(self) -> None:
        class _TimeoutClient:
            def __init__(self, *, timeout: float, headers: dict[str, str]) -> None:
                self.timeout = timeout
                self.headers = headers

            def __enter__(self) -> "_TimeoutClient":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def request(
                self, method: str, url: str, *, json: dict[str, object]
            ) -> object:
                raise httpx.TimeoutException("boom")

        with (
            patch.dict("os.environ", {"CNB_TOKEN": "token-123"}, clear=True),
            patch("fluxa.publish.httpx.Client", _TimeoutClient),
        ):
            with self.assertRaises(PublishError):
                _request_cnb_json(
                    "POST",
                    "wwvo/Issuo",
                    payload={"title": "hello"},
                )

    def test_request_cnb_json_raises_publish_error_when_token_missing(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(PublishError):
                _request_cnb_json(
                    "POST",
                    "wwvo/Issuo",
                    payload={"title": "hello"},
                )

    def test_publish_summaries_persists_partial_success_to_publish_state(self) -> None:
        summary = _build_summary()

        with tempfile.TemporaryDirectory() as temp_dir:
            publish_state_path = Path(temp_dir) / "publish-state.json"
            publish_state = PublishState()

            with (
                patch("fluxa.publish.render_run_issue", return_value="# issue body"),
                patch("fluxa.publish.datetime", _FixedPublishDatetime),
                patch("fluxa.publish._run_gh_json", return_value={"number": 10}),
                patch(
                    "fluxa.publish._request_cnb_json",
                    side_effect=PublishError("cnb publish failed"),
                ),
                patch.dict(
                    "os.environ",
                    {
                        "GITHUB_REPOSITORY": "wwvo/Fluxa",
                        "CNB_REPO": "wwvo/Issuo",
                        "CNB_TOKEN": "token-123",
                    },
                    clear=True,
                ),
            ):
                with self.assertRaises(PublishError):
                    publish_summaries(
                        summary,
                        Path("."),
                        publishers=("github", "cnb"),
                        repo=None,
                        timezone_name="Asia/Shanghai",
                        run_id="23626373259",
                        display_key=None,
                        dry_run=False,
                        publish_state=publish_state,
                        publish_state_path=publish_state_path,
                    )

            loaded_publish_state = load_publish_state(publish_state_path)

        self.assertEqual(
            loaded_publish_state.latest_window_key, "2026-03-27|08:00-10:00"
        )
        self.assertEqual(
            loaded_publish_state.get_issue_number("2026-03-27|08:00-10:00", "github"),
            10,
        )
        self.assertIsNone(
            loaded_publish_state.get_issue_number("2026-03-27|08:00-10:00", "cnb")
        )

    def test_publish_summary_allows_dry_run_without_repo(self) -> None:
        summary = _build_summary()

        with (
            patch("fluxa.publish.render_run_issue", return_value="# dry-run issue"),
            patch("fluxa.publish.datetime", _FixedPublishDatetime),
        ):
            with patch.dict("os.environ", {}, clear=True):
                result = publish_summaries(
                    summary,
                    Path("."),
                    publishers=("github",),
                    repo=None,
                    timezone_name="Asia/Shanghai",
                    run_id="dry-run-123",
                    display_key=None,
                    dry_run=True,
                )[0]

        self.assertEqual(result.publisher, "github")
        self.assertIsNone(result.repo)
        self.assertIsNone(result.issue_number)
        self.assertEqual(
            result.issue_title, "Fluxa Digest | 2026-03-27 | run dry-run-123"
        )

    def test_publish_summary_supports_cnb_dry_run_without_repo(self) -> None:
        summary = _build_summary()

        with (
            patch("fluxa.publish.render_run_issue", return_value="# dry-run issue"),
            patch("fluxa.publish.datetime", _FixedPublishDatetime),
        ):
            with patch.dict("os.environ", {}, clear=True):
                result = publish_summaries(
                    summary,
                    Path("."),
                    publishers=("cnb",),
                    repo=None,
                    timezone_name="Asia/Shanghai",
                    run_id="dry-run-cnb",
                    display_key=None,
                    dry_run=True,
                )[0]

        self.assertEqual(result.publisher, "cnb")
        self.assertIsNone(result.repo)
        self.assertIsNone(result.issue_number)
        self.assertEqual(
            result.issue_title, "Fluxa Digest | 2026-03-27 | run dry-run-cnb"
        )

    def test_publish_summary_keeps_run_id_title_when_display_key_is_explicit(
        self,
    ) -> None:
        summary = _build_summary()

        with (
            patch("fluxa.publish.render_run_issue", return_value="# dry-run issue"),
            patch("fluxa.publish.datetime", _FixedPublishDatetime),
        ):
            with patch.dict("os.environ", {}, clear=True):
                result = publish_summaries(
                    summary,
                    Path("."),
                    publishers=("github",),
                    repo=None,
                    timezone_name="Asia/Shanghai",
                    run_id="dry-run-display",
                    display_key="Morning Window",
                    dry_run=True,
                )[0]

        self.assertEqual(
            result.issue_title, "Fluxa Digest | 2026-03-27 | run dry-run-display"
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
                    publish_summaries(
                        summary,
                        templates_dir,
                        publishers=("github",),
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
                cmd=["gh", "issue", "create"], timeout=60
            ),
        ):
            with self.assertRaises(PublishError):
                _run_gh(["issue", "create"])

    def test_publish_state_file_is_json_object(self) -> None:
        summary = _build_summary()

        with tempfile.TemporaryDirectory() as temp_dir:
            publish_state_path = Path(temp_dir) / "publish-state.json"
            publish_state = PublishState()

            with (
                patch("fluxa.publish.render_run_issue", return_value="# issue body"),
                patch("fluxa.publish.datetime", _FixedPublishDatetime),
                patch("fluxa.publish._run_gh_json", return_value={"number": 10}),
                patch.dict(
                    "os.environ",
                    {"GITHUB_REPOSITORY": "wwvo/Fluxa"},
                    clear=True,
                ),
            ):
                publish_summaries(
                    summary,
                    Path("."),
                    publishers=("github",),
                    repo=None,
                    timezone_name="Asia/Shanghai",
                    run_id="single-run",
                    display_key=None,
                    dry_run=False,
                    publish_state=publish_state,
                    publish_state_path=publish_state_path,
                )

            payload = json.loads(publish_state_path.read_text(encoding="utf-8"))

        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["latest_window_key"], "2026-03-27|08:00-10:00")


if __name__ == "__main__":
    unittest.main()
