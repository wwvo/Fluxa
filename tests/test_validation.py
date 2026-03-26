from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fluxa.config import load_config
from fluxa.models import AppState, ConfigError, FeedSourceState, FeedState, StateError


class StrictIntValidationTests(unittest.TestCase):
    def test_config_rejects_boolean_for_integer_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "feeds.yml"
            config_path.write_text(
                "\n".join(
                    [
                        "defaults:",
                        "  max_entries_per_feed: true",
                        "feeds: []",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigError):
                load_config(config_path)

    def test_feed_source_state_rejects_boolean_status(self) -> None:
        with self.assertRaises(StateError):
            FeedSourceState.from_dict({"last_http_status": True})

    def test_feed_state_rejects_boolean_status(self) -> None:
        with self.assertRaises(StateError):
            FeedState.from_dict(
                {
                    "seen_ids": [],
                    "last_http_status": False,
                    "sources": {},
                }
            )

    def test_app_state_rejects_boolean_schema_version(self) -> None:
        with self.assertRaises(StateError):
            AppState.from_dict({"schema_version": True, "bootstrap_completed": False})

    def test_feed_source_state_rejects_non_string_etag(self) -> None:
        with self.assertRaises(StateError):
            FeedSourceState.from_dict({"etag": 123})

    def test_feed_state_rejects_non_string_seen_id_item(self) -> None:
        with self.assertRaises(StateError):
            FeedState.from_dict(
                {
                    "seen_ids": [123],
                    "sources": {},
                }
            )

    def test_feed_state_rejects_non_string_last_success_source(self) -> None:
        with self.assertRaises(StateError):
            FeedState.from_dict(
                {
                    "seen_ids": [],
                    "last_success_source": False,
                    "sources": {},
                }
            )


class StrictStringValidationTests(unittest.TestCase):
    def test_config_rejects_non_string_feed_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "feeds.yml"
            config_path.write_text(
                "\n".join(
                    [
                        "feeds:",
                        "  - id: 123",
                        "    url: https://example.com/feed.xml",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigError):
                load_config(config_path)

    def test_config_rejects_non_string_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "feeds.yml"
            config_path.write_text(
                "\n".join(
                    [
                        "feeds:",
                        "  - id: demo",
                        "    title: false",
                        "    url: https://example.com/feed.xml",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigError):
                load_config(config_path)

    def test_config_rejects_non_string_fallback_url_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "feeds.yml"
            config_path.write_text(
                "\n".join(
                    [
                        "feeds:",
                        "  - id: demo",
                        "    url: https://example.com/feed.xml",
                        "    fallback_urls:",
                        "      - 123",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigError):
                load_config(config_path)

    def test_config_wraps_read_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "feeds.yml"
            config_path.write_text("feeds: []\n", encoding="utf-8")

            with patch("pathlib.Path.read_text", side_effect=OSError("permission")):
                with self.assertRaises(ConfigError):
                    load_config(config_path)


if __name__ == "__main__":
    unittest.main()
