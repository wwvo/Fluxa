"""Markdown 模板渲染。

本模块把 `RunSummary` 投影为 issue 模板可直接消费的数据结构，并负责最终 Markdown 渲染。
它是“业务数据”和“展示文案”之间的边界层，方便后续单独调整模板而不改抓取逻辑。
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, TemplateError, select_autoescape

from fluxa.models import PublishError, RunSummary

_RUN_ISSUE_TEMPLATE = "run_issue.md.j2"


def render_run_issue(
    templates_dir: Path,
    summary: RunSummary,
    *,
    issue_title: str,
    display_key: str,
    timezone_name: str,
    timezone: ZoneInfo,
    run_id: str,
    run_time: datetime,
) -> str:
    try:
        template = _build_environment(templates_dir).get_template(_RUN_ISSUE_TEMPLATE)
        return template.render(
            run_marker=f"fluxa-run:{run_id}",
            issue_title=issue_title,
            display_key=display_key,
            run_id=run_id,
            run_time=run_time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            timezone_name=timezone_name,
            total_feeds=len(summary.config.feeds),
            enabled_feeds=len(summary.config.enabled_feeds),
            checked_count=summary.checked_count,
            new_count=summary.new_count,
            error_count=summary.error_count,
            not_modified_count=summary.not_modified_count,
            grouped_entries=_group_entries(summary, timezone),
            errors=[
                {
                    "feed_id": result.feed.id,
                    "feed_title": result.feed_title,
                    "error": result.error or "未知错误",
                }
                for result in summary.results
                if result.status == "error"
            ],
        )
    except (OSError, TemplateError) as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        raise PublishError(
            f"issue 模板渲染失败（{_RUN_ISSUE_TEMPLATE}）: {detail}"
        ) from exc


def _build_environment(templates_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _group_entries(summary: RunSummary, timezone: ZoneInfo) -> list[dict[str, object]]:
    grouped: OrderedDict[str, list[dict[str, str | None]]] = OrderedDict()
    titles: dict[str, str] = {}

    for entry in summary.new_entries:
        grouped.setdefault(entry.feed_id, [])
        titles.setdefault(entry.feed_id, entry.feed_title)
        grouped[entry.feed_id].append(
            {
                "title": entry.title,
                "url": entry.url,
                "published_at": _format_entry_time(entry.published_at, timezone),
                "summary": entry.summary,
            }
        )

    return [
        {
            "feed_id": feed_id,
            "feed_title": titles[feed_id],
            "entries": entries,
            "count": len(entries),
        }
        for feed_id, entries in grouped.items()
    ]


def _format_entry_time(value: datetime | None, timezone: ZoneInfo) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone).strftime("%Y-%m-%d %H:%M")
