"""RSS 抓取与条件请求。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import feedparser
import httpx

from fluxa.diff import compute_entry_delta
from fluxa.models import FeedConfig, FeedPollResult, FeedState
from fluxa.normalize import normalize_entries

_DEFAULT_HEADERS = {
    "User-Agent": "Fluxa/0.1 (+https://github.com/)",
    "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
}


def poll_feeds(
    feeds: Sequence[FeedConfig],
    state_by_feed: dict[str, FeedState],
    *,
    bootstrap_mode: bool,
) -> list[FeedPollResult]:
    results: list[FeedPollResult] = []
    with httpx.Client(headers=_DEFAULT_HEADERS, follow_redirects=True) as client:
        for feed in feeds:
            previous_state = state_by_feed.get(feed.id, FeedState())
            feed_bootstrap = bootstrap_mode or previous_state.last_success_at is None
            result = poll_feed(
                client,
                feed,
                previous_state,
                bootstrap_mode=feed_bootstrap,
            )
            state_by_feed[feed.id] = result.next_state
            results.append(result)
    return results


def poll_feed(
    client: httpx.Client,
    feed: FeedConfig,
    feed_state: FeedState,
    *,
    bootstrap_mode: bool,
) -> FeedPollResult:
    checked_at = _utcnow_iso()
    request_headers = {}
    if feed_state.etag:
        request_headers["If-None-Match"] = feed_state.etag
    if feed_state.last_modified:
        request_headers["If-Modified-Since"] = feed_state.last_modified

    try:
        response = client.get(
            feed.url,
            headers=request_headers,
            timeout=feed.timeout_seconds,
        )
        if response.status_code == 304:
            next_state = FeedState(
                etag=feed_state.etag,
                last_modified=feed_state.last_modified,
                seen_ids=feed_state.seen_ids.copy(),
                last_checked_at=checked_at,
                last_success_at=checked_at,
                last_http_status=304,
                last_error=None,
            )
            return FeedPollResult(
                feed=feed,
                feed_title=feed.title or feed.id,
                checked_at=checked_at,
                status="not-modified",
                http_status=304,
                entries=[],
                new_entries=[],
                next_state=next_state,
            )

        response.raise_for_status()
        parsed = feedparser.parse(response.content)
        feed_title = feed.title or parsed.feed.get("title") or feed.id
        entries = normalize_entries(feed, feed_title, parsed.entries)
        new_entries, merged_seen_ids = compute_entry_delta(
            entries,
            feed_state.seen_ids,
            max_seen_ids=feed.max_seen_ids,
            suppress_new_entries=bootstrap_mode,
        )
        next_state = FeedState(
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            seen_ids=merged_seen_ids,
            last_checked_at=checked_at,
            last_success_at=checked_at,
            last_http_status=response.status_code,
            last_error=None,
        )
        status = "ok"
        if parsed.bozo and not entries:
            status = "parse-warning"
        return FeedPollResult(
            feed=feed,
            feed_title=feed_title,
            checked_at=checked_at,
            status=status,
            http_status=response.status_code,
            entries=entries,
            new_entries=new_entries,
            next_state=next_state,
        )
    except httpx.HTTPError as exc:
        response = getattr(exc, "response", None)
        next_state = FeedState(
            etag=feed_state.etag,
            last_modified=feed_state.last_modified,
            seen_ids=feed_state.seen_ids.copy(),
            last_checked_at=checked_at,
            last_success_at=feed_state.last_success_at,
            last_http_status=response.status_code if response is not None else None,
            last_error=str(exc),
        )
        return FeedPollResult(
            feed=feed,
            feed_title=feed.title or feed.id,
            checked_at=checked_at,
            status="error",
            http_status=response.status_code if response is not None else None,
            entries=[],
            new_entries=[],
            next_state=next_state,
            error=str(exc),
        )


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()
