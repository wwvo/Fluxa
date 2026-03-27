"""Fluxa 核心数据模型。

这里定义了 Fluxa 在各模块之间传递的统一数据结构：
- Config 层负责描述输入源与抓取参数
- State 层负责描述持久化缓存与增量上下文
- Result / Summary 层负责承接一次轮询与发布流程的输出
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


class FluxaError(Exception):
    """Fluxa 运行期基础异常。"""


class ConfigError(FluxaError):
    """配置文件错误。"""


class StateError(FluxaError):
    """状态文件错误。"""


class PublishError(FluxaError):
    """Issue 发布错误。"""


@dataclass(slots=True, frozen=True)
class NormalizedEntry:
    """统一后的 RSS 条目。"""

    feed_id: str
    feed_title: str
    entry_id: str
    title: str
    url: str | None
    published_at: datetime | None
    summary: str | None

    @property
    def published_at_iso(self) -> str | None:
        if self.published_at is None:
            return None
        return self.published_at.isoformat()


@dataclass(slots=True, frozen=True)
class FeedDefaults:
    """全局默认抓取配置。"""

    timeout_seconds: int = 20
    max_entries_per_feed: int = 20
    max_seen_ids: int = 300
    enabled: bool = True


@dataclass(slots=True, frozen=True)
class FeedConfig:
    """单个 RSS Feed 配置。"""

    id: str
    url: str
    fallback_urls: tuple[str, ...]
    title: str | None
    enabled: bool
    timeout_seconds: int
    max_entries_per_feed: int
    max_seen_ids: int

    @property
    def source_urls(self) -> tuple[str, ...]:
        ordered_urls: list[str] = []
        seen_urls: set[str] = set()
        for source_url in (self.url, *self.fallback_urls):
            if source_url in seen_urls:
                continue
            seen_urls.add(source_url)
            ordered_urls.append(source_url)
        return tuple(ordered_urls)


@dataclass(slots=True, frozen=True)
class AppConfig:
    """应用配置聚合。"""

    path: Path
    defaults: FeedDefaults
    feeds: tuple[FeedConfig, ...]

    @property
    def enabled_feeds(self) -> tuple[FeedConfig, ...]:
        return tuple(feed for feed in self.feeds if feed.enabled)


@dataclass(slots=True)
class FeedSourceState:
    """单个来源 URL 的缓存与最近状态。"""

    etag: str | None = None
    last_modified: str | None = None
    last_checked_at: str | None = None
    last_success_at: str | None = None
    last_http_status: int | None = None
    last_error: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FeedSourceState":
        last_http_status = payload.get("last_http_status")
        if last_http_status is not None and not _is_strict_int(last_http_status):
            raise StateError("state.feed.source.last_http_status 必须是整数")
        return cls(
            etag=_coerce_optional_str(payload.get("etag"), "state.feed.source.etag"),
            last_modified=_coerce_optional_str(
                payload.get("last_modified"),
                "state.feed.source.last_modified",
            ),
            last_checked_at=_coerce_optional_str(
                payload.get("last_checked_at"),
                "state.feed.source.last_checked_at",
            ),
            last_success_at=_coerce_optional_str(
                payload.get("last_success_at"),
                "state.feed.source.last_success_at",
            ),
            last_http_status=last_http_status,
            last_error=_coerce_optional_str(
                payload.get("last_error"),
                "state.feed.source.last_error",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "etag": self.etag,
            "last_modified": self.last_modified,
            "last_checked_at": self.last_checked_at,
            "last_success_at": self.last_success_at,
            "last_http_status": self.last_http_status,
            "last_error": self.last_error,
        }


@dataclass(slots=True)
class FeedState:
    """单个 Feed 的持久化状态。"""

    etag: str | None = None
    last_modified: str | None = None
    seen_ids: list[str] = field(default_factory=list)
    last_checked_at: str | None = None
    last_success_at: str | None = None
    last_http_status: int | None = None
    last_error: str | None = None
    last_success_source: str | None = None
    sources: dict[str, FeedSourceState] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FeedState":
        seen_ids_raw = payload.get("seen_ids", [])
        if not isinstance(seen_ids_raw, list):
            raise StateError("state.feed.seen_ids 必须是数组")
        seen_ids: list[str] = []
        for index, item in enumerate(seen_ids_raw):
            if not isinstance(item, str):
                raise StateError(f"state.feed.seen_ids[{index}] 必须是字符串")
            text = item.strip()
            if text:
                seen_ids.append(text)
        last_http_status = payload.get("last_http_status")
        if last_http_status is not None and not _is_strict_int(last_http_status):
            raise StateError("state.feed.last_http_status 必须是整数")

        sources_raw = payload.get("sources", {})
        if not isinstance(sources_raw, dict):
            raise StateError("state.feed.sources 必须是对象")

        sources: dict[str, FeedSourceState] = {}
        for source_url, source_payload in sources_raw.items():
            if not isinstance(source_url, str) or not source_url.strip():
                raise StateError("state.feed.sources 的 key 必须是非空字符串")
            if not isinstance(source_payload, dict):
                raise StateError(f"state.feed.sources.{source_url} 必须是对象")
            sources[source_url] = FeedSourceState.from_dict(source_payload)

        return cls(
            etag=_coerce_optional_str(payload.get("etag"), "state.feed.etag"),
            last_modified=_coerce_optional_str(
                payload.get("last_modified"),
                "state.feed.last_modified",
            ),
            seen_ids=seen_ids,
            last_checked_at=_coerce_optional_str(
                payload.get("last_checked_at"),
                "state.feed.last_checked_at",
            ),
            last_success_at=_coerce_optional_str(
                payload.get("last_success_at"),
                "state.feed.last_success_at",
            ),
            last_http_status=last_http_status,
            last_error=_coerce_optional_str(
                payload.get("last_error"),
                "state.feed.last_error",
            ),
            last_success_source=_coerce_optional_str(
                payload.get("last_success_source"),
                "state.feed.last_success_source",
            ),
            sources=sources,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "etag": self.etag,
            "last_modified": self.last_modified,
            "seen_ids": self.seen_ids,
            "last_checked_at": self.last_checked_at,
            "last_success_at": self.last_success_at,
            "last_http_status": self.last_http_status,
            "last_error": self.last_error,
            "last_success_source": self.last_success_source,
            "sources": {
                source_url: source_state.to_dict()
                for source_url, source_state in sorted(self.sources.items())
            },
        }

    def get_source_state(
        self,
        source_url: str,
        *,
        primary_url: str,
    ) -> FeedSourceState:
        source_state = self.sources.get(source_url)
        if source_state is not None:
            return source_state
        if source_url == primary_url and (self.etag or self.last_modified):
            return FeedSourceState(
                etag=self.etag,
                last_modified=self.last_modified,
            )
        return FeedSourceState()

    def resolve_primary_cache(self, primary_url: str) -> tuple[str | None, str | None]:
        primary_state = self.sources.get(primary_url)
        if primary_state is not None:
            return primary_state.etag, primary_state.last_modified
        return self.etag, self.last_modified


@dataclass(slots=True, frozen=True)
class FeedAttemptResult:
    """单次来源请求的结果。"""

    source_url: str
    attempt_number: int
    status: str
    http_status: int | None
    error: str | None = None
    note: str | None = None


@dataclass(slots=True)
class FeedPollResult:
    """单个 Feed 的轮询结果。"""

    feed: FeedConfig
    feed_title: str
    checked_at: str
    status: str
    http_status: int | None
    source_url: str | None
    entries: list[NormalizedEntry]
    new_entries: list[NormalizedEntry]
    next_state: FeedState
    attempts: list[FeedAttemptResult] = field(default_factory=list)
    used_fallback: bool = False
    recovered_from_error: bool = False
    effective_max_entries_per_feed: int | None = None
    error: str | None = None


@dataclass(slots=True)
class AppState:
    """应用整体状态。"""

    schema_version: int = 1
    bootstrap_completed: bool = False
    feeds: dict[str, FeedState] = field(default_factory=dict)

    def ensure_feeds(self, feed_ids: list[str]) -> None:
        self.feeds = {
            feed_id: self.feeds.get(feed_id, FeedState()) for feed_id in feed_ids
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AppState":
        schema_version = payload.get("schema_version", 1)
        if not _is_strict_int(schema_version) or schema_version < 1:
            raise StateError("state.schema_version 必须是正整数")
        bootstrap_completed = payload.get("bootstrap_completed", False)
        if not isinstance(bootstrap_completed, bool):
            raise StateError("state.bootstrap_completed 必须是布尔值")

        feeds_raw = payload.get("feeds", {})
        if not isinstance(feeds_raw, dict):
            raise StateError("state.feeds 必须是对象")

        feeds: dict[str, FeedState] = {}
        for feed_id, feed_state_raw in feeds_raw.items():
            if not isinstance(feed_id, str) or not feed_id.strip():
                raise StateError("state.feeds 的 key 必须是非空字符串")
            if not isinstance(feed_state_raw, dict):
                raise StateError(f"state.feeds.{feed_id} 必须是对象")
            feeds[feed_id] = FeedState.from_dict(feed_state_raw)

        return cls(
            schema_version=schema_version,
            bootstrap_completed=bootstrap_completed,
            feeds=feeds,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "bootstrap_completed": self.bootstrap_completed,
            "feeds": {
                feed_id: feed_state.to_dict()
                for feed_id, feed_state in sorted(self.feeds.items())
            },
        }


@dataclass(slots=True)
class PublishTargetState:
    """单个发布后端的 issue 落盘信息。"""

    repo: str | None = None
    issue_number: int | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PublishTargetState":
        issue_number = payload.get("issue_number")
        if issue_number is not None:
            if not _is_strict_int(issue_number) or issue_number <= 0:
                raise StateError("publish.target.issue_number 必须是正整数")
        return cls(
            repo=_coerce_optional_str(payload.get("repo"), "publish.target.repo"),
            issue_number=issue_number,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "issue_number": self.issue_number,
        }


@dataclass(slots=True)
class PublishWindowState:
    """单个抓取窗口的发布账本。"""

    issue_date: str
    display_key: str
    issue_title: str
    run_id: str
    publishers: dict[str, PublishTargetState] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PublishWindowState":
        publishers_raw = payload.get("publishers", {})
        if not isinstance(publishers_raw, dict):
            raise StateError("publish.window.publishers 必须是对象")

        publishers: dict[str, PublishTargetState] = {}
        for publisher_name, publisher_payload in publishers_raw.items():
            if not isinstance(publisher_name, str) or not publisher_name.strip():
                raise StateError("publish.window.publishers 的 key 必须是非空字符串")
            if not isinstance(publisher_payload, dict):
                raise StateError(
                    f"publish.window.publishers.{publisher_name} 必须是对象"
                )
            publishers[publisher_name] = PublishTargetState.from_dict(publisher_payload)

        issue_date = _coerce_required_str(
            payload.get("issue_date"), "publish.window.issue_date"
        )
        display_key = _coerce_required_str(
            payload.get("display_key"),
            "publish.window.display_key",
        )
        issue_title = _coerce_required_str(
            payload.get("issue_title"),
            "publish.window.issue_title",
        )
        run_id = _coerce_required_str(payload.get("run_id"), "publish.window.run_id")
        return cls(
            issue_date=issue_date,
            display_key=display_key,
            issue_title=issue_title,
            run_id=run_id,
            publishers=publishers,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_date": self.issue_date,
            "display_key": self.display_key,
            "issue_title": self.issue_title,
            "run_id": self.run_id,
            "publishers": {
                publisher_name: publisher_state.to_dict()
                for publisher_name, publisher_state in sorted(self.publishers.items())
            },
        }


@dataclass(slots=True)
class PublishState:
    """Issue 发布账本。"""

    schema_version: int = 1
    latest_window_key: str | None = None
    windows: dict[str, PublishWindowState] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PublishState":
        schema_version = payload.get("schema_version", 1)
        if not _is_strict_int(schema_version) or schema_version < 1:
            raise StateError("publish.schema_version 必须是正整数")

        latest_window_key = _coerce_optional_str(
            payload.get("latest_window_key"),
            "publish.latest_window_key",
        )

        windows_raw = payload.get("windows", {})
        if not isinstance(windows_raw, dict):
            raise StateError("publish.windows 必须是对象")

        windows: dict[str, PublishWindowState] = {}
        for window_key, window_payload in windows_raw.items():
            if not isinstance(window_key, str) or not window_key.strip():
                raise StateError("publish.windows 的 key 必须是非空字符串")
            if not isinstance(window_payload, dict):
                raise StateError(f"publish.windows.{window_key} 必须是对象")
            windows[window_key] = PublishWindowState.from_dict(window_payload)

        return cls(
            schema_version=schema_version,
            latest_window_key=latest_window_key,
            windows=windows,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "latest_window_key": self.latest_window_key,
            "windows": {
                window_key: window_state.to_dict()
                for window_key, window_state in sorted(self.windows.items())
            },
        }

    def get_issue_number(self, window_key: str, publisher: str) -> int | None:
        window_state = self.windows.get(window_key)
        if window_state is None:
            return None
        publisher_state = window_state.publishers.get(publisher)
        if publisher_state is None:
            return None
        return publisher_state.issue_number

    def record_issue(
        self,
        *,
        window_key: str,
        issue_date: str,
        display_key: str,
        issue_title: str,
        run_id: str,
        publisher: str,
        repo: str,
        issue_number: int,
    ) -> None:
        normalized_window_key = window_key.strip()
        if not normalized_window_key:
            raise StateError("publish.window_key 不能为空")

        window_state = self.windows.get(normalized_window_key)
        if window_state is None:
            window_state = PublishWindowState(
                issue_date=issue_date,
                display_key=display_key,
                issue_title=issue_title,
                run_id=run_id,
            )
            self.windows[normalized_window_key] = window_state

        window_state.issue_date = issue_date
        window_state.display_key = display_key
        window_state.issue_title = issue_title
        window_state.run_id = run_id
        window_state.publishers[publisher] = PublishTargetState(
            repo=repo,
            issue_number=issue_number,
        )
        self.latest_window_key = normalized_window_key


@dataclass(slots=True)
class RunSummary:
    """一次完整轮询的汇总结果。"""

    config: AppConfig
    bootstrap_mode: bool
    results: list[FeedPollResult]

    @property
    def checked_count(self) -> int:
        return len(self.results)

    @property
    def error_count(self) -> int:
        return sum(1 for result in self.results if result.status == "error")

    @property
    def not_modified_count(self) -> int:
        return sum(1 for result in self.results if result.status == "not-modified")

    @property
    def new_entries(self) -> list[NormalizedEntry]:
        return [entry for result in self.results for entry in result.new_entries]

    @property
    def new_count(self) -> int:
        return len(self.new_entries)

    @property
    def failed_results(self) -> list[FeedPollResult]:
        return [result for result in self.results if result.status == "error"]

    @property
    def fallback_recovered_results(self) -> list[FeedPollResult]:
        return [
            result
            for result in self.results
            if result.status != "error" and result.used_fallback
        ]

    @property
    def recovered_results(self) -> list[FeedPollResult]:
        return [
            result
            for result in self.results
            if result.status != "error" and result.recovered_from_error
        ]


def _coerce_optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StateError(f"{field_name} 必须是字符串")
    text = value.strip()
    return text or None


def _coerce_required_str(value: Any, field_name: str) -> str:
    text = _coerce_optional_str(value, field_name)
    if text is None:
        raise StateError(f"{field_name} 必须是非空字符串")
    return text


def _is_strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
