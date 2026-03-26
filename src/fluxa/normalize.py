"""RSS 条目标准化。"""

from __future__ import annotations

import calendar
import hashlib
import html
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from time import struct_time
from typing import Any

from dateutil import parser as date_parser

from fluxa.models import FeedConfig, NormalizedEntry

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def normalize_entries(
    feed: FeedConfig,
    feed_title: str,
    raw_entries: Sequence[Mapping[str, Any]],
    *,
    entry_limit: int | None = None,
) -> list[NormalizedEntry]:
    limit = entry_limit or feed.max_entries_per_feed
    entries: list[NormalizedEntry] = []
    for raw_entry in raw_entries[:limit]:
        normalized = normalize_entry(feed, feed_title, raw_entry)
        if normalized is not None:
            entries.append(normalized)
    return entries


def normalize_entry(
    feed: FeedConfig,
    feed_title: str,
    raw_entry: Mapping[str, Any],
) -> NormalizedEntry | None:
    title = _clean_text(raw_entry.get("title")) or "(无标题)"
    url = _coerce_url(raw_entry.get("link"))
    published_at = _extract_datetime(raw_entry)
    summary = _extract_summary(raw_entry)
    entry_id = _build_entry_id(raw_entry, title, url, published_at, summary)

    if not entry_id:
        return None

    return NormalizedEntry(
        feed_id=feed.id,
        feed_title=feed_title,
        entry_id=entry_id,
        title=title,
        url=url,
        published_at=published_at,
        summary=summary,
    )


def _build_entry_id(
    raw_entry: Mapping[str, Any],
    title: str,
    url: str | None,
    published_at: datetime | None,
    summary: str | None,
) -> str:
    for key in ("id", "guid", "link"):
        value = _clean_text(raw_entry.get(key))
        if value:
            return value

    seed = "|".join(
        [
            title,
            url or "",
            published_at.isoformat() if published_at else "",
            summary or "",
        ]
    ).strip("|")
    if not seed:
        return ""
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return f"sha1:{digest}"


def _extract_summary(raw_entry: Mapping[str, Any]) -> str | None:
    summary = _clean_text(raw_entry.get("summary"))
    if summary:
        return summary[:280]

    description = _clean_text(raw_entry.get("description"))
    if description:
        return description[:280]

    content = raw_entry.get("content")
    if isinstance(content, Sequence):
        for item in content:
            if isinstance(item, Mapping):
                value = _clean_text(item.get("value"))
                if value:
                    return value[:280]
    return None


def _extract_datetime(raw_entry: Mapping[str, Any]) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = raw_entry.get(key)
        normalized = _parse_struct_time(value)
        if normalized is not None:
            return normalized

    for key in ("published", "updated", "created"):
        value = raw_entry.get(key)
        normalized = _parse_datetime_text(value)
        if normalized is not None:
            return normalized

    return None


def _parse_struct_time(value: Any) -> datetime | None:
    if isinstance(value, struct_time):
        return datetime.fromtimestamp(calendar.timegm(value), tz=UTC)
    return None


def _parse_datetime_text(value: Any) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        parsed = date_parser.parse(text)
    except (OverflowError, TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _coerce_url(value: Any) -> str | None:
    text = _clean_text(value)
    return text or None


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()
