from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fluxa.main import _build_recovery_sections, _format_attempts, _write_step_summary
from fluxa.models import (
    AppConfig,
    FeedAttemptResult,
    FeedConfig,
    FeedDefaults,
    FeedPollResult,
    FeedState,
    RunSummary,
)
from fluxa.publish import PublishResult


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


class StepSummaryTests(unittest.TestCase):
    def test_build_recovery_sections_keeps_console_and_markdown_titles_in_sync(
        self,
    ) -> None:
        summary = RunSummary(
            config=AppConfig(
                path=Path("feeds/feeds.yml"),
                defaults=FeedDefaults(),
                feeds=(),
            ),
            bootstrap_mode=False,
            results=[
                FeedPollResult(
                    feed=FeedConfig(
                        id="fallback",
                        url="https://example.com/feed.xml",
                        fallback_urls=(),
                        title="Fallback Feed",
                        enabled=True,
                        timeout_seconds=20,
                        max_entries_per_feed=20,
                        max_seen_ids=300,
                    ),
                    feed_title="Fallback Feed",
                    checked_at="2026-03-27T00:00:00+00:00",
                    status="ok",
                    http_status=200,
                    source_url="https://mirror.example.com/feed.xml",
                    entries=[],
                    new_entries=[],
                    next_state=FeedState(),
                    used_fallback=True,
                    recovered_from_error=False,
                ),
                FeedPollResult(
                    feed=FeedConfig(
                        id="recovered",
                        url="https://example.com/recovered.xml",
                        fallback_urls=(),
                        title="Recovered Feed",
                        enabled=True,
                        timeout_seconds=20,
                        max_entries_per_feed=20,
                        max_seen_ids=300,
                    ),
                    feed_title="Recovered Feed",
                    checked_at="2026-03-27T00:00:00+00:00",
                    status="ok",
                    http_status=200,
                    source_url="https://example.com/recovered.xml",
                    entries=[],
                    new_entries=[],
                    next_state=FeedState(),
                    used_fallback=False,
                    recovered_from_error=True,
                ),
            ],
        )

        sections = _build_recovery_sections(summary)

        self.assertEqual(
            [(section.console_title, section.markdown_title) for section in sections],
            [
                ("本轮由备用实例兜底的 feeds：", "备用实例兜底成功"),
                ("本轮恢复成功并扩大抓取窗口的 feeds：", "失败后恢复成功"),
            ],
        )

    def test_dry_run_summary_does_not_render_issue_none(self) -> None:
        summary = _build_summary()
        publish_results = [
            PublishResult(
                publisher="github",
                repo=None,
                issue_number=None,
                issue_title="Fluxa Digest | 2026-03-27 | 08:00-10:00",
                run_id="dry-run-1",
                issue_date="2026-03-27",
            ),
            PublishResult(
                publisher="cnb",
                repo="wwvo/Issuo",
                issue_number=None,
                issue_title="Fluxa Digest | 2026-03-27 | 08:00-10:00",
                run_id="dry-run-1",
                issue_date="2026-03-27",
            ),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            summary_path = Path(temp_dir) / "summary.md"
            original_step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
            os.environ["GITHUB_STEP_SUMMARY"] = str(summary_path)
            try:
                _write_step_summary(
                    summary,
                    config_path="feeds/feeds.yml",
                    state_path="state/state.json",
                    publish_results=publish_results,
                    dry_run=True,
                    state_saved=False,
                    operation_error=None,
                    total_count=0,
                    enabled_count=0,
                )
            finally:
                if original_step_summary is None:
                    os.environ.pop("GITHUB_STEP_SUMMARY", None)
                else:
                    os.environ["GITHUB_STEP_SUMMARY"] = original_step_summary

            rendered = summary_path.read_text(encoding="utf-8")

        self.assertIn("dry-run", rendered)
        self.assertNotIn("issue #None", rendered)
        self.assertIn("GitHub issue", rendered)
        self.assertIn("CNB issue", rendered)

    def test_format_attempts_includes_retry_mode_note(self) -> None:
        result = FeedPollResult(
            feed=FeedConfig(
                id="demo",
                url="https://example.com/feed.xml",
                fallback_urls=(),
                title="Demo Feed",
                enabled=True,
                timeout_seconds=20,
                max_entries_per_feed=20,
                max_seen_ids=300,
            ),
            feed_title="Demo Feed",
            checked_at="2026-03-27T00:00:00+00:00",
            status="error",
            http_status=415,
            source_url="https://example.com/feed.xml",
            entries=[],
            new_entries=[],
            next_state=FeedState(),
            attempts=[
                FeedAttemptResult(
                    source_url="https://example.com/feed.xml",
                    attempt_number=1,
                    status="error",
                    http_status=415,
                    error="status 415",
                    note="条件请求，已切换宽松请求头重试",
                ),
                FeedAttemptResult(
                    source_url="https://example.com/feed.xml",
                    attempt_number=2,
                    status="error",
                    http_status=415,
                    error="status 415",
                    note="宽松请求头",
                ),
            ],
            error="status 415",
        )

        attempt_text = _format_attempts(result)

        self.assertIn("条件请求，已切换宽松请求头重试", attempt_text)
        self.assertIn("宽松请求头", attempt_text)


if __name__ == "__main__":
    unittest.main()
