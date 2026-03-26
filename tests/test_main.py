from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fluxa.main import _write_step_summary
from fluxa.models import AppConfig, FeedDefaults, RunSummary
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
    def test_dry_run_summary_does_not_render_issue_none(self) -> None:
        summary = _build_summary()
        publish_result = PublishResult(
            repo=None,
            issue_number=None,
            issue_title="Fluxa Digest | 2026-03-27 | run dry-run-1",
            run_id="dry-run-1",
            issue_date="2026-03-27",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            summary_path = Path(temp_dir) / "summary.md"
            original_step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
            os.environ["GITHUB_STEP_SUMMARY"] = str(summary_path)
            try:
                _write_step_summary(
                    summary,
                    config_path="feeds/feeds.yml",
                    state_path="state/state.json",
                    publish_result=publish_result,
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


if __name__ == "__main__":
    unittest.main()
