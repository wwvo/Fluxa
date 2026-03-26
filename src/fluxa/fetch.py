"""RSS 抓取与条件请求。"""

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
                    poll_feed,
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
    effective_entry_limit = _resolve_entry_limit(feed, recovered_from_error)
    source_states = _clone_source_states(feed_state)
    attempts: list[FeedAttemptResult] = []

    for source_url in _resolve_source_urls(feed, feed_state):
        source_state = source_states.get(source_url)
        if source_state is None:
            source_state = _clone_source_state(
                feed_state.get_source_state(source_url, primary_url=feed.url)
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
            next_state = _build_success_state(
                feed,
                feed_state,
                source_states,
                checked_at=checked_at,
                source_url=source_url,
                http_status=source_result.http_status,
                seen_ids=source_result.next_seen_ids or feed_state.seen_ids.copy(),
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

    last_attempt = attempts[-1] if attempts else None
    next_state = _build_error_state(
        feed,
        feed_state,
        source_states,
        checked_at=checked_at,
        http_status=last_attempt.http_status if last_attempt is not None else None,
        error=last_attempt.error if last_attempt is not None else "未知错误",
    )
    return FeedPollResult(
        feed=feed,
        feed_title=feed.title or feed.id,
        checked_at=checked_at,
        status="error",
        http_status=last_attempt.http_status if last_attempt is not None else None,
        source_url=last_attempt.source_url if last_attempt is not None else None,
        entries=[],
        new_entries=[],
        next_state=next_state,
        attempts=attempts,
        error=last_attempt.error if last_attempt is not None else "未知错误",
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
            parsed = feedparser.parse(response.content)
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
                sleep(_RETRY_BACKOFF_SECONDS * attempt_number)
                continue
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
                    last_error=error_text,
                ),
                attempts=attempts,
                error=error_text,
            )

    return _SourcePollResult(
        source_url=source_url,
        status="error",
        http_status=None,
        next_source_state=FeedSourceState(
            etag=source_state.etag,
            last_modified=source_state.last_modified,
            last_checked_at=checked_at,
            last_success_at=source_state.last_success_at,
            last_http_status=None,
            last_error="未知错误",
        ),
        attempts=attempts,
        error="未知错误",
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


def _resolve_source_urls(
    feed: FeedConfig,
    feed_state: FeedState,
) -> tuple[str, ...]:
    ordered_urls: list[str] = []
    seen_urls: set[str] = set()

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


def _resolve_entry_limit(feed: FeedConfig, recovered_from_error: bool) -> int:
    if not recovered_from_error:
        return feed.max_entries_per_feed
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


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()
