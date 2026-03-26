"""RSS 抓取、重试与条件请求。"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
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
_RECOVERY_WINDOW_CAP = 100


@dataclass(slots=True)
class _SourcePollOutcome:
    status: str
    http_status: int | None
    error: str | None
    feed_title: str
    entries: list[NormalizedEntry]
    new_entries: list[NormalizedEntry]
    merged_seen_ids: list[str]
    source_state: FeedSourceState
    effective_max_entries_per_feed: int
    attempts: list[FeedAttemptResult]


def poll_feeds(
    feeds: Sequence[FeedConfig],
    state_by_feed: dict[str, FeedState],
    *,
    bootstrap_mode: bool,
) -> list[FeedPollResult]:
    if not feeds:
        return []

    results_by_feed: dict[str, FeedPollResult] = {}
    host_limiters = _build_host_limiters(feeds)
    max_workers = min(_MAX_WORKERS, len(feeds))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        for feed in feeds:
            previous_state = state_by_feed.get(feed.id, FeedState())
            feed_bootstrap = bootstrap_mode or previous_state.last_success_at is None
            future = executor.submit(
                poll_feed,
                feed,
                previous_state,
                bootstrap_mode=feed_bootstrap,
                host_limiters=host_limiters,
            )
            future_map[future] = feed.id

        for future, feed_id in future_map.items():
            result = future.result()
            state_by_feed[feed_id] = result.next_state
            results_by_feed[feed_id] = result

    return [results_by_feed[feed.id] for feed in feeds]


def poll_feed(
    feed: FeedConfig,
    feed_state: FeedState,
    *,
    bootstrap_mode: bool,
    host_limiters: dict[str, BoundedSemaphore],
) -> FeedPollResult:
    checked_at = _utcnow_iso()
    source_states = dict(feed_state.sources)
    attempts: list[FeedAttemptResult] = []
    recovered_from_error = _was_previously_unhealthy(feed_state)

    with httpx.Client(headers=_DEFAULT_HEADERS, follow_redirects=True) as client:
        for source_index, source_url in enumerate(feed.source_urls):
            source_outcome = _poll_source(
                client,
                feed,
                feed_state,
                source_url,
                checked_at=checked_at,
                bootstrap_mode=bootstrap_mode,
                recovering_from_error=recovered_from_error,
                allow_legacy=source_index == 0 and source_url == feed.url,
                host_limiters=host_limiters,
            )
            attempts.extend(source_outcome.attempts)
            source_states[source_url] = source_outcome.source_state

            if source_outcome.status == "error":
                continue

            next_state = FeedState(
                etag=source_outcome.source_state.etag,
                last_modified=source_outcome.source_state.last_modified,
                seen_ids=source_outcome.merged_seen_ids,
                last_checked_at=checked_at,
                last_success_at=checked_at,
                last_http_status=source_outcome.http_status,
                last_error=None,
                last_success_source=source_url,
                sources=source_states,
            )
            return FeedPollResult(
                feed=feed,
                feed_title=source_outcome.feed_title,
                checked_at=checked_at,
                status=source_outcome.status,
                http_status=source_outcome.http_status,
                entries=source_outcome.entries,
                new_entries=source_outcome.new_entries,
                next_state=next_state,
                source_url=source_url,
                used_fallback=source_url != feed.url,
                recovered_from_error=recovered_from_error,
                effective_max_entries_per_feed=source_outcome.effective_max_entries_per_feed,
                attempts=attempts,
            )

    preserved_etag, preserved_last_modified = _resolve_preserved_headers(
        feed_state,
        source_states,
    )
    final_error = next(
        (attempt.error for attempt in reversed(attempts) if attempt.error), None
    )
    final_http_status = next(
        (
            attempt.http_status
            for attempt in reversed(attempts)
            if attempt.http_status is not None
        ),
        None,
    )
    next_state = FeedState(
        etag=preserved_etag,
        last_modified=preserved_last_modified,
        seen_ids=feed_state.seen_ids.copy(),
        last_checked_at=checked_at,
        last_success_at=feed_state.last_success_at,
        last_http_status=final_http_status,
        last_error=final_error,
        last_success_source=feed_state.last_success_source,
        sources=source_states,
    )
    return FeedPollResult(
        feed=feed,
        feed_title=feed.title or feed.id,
        checked_at=checked_at,
        status="error",
        http_status=final_http_status,
        entries=[],
        new_entries=[],
        next_state=next_state,
        attempts=attempts,
        error=final_error or "未知错误",
    )


def _poll_source(
    client: httpx.Client,
    feed: FeedConfig,
    feed_state: FeedState,
    source_url: str,
    *,
    checked_at: str,
    bootstrap_mode: bool,
    recovering_from_error: bool,
    allow_legacy: bool,
    host_limiters: dict[str, BoundedSemaphore],
) -> _SourcePollOutcome:
    source_state = feed_state.get_source_state(source_url, allow_legacy=allow_legacy)
    request_headers = _build_request_headers(source_state)
    attempts: list[FeedAttemptResult] = []

    for attempt_number in range(1, _MAX_RETRIES + 2):
        try:
            with _acquire_host_slot(host_limiters, source_url):
                response = client.get(
                    source_url,
                    headers=request_headers,
                    timeout=feed.timeout_seconds,
                )

            if response.status_code == 304:
                attempts.append(
                    FeedAttemptResult(
                        source_url=source_url,
                        attempt_number=attempt_number,
                        outcome="not-modified",
                        http_status=304,
                    )
                )
                return _SourcePollOutcome(
                    status="not-modified",
                    http_status=304,
                    error=None,
                    feed_title=feed.title or feed.id,
                    entries=[],
                    new_entries=[],
                    merged_seen_ids=feed_state.seen_ids.copy(),
                    source_state=FeedSourceState(
                        etag=source_state.etag,
                        last_modified=source_state.last_modified,
                        last_checked_at=checked_at,
                        last_success_at=checked_at,
                        last_http_status=304,
                        last_error=None,
                    ),
                    effective_max_entries_per_feed=feed.max_entries_per_feed,
                    attempts=attempts,
                )

            response.raise_for_status()
            parsed = feedparser.parse(response.content)
            feed_title = feed.title or parsed.feed.get("title") or feed.id
            effective_limit = _resolve_entry_limit(
                feed,
                recovering_from_error=recovering_from_error,
            )
            entries = normalize_entries(
                feed,
                feed_title,
                parsed.entries,
                entry_limit=effective_limit,
            )
            new_entries, merged_seen_ids = compute_entry_delta(
                entries,
                feed_state.seen_ids,
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
                    outcome=status,
                    http_status=response.status_code,
                )
            )
            return _SourcePollOutcome(
                status=status,
                http_status=response.status_code,
                error=None,
                feed_title=feed_title,
                entries=entries,
                new_entries=new_entries,
                merged_seen_ids=merged_seen_ids,
                source_state=FeedSourceState(
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                    last_checked_at=checked_at,
                    last_success_at=checked_at,
                    last_http_status=response.status_code,
                    last_error=None,
                ),
                effective_max_entries_per_feed=effective_limit,
                attempts=attempts,
            )
        except httpx.HTTPError as exc:
            attempts.append(
                FeedAttemptResult(
                    source_url=source_url,
                    attempt_number=attempt_number,
                    outcome="error",
                    http_status=_extract_http_status(exc),
                    error=str(exc),
                )
            )
            if attempt_number <= _MAX_RETRIES and _is_retryable_http_error(exc):
                sleep(_retry_delay_seconds(attempt_number))
                continue
            return _build_source_error_outcome(
                feed,
                feed_state,
                source_url,
                checked_at=checked_at,
                source_state=source_state,
                error=str(exc),
                http_status=_extract_http_status(exc),
                attempts=attempts,
            )
        except (
            Exception
        ) as exc:  # pragma: no cover - 防止单个 feed 的解析异常中断整轮任务
            attempts.append(
                FeedAttemptResult(
                    source_url=source_url,
                    attempt_number=attempt_number,
                    outcome="error",
                    error=str(exc),
                )
            )
            return _build_source_error_outcome(
                feed,
                feed_state,
                source_url,
                checked_at=checked_at,
                source_state=source_state,
                error=str(exc),
                http_status=None,
                attempts=attempts,
            )

    return _build_source_error_outcome(
        feed,
        feed_state,
        source_url,
        checked_at=checked_at,
        source_state=source_state,
        error="未知错误",
        http_status=None,
        attempts=attempts,
    )


def _build_source_error_outcome(
    feed: FeedConfig,
    feed_state: FeedState,
    source_url: str,
    *,
    checked_at: str,
    source_state: FeedSourceState,
    error: str,
    http_status: int | None,
    attempts: list[FeedAttemptResult],
) -> _SourcePollOutcome:
    return _SourcePollOutcome(
        status="error",
        http_status=http_status,
        error=error,
        feed_title=feed.title or feed.id,
        entries=[],
        new_entries=[],
        merged_seen_ids=feed_state.seen_ids.copy(),
        source_state=FeedSourceState(
            etag=source_state.etag,
            last_modified=source_state.last_modified,
            last_checked_at=checked_at,
            last_success_at=source_state.last_success_at,
            last_http_status=http_status,
            last_error=error,
        ),
        effective_max_entries_per_feed=feed.max_entries_per_feed,
        attempts=attempts,
    )


def _build_request_headers(source_state: FeedSourceState) -> dict[str, str]:
    request_headers: dict[str, str] = {}
    if source_state.etag:
        request_headers["If-None-Match"] = source_state.etag
    if source_state.last_modified:
        request_headers["If-Modified-Since"] = source_state.last_modified
    return request_headers


def _build_host_limiters(feeds: Sequence[FeedConfig]) -> dict[str, BoundedSemaphore]:
    hosts = {_host_key(source_url) for feed in feeds for source_url in feed.source_urls}
    return {host: BoundedSemaphore(_MAX_PER_HOST) for host in hosts}


def _resolve_preserved_headers(
    feed_state: FeedState,
    source_states: dict[str, FeedSourceState],
) -> tuple[str | None, str | None]:
    if (
        feed_state.last_success_source
        and feed_state.last_success_source in source_states
    ):
        previous_source_state = source_states[feed_state.last_success_source]
        return previous_source_state.etag, previous_source_state.last_modified
    return feed_state.etag, feed_state.last_modified


def _resolve_entry_limit(
    feed: FeedConfig,
    *,
    recovering_from_error: bool,
) -> int:
    if not recovering_from_error:
        return feed.max_entries_per_feed
    return min(
        feed.max_entries_per_feed * _RECOVERY_WINDOW_MULTIPLIER, _RECOVERY_WINDOW_CAP
    )


def _was_previously_unhealthy(feed_state: FeedState) -> bool:
    if feed_state.last_error:
        return True
    if feed_state.last_http_status is None:
        return False
    return feed_state.last_http_status not in {200, 304}


def _is_retryable_http_error(exc: httpx.HTTPError) -> bool:
    http_status = _extract_http_status(exc)
    if http_status is not None:
        return http_status == 429 or 500 <= http_status < 600
    return isinstance(exc, httpx.TransportError)


def _extract_http_status(exc: httpx.HTTPError) -> int | None:
    response = getattr(exc, "response", None)
    return response.status_code if response is not None else None


def _retry_delay_seconds(attempt_number: int) -> float:
    return _RETRY_BACKOFF_SECONDS * attempt_number


def _host_key(source_url: str) -> str:
    split_result = urlsplit(source_url)
    return split_result.netloc or source_url


@contextmanager
def _acquire_host_slot(
    host_limiters: dict[str, BoundedSemaphore],
    source_url: str,
):
    limiter = host_limiters[_host_key(source_url)]
    limiter.acquire()
    try:
        yield
    finally:
        limiter.release()


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()
