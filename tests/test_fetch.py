from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from fluxa.fetch import poll_feed, poll_feeds
from fluxa.models import FeedConfig, FeedState


def _build_feed(feed_id: str, url: str) -> FeedConfig:
    return FeedConfig(
        id=feed_id,
        url=url,
        fallback_urls=(),
        title=f"{feed_id} title",
        enabled=True,
        timeout_seconds=20,
        max_entries_per_feed=20,
        max_seen_ids=300,
    )


def _build_response(url: str, content: bytes) -> httpx.Response:
    return httpx.Response(
        200,
        content=content,
        headers={"ETag": f"etag-{url}"},
        request=httpx.Request("GET", url),
    )


class FetchIsolationTests(unittest.TestCase):
    def test_poll_feed_converts_parse_exception_to_error_result(self) -> None:
        feed = _build_feed("broken", "https://example.com/broken.xml")
        previous_state = FeedState(
            seen_ids=["entry-1"],
            last_success_at="2026-03-26T00:00:00+00:00",
            last_success_source=feed.url,
        )

        with httpx.Client() as client:
            with (
                patch(
                    "fluxa.fetch._get_with_host_limit",
                    return_value=_build_response(feed.url, b"broken"),
                ),
                patch(
                    "fluxa.fetch.feedparser.parse",
                    side_effect=ValueError("malformed payload"),
                ),
            ):
                result = poll_feed(
                    client,
                    feed,
                    previous_state,
                    bootstrap_mode=False,
                    host_limiters={},
                )

        self.assertEqual(result.status, "error")
        self.assertEqual(result.http_status, 200)
        self.assertEqual(result.source_url, feed.url)
        self.assertIn("解析或标准化 feed 失败", result.error or "")
        self.assertIn("malformed payload", result.error or "")
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.attempts[0].status, "error")
        self.assertEqual(result.attempts[0].http_status, 200)
        self.assertEqual(
            result.next_state.last_success_at, previous_state.last_success_at
        )
        self.assertEqual(result.next_state.last_error, result.error)
        self.assertEqual(result.next_state.seen_ids, previous_state.seen_ids)

    def test_poll_feeds_keeps_other_feeds_running_after_parse_exception(self) -> None:
        broken_feed = _build_feed("broken", "https://example.com/broken.xml")
        healthy_feed = _build_feed("healthy", "https://example.com/healthy.xml")

        def fake_get(
            client: httpx.Client,
            source_url: str,
            *,
            headers: dict[str, str],
            timeout: int,
            host_limiters: dict[str, object],
        ) -> httpx.Response:
            del client, headers, timeout, host_limiters
            if source_url == broken_feed.url:
                return _build_response(source_url, b"broken")
            return _build_response(source_url, b"healthy")

        def fake_parse(content: bytes) -> SimpleNamespace:
            if content == b"broken":
                raise ValueError("boom")
            return SimpleNamespace(
                feed={"title": "Healthy Feed"}, entries=[], bozo=False
            )

        with (
            patch("fluxa.fetch._get_with_host_limit", side_effect=fake_get),
            patch("fluxa.fetch.feedparser.parse", side_effect=fake_parse),
        ):
            results = poll_feeds(
                [broken_feed, healthy_feed],
                {
                    broken_feed.id: FeedState(),
                    healthy_feed.id: FeedState(),
                },
                bootstrap_mode=False,
            )

        self.assertEqual([result.feed.id for result in results], ["broken", "healthy"])
        self.assertEqual(results[0].status, "error")
        self.assertEqual(results[1].status, "ok")


if __name__ == "__main__":
    unittest.main()
