"""RSS 抓取与条件请求。

本模块负责网络层和来源调度层：并发抓取、条件请求、fallback、retry、host 限流都在这里完成。
它的输出是 `FeedPollResult`，供后续汇总、渲染和发布阶段消费。
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import BoundedSemaphore
from time import sleep
from urllib.parse import urlsplit

import feedparser
import httpx

from fluxa.diff import compute_entry_delta
from fluxa.models import (
    FeedAttemptResult,
    FeedConfig,
    FeedPollResult,
    FeedSourceState,
    FeedState,
    NormalizedEntry,
)
from fluxa.normalize import normalize_entries

_DEFAULT_HEADERS = {
    "User-Agent": "Fluxa/0.1 (+https://github.com/)",
    "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
}
_MAX_WORKERS = 6
_MAX_PER_HOST = 2
_MAX_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 0.5
_RECOVERY_WINDOW_MULTIPLIER = 5
_RECOVERY_MAX_ENTRIES_CAP = 100
_SUCCESS_STATUSES = {"ok", "parse-warning", "not-modified"}


@dataclass(slots=True)
class _SourcePollResult:
    """单个来源 URL 的请求结果。"""

    source_url: str
    status: str
    http_status: int | None
    next_source_state: FeedSourceState
    attempts: list[FeedAttemptResult]
    feed_title: str | None = None
    entries: list[NormalizedEntry] = field(default_factory=list)
    new_entries: list[NormalizedEntry] = field(default_factory=list)
    next_seen_ids: list[str] | None = None
    error: str | None = None


def poll_feeds(
    feeds: list[FeedConfig] | tuple[FeedConfig, ...],
    state_by_feed: dict[str, FeedState],
    *,
    bootstrap_mode: bool,
) -> list[FeedPollResult]:
    if not feeds:
        return []

    results: list[FeedPollResult] = []
    # 总并发受全局 worker 限制，单 host 再单独限流，避免某个 RSSHub 实例被瞬时打爆。
    host_limiters = _build_host_limiters(feeds)
    max_workers = min(_MAX_WORKERS, len(feeds)) or 1

    with httpx.Client(headers=_DEFAULT_HEADERS, follow_redirects=True) as client:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures: list[tuple[str, Future[FeedPollResult]]] = []
            for feed in feeds:
                previous_state = state_by_feed.get(feed.id, FeedState())
                feed_bootstrap = (
                    bootstrap_mode or previous_state.last_success_at is None
                )
                future = executor.submit(
                    _poll_feed_safely,
                    client,
                    feed,
                    previous_state,
                    bootstrap_mode=feed_bootstrap,
                    host_limiters=host_limiters,
                )
                futures.append((feed.id, future))

            for feed_id, future in futures:
                result = future.result()
                state_by_feed[feed_id] = result.next_state
                results.append(result)

    return results


def _poll_feed_safely(
    client: httpx.Client,
    feed: FeedConfig,
    feed_state: FeedState,
    *,
    bootstrap_mode: bool,
    host_limiters: dict[str, BoundedSemaphore],
) -> FeedPollResult:
    try:
        return poll_feed(
            client,
            feed,
            feed_state,
            bootstrap_mode=bootstrap_mode,
            host_limiters=host_limiters,
        )
    except Exception as exc:
        return _build_unexpected_feed_error_result(feed, feed_state, exc)


def poll_feed(
    client: httpx.Client,
    feed: FeedConfig,
    feed_state: FeedState,
    *,
    bootstrap_mode: bool,
    host_limiters: dict[str, BoundedSemaphore],
) -> FeedPollResult:
    checked_at = _utcnow_iso()
    recovered_from_error = _was_previous_poll_error(feed_state)
    # 某个 feed 刚从失败恢复时，临时扩大抓取窗口，尽量补回停机期间漏掉的新文章。
    effective_entry_limit = _resolve_entry_limit(feed, recovered_from_error)
    source_states = _clone_source_states(feed_state)
    attempts: list[FeedAttemptResult] = []

    # 来源顺序会优先尝试上次成功的实例；一旦某个来源成功，本轮就提前结束。
    for source_url in _resolve_source_urls(feed, feed_state):
        source_state = _resolve_feed_source_state(
            feed_state,
            source_states,
            source_url,
            primary_url=feed.url,
        )

        source_result = _poll_source(
            client,
            feed,
            source_url,
            source_state,
            seen_ids=feed_state.seen_ids,
            bootstrap_mode=bootstrap_mode,
            checked_at=checked_at,
            host_limiters=host_limiters,
            entry_limit=effective_entry_limit,
        )
        source_states[source_url] = source_result.next_source_state
        attempts.extend(source_result.attempts)

        if source_result.status in _SUCCESS_STATUSES:
            return _build_feed_success_result(
                feed,
                feed_state,
                source_states,
                source_result,
                attempts=attempts,
                checked_at=checked_at,
                source_url=source_url,
                recovered_from_error=recovered_from_error,
                effective_entry_limit=effective_entry_limit,
            )

    return _build_feed_error_result(
        feed,
        feed_state,
        source_states,
        attempts=attempts,
        checked_at=checked_at,
    )


def _poll_source(
    client: httpx.Client,
    feed: FeedConfig,
    source_url: str,
    source_state: FeedSourceState,
    *,
    seen_ids: list[str],
    bootstrap_mode: bool,
    checked_at: str,
    host_limiters: dict[str, BoundedSemaphore],
    entry_limit: int,
) -> _SourcePollResult:
    # 条件请求按来源 URL 维度缓存，避免不同 RSSHub 实例之间错误复用 ETag / Last-Modified。
    request_headers = _build_conditional_headers(source_state)
    attempts: list[FeedAttemptResult] = []
    total_attempts = _MAX_RETRIES + 1

    for attempt_number in range(1, total_attempts + 1):
        try:
            response = _get_with_host_limit(
                client,
                source_url,
                headers=request_headers,
                timeout=feed.timeout_seconds,
                host_limiters=host_limiters,
            )
            if response.status_code == 304:
                attempts.append(
                    FeedAttemptResult(
                        source_url=source_url,
                        attempt_number=attempt_number,
                        status="not-modified",
                        http_status=304,
                    )
                )
                return _SourcePollResult(
                    source_url=source_url,
                    status="not-modified",
                    http_status=304,
                    next_source_state=FeedSourceState(
                        etag=source_state.etag,
                        last_modified=source_state.last_modified,
                        last_checked_at=checked_at,
                        last_success_at=checked_at,
                        last_http_status=304,
                        last_error=None,
                    ),
                    attempts=attempts,
                    feed_title=feed.title or feed.id,
                )

            response.raise_for_status()
            try:
                parsed = feedparser.parse(response.content)
                # feedparser 解析容错较强；只要能提取到条目，就仍按成功处理。
                feed_title = feed.title or parsed.feed.get("title") or feed.id
                entries = normalize_entries(
                    feed,
                    feed_title,
                    parsed.entries,
                    entry_limit=entry_limit,
                )
                new_entries, merged_seen_ids = compute_entry_delta(
                    entries,
                    seen_ids,
                    max_seen_ids=feed.max_seen_ids,
                    suppress_new_entries=bootstrap_mode,
                )
            except Exception as exc:
                error_text = _format_parse_error(exc)
                attempts.append(
                    FeedAttemptResult(
                        source_url=source_url,
                        attempt_number=attempt_number,
                        status="error",
                        http_status=response.status_code,
                        error=error_text,
                    )
                )
                return _build_source_error_result(
                    source_url,
                    source_state,
                    checked_at=checked_at,
                    http_status=response.status_code,
                    error=error_text,
                    attempts=attempts,
                )
            status = "ok"
            if parsed.bozo and not entries:
                status = "parse-warning"
            attempts.append(
                FeedAttemptResult(
                    source_url=source_url,
                    attempt_number=attempt_number,
                    status=status,
                    http_status=response.status_code,
                )
            )
            return _SourcePollResult(
                source_url=source_url,
                status=status,
                http_status=response.status_code,
                next_source_state=FeedSourceState(
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                    last_checked_at=checked_at,
                    last_success_at=checked_at,
                    last_http_status=response.status_code,
                    last_error=None,
                ),
                attempts=attempts,
                feed_title=feed_title,
                entries=entries,
                new_entries=new_entries,
                next_seen_ids=merged_seen_ids,
            )
        except httpx.HTTPError as exc:
            response = getattr(exc, "response", None)
            http_status = response.status_code if response is not None else None
            error_text = str(exc)
            attempts.append(
                FeedAttemptResult(
                    source_url=source_url,
                    attempt_number=attempt_number,
                    status="error",
                    http_status=http_status,
                    error=error_text,
                )
            )
            if attempt_number < total_attempts and _is_retryable_error(exc):
                # 只对瞬时错误重试，避免把 4xx 配置错误或永久失效源重复打满。
                sleep(_RETRY_BACKOFF_SECONDS * attempt_number)
                continue
            return _build_source_error_result(
                source_url,
                source_state,
                checked_at=checked_at,
                http_status=http_status,
                error=error_text,
                attempts=attempts,
            )

    return _build_source_error_result(
        source_url,
        source_state,
        checked_at=checked_at,
        http_status=None,
        error="未知错误",
        attempts=attempts,
    )


def _build_host_limiters(
    feeds: list[FeedConfig] | tuple[FeedConfig, ...],
) -> dict[str, BoundedSemaphore]:
    host_limiters: dict[str, BoundedSemaphore] = {}
    for feed in feeds:
        for source_url in feed.source_urls:
            host = _extract_host(source_url)
            host_limiters.setdefault(host, BoundedSemaphore(_MAX_PER_HOST))
    return host_limiters


def _resolve_feed_source_state(
    feed_state: FeedState,
    source_states: dict[str, FeedSourceState],
    source_url: str,
    *,
    primary_url: str,
) -> FeedSourceState:
    source_state = source_states.get(source_url)
    if source_state is not None:
        return source_state
    return _clone_source_state(
        feed_state.get_source_state(source_url, primary_url=primary_url)
    )


def _resolve_source_urls(
    feed: FeedConfig,
    feed_state: FeedState,
) -> tuple[str, ...]:
    ordered_urls: list[str] = []
    seen_urls: set[str] = set()

    # 上次成功的实例通常命中率最高，下一轮优先尝试它，减少不必要的 fallback 探测。
    if (
        feed_state.last_success_source
        and feed_state.last_success_source in feed.source_urls
    ):
        ordered_urls.append(feed_state.last_success_source)
        seen_urls.add(feed_state.last_success_source)

    for source_url in feed.source_urls:
        if source_url in seen_urls:
            continue
        seen_urls.add(source_url)
        ordered_urls.append(source_url)

    return tuple(ordered_urls)


def _build_conditional_headers(source_state: FeedSourceState) -> dict[str, str]:
    request_headers: dict[str, str] = {}
    if source_state.etag:
        request_headers["If-None-Match"] = source_state.etag
    if source_state.last_modified:
        request_headers["If-Modified-Since"] = source_state.last_modified
    return request_headers


def _get_with_host_limit(
    client: httpx.Client,
    source_url: str,
    *,
    headers: dict[str, str],
    timeout: int,
    host_limiters: dict[str, BoundedSemaphore],
) -> httpx.Response:
    limiter = host_limiters[_extract_host(source_url)]
    limiter.acquire()
    try:
        return client.get(
            source_url,
            headers=headers,
            timeout=timeout,
        )
    finally:
        limiter.release()


def _build_source_error_result(
    source_url: str,
    source_state: FeedSourceState,
    *,
    checked_at: str,
    http_status: int | None,
    error: str,
    attempts: list[FeedAttemptResult],
) -> _SourcePollResult:
    return _SourcePollResult(
        source_url=source_url,
        status="error",
        http_status=http_status,
        next_source_state=FeedSourceState(
            etag=source_state.etag,
            last_modified=source_state.last_modified,
            last_checked_at=checked_at,
            last_success_at=source_state.last_success_at,
            last_http_status=http_status,
            last_error=error,
        ),
        attempts=attempts,
        error=error,
    )


def _build_feed_success_result(
    feed: FeedConfig,
    previous_state: FeedState,
    source_states: dict[str, FeedSourceState],
    source_result: _SourcePollResult,
    *,
    attempts: list[FeedAttemptResult],
    checked_at: str,
    source_url: str,
    recovered_from_error: bool,
    effective_entry_limit: int,
) -> FeedPollResult:
    next_state = _build_success_state(
        feed,
        previous_state,
        source_states,
        checked_at=checked_at,
        source_url=source_url,
        http_status=source_result.http_status,
        seen_ids=source_result.next_seen_ids or previous_state.seen_ids.copy(),
    )
    return FeedPollResult(
        feed=feed,
        feed_title=source_result.feed_title or feed.title or feed.id,
        checked_at=checked_at,
        status=source_result.status,
        http_status=source_result.http_status,
        source_url=source_url,
        entries=source_result.entries,
        new_entries=source_result.new_entries,
        next_state=next_state,
        attempts=attempts,
        used_fallback=source_url != feed.url,
        recovered_from_error=recovered_from_error,
        effective_max_entries_per_feed=(
            effective_entry_limit
            if source_result.status in {"ok", "parse-warning"}
            else None
        ),
    )


def _build_unexpected_feed_error_result(
    feed: FeedConfig,
    feed_state: FeedState,
    exc: Exception,
) -> FeedPollResult:
    checked_at = _utcnow_iso()
    error_text = _format_unexpected_feed_error(exc)
    source_states = _clone_source_states(feed_state)
    next_state = _build_error_state(
        feed,
        feed_state,
        source_states,
        checked_at=checked_at,
        http_status=None,
        error=error_text,
    )
    return FeedPollResult(
        feed=feed,
        feed_title=feed.title or feed.id,
        checked_at=checked_at,
        status="error",
        http_status=None,
        source_url=None,
        entries=[],
        new_entries=[],
        next_state=next_state,
        attempts=[],
        error=error_text,
    )


def _build_feed_error_result(
    feed: FeedConfig,
    previous_state: FeedState,
    source_states: dict[str, FeedSourceState],
    *,
    attempts: list[FeedAttemptResult],
    checked_at: str,
) -> FeedPollResult:
    source_url, http_status, error = _resolve_last_attempt_details(attempts)
    next_state = _build_error_state(
        feed,
        previous_state,
        source_states,
        checked_at=checked_at,
        http_status=http_status,
        error=error,
    )
    return FeedPollResult(
        feed=feed,
        feed_title=feed.title or feed.id,
        checked_at=checked_at,
        status="error",
        http_status=http_status,
        source_url=source_url,
        entries=[],
        new_entries=[],
        next_state=next_state,
        attempts=attempts,
        error=error,
    )


def _build_success_state(
    feed: FeedConfig,
    previous_state: FeedState,
    source_states: dict[str, FeedSourceState],
    *,
    checked_at: str,
    source_url: str,
    http_status: int | None,
    seen_ids: list[str],
) -> FeedState:
    etag, last_modified = _resolve_primary_cache(feed, previous_state, source_states)
    return FeedState(
        etag=etag,
        last_modified=last_modified,
        seen_ids=seen_ids,
        last_checked_at=checked_at,
        last_success_at=checked_at,
        last_http_status=http_status,
        last_error=None,
        last_success_source=source_url,
        sources=source_states,
    )


def _build_error_state(
    feed: FeedConfig,
    previous_state: FeedState,
    source_states: dict[str, FeedSourceState],
    *,
    checked_at: str,
    http_status: int | None,
    error: str,
) -> FeedState:
    etag, last_modified = _resolve_primary_cache(feed, previous_state, source_states)
    return FeedState(
        etag=etag,
        last_modified=last_modified,
        seen_ids=previous_state.seen_ids.copy(),
        last_checked_at=checked_at,
        last_success_at=previous_state.last_success_at,
        last_http_status=http_status,
        last_error=error,
        last_success_source=previous_state.last_success_source,
        sources=source_states,
    )


def _resolve_primary_cache(
    feed: FeedConfig,
    previous_state: FeedState,
    source_states: dict[str, FeedSourceState],
) -> tuple[str | None, str | None]:
    # 顶层 FeedState 继续保留主源缓存，兼容旧状态结构；来源级细节单独放在 sources 中。
    primary_state = source_states.get(feed.url)
    if primary_state is not None:
        return primary_state.etag, primary_state.last_modified
    return previous_state.resolve_primary_cache(feed.url)


def _clone_source_states(feed_state: FeedState) -> dict[str, FeedSourceState]:
    return {
        source_url: _clone_source_state(source_state)
        for source_url, source_state in feed_state.sources.items()
    }


def _clone_source_state(source_state: FeedSourceState) -> FeedSourceState:
    return FeedSourceState(
        etag=source_state.etag,
        last_modified=source_state.last_modified,
        last_checked_at=source_state.last_checked_at,
        last_success_at=source_state.last_success_at,
        last_http_status=source_state.last_http_status,
        last_error=source_state.last_error,
    )


def _resolve_last_attempt_details(
    attempts: list[FeedAttemptResult],
) -> tuple[str | None, int | None, str]:
    last_attempt = attempts[-1] if attempts else None
    if last_attempt is None:
        return None, None, "未知错误"
    return (
        last_attempt.source_url,
        last_attempt.http_status,
        last_attempt.error or "未知错误",
    )


def _resolve_entry_limit(feed: FeedConfig, recovered_from_error: bool) -> int:
    if not recovered_from_error:
        return feed.max_entries_per_feed
    # 恢复窗口只临时放大，并设置上限，避免热门源一次性回补过多历史文章。
    return min(
        max(feed.max_entries_per_feed * _RECOVERY_WINDOW_MULTIPLIER, 1),
        _RECOVERY_MAX_ENTRIES_CAP,
    )


def _was_previous_poll_error(feed_state: FeedState) -> bool:
    if feed_state.last_error:
        return True
    if feed_state.last_http_status is None:
        return False
    return feed_state.last_http_status not in {200, 304}


def _is_retryable_error(exc: httpx.HTTPError) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or 500 <= status_code <= 599
    return isinstance(exc, httpx.TransportError)


def _extract_host(source_url: str) -> str:
    parts = urlsplit(source_url)
    return parts.netloc.lower() or source_url


def _format_parse_error(exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return f"解析或标准化 feed 失败: {detail}"


def _format_unexpected_feed_error(exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return f"轮询 feed 失败: {detail}"


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()
