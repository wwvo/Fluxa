from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from fluxa.models import NormalizedEntry
from fluxa.site import SiteBuildResult, build_digest_site, build_url, normalize_base_url, slugify


def _build_entry(
    entry_id: str = "entry-1",
    title: str = "Test Article",
    feed_id: str = "demo",
    feed_title: str = "Demo Feed",
    url: str | None = "https://example.com/article",
    summary: str | None = "这是一篇测试文章的摘要。",
) -> NormalizedEntry:
    return NormalizedEntry(
        feed_id=feed_id,
        feed_title=feed_title,
        entry_id=entry_id,
        title=title,
        url=url,
        published_at=datetime(2026, 4, 1, 8, 0, 0, tzinfo=UTC),
        summary=summary,
    )


class SlugifyTests(unittest.TestCase):
    def test_slugify_latin_text(self) -> None:
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_slugify_with_special_characters(self) -> None:
        self.assertEqual(slugify("Hello, World! (2026)"), "hello-world-2026")

    def test_slugify_cjk_returns_empty(self) -> None:
        self.assertEqual(slugify("中文标题"), "")

    def test_slugify_mixed_text(self) -> None:
        self.assertEqual(slugify("React 18 新特性"), "react-18")


class NormalizeBaseUrlTests(unittest.TestCase):
    def test_root(self) -> None:
        self.assertEqual(normalize_base_url("/"), "/")

    def test_empty(self) -> None:
        self.assertEqual(normalize_base_url(""), "/")

    def test_subpath(self) -> None:
        self.assertEqual(normalize_base_url("/preview/"), "/preview")

    def test_missing_leading_slash(self) -> None:
        self.assertEqual(normalize_base_url("preview"), "/preview")


class BuildUrlTests(unittest.TestCase):
    def test_root_base(self) -> None:
        self.assertEqual(build_url("/", "/doc/hello"), "/doc/hello")

    def test_subpath_base(self) -> None:
        self.assertEqual(build_url("/preview", "/doc/hello"), "/preview/doc/hello")


class BuildDigestSiteTests(unittest.TestCase):
    def test_end_to_end_build_creates_one_digest_page(self) -> None:
        entries = [
            _build_entry(entry_id="e1", title="First Article"),
            _build_entry(entry_id="e2", title="Second Article"),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "dist"
            result = build_digest_site(
                entries,
                site_url="https://example.com",
                output_dir=output_dir,
                templates_dir=Path("templates/site"),
                static_dir=Path("static/site"),
                issue_title="Fluxa Digest | 2026-04-01 | run run-123",
                issue_date="2026-04-01",
                display_key="08:00-10:00",
                run_id="run-123",
            )

            self.assertIsInstance(result, SiteBuildResult)
            self.assertEqual(result.digest_url, "https://example.com/doc/2026-04-01-08-00-10-00/")
            self.assertEqual(result.total_count, 1)
            self.assertTrue((output_dir / "index.html").exists())
            self.assertTrue((output_dir / "doc" / "2026-04-01-08-00-10-00" / "index.html").exists())
            self.assertTrue((output_dir / "assets" / "style.css").exists())
            self.assertTrue((output_dir / "manifest.json").exists())

            index_html = (output_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn("2026-04-01", index_html)
            self.assertIn("08:00-10:00", index_html)

            digest_html = (output_dir / "doc" / "2026-04-01-08-00-10-00" / "index.html").read_text(encoding="utf-8")
            self.assertIn("First Article", digest_html)
            self.assertIn("Second Article", digest_html)
            self.assertIn("Demo Feed", digest_html)

    def test_subpath_base_url(self) -> None:
        entries = [_build_entry(entry_id="e1", title="Hello World")]

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "dist"
            result = build_digest_site(
                entries,
                site_url="https://example.com",
                base_url="/preview",
                output_dir=output_dir,
                templates_dir=Path("templates/site"),
                static_dir=Path("static/site"),
                issue_title="Fluxa Digest | 2026-04-01 | run run-123",
                issue_date="2026-04-01",
                display_key="08:00-10:00",
                run_id="run-123",
            )

            self.assertEqual(
                result.digest_url,
                "https://example.com/preview/doc/2026-04-01-08-00-10-00/",
            )


class IncrementalBuildTests(unittest.TestCase):
    def test_second_build_preserves_first_digest_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "dist"
            common = {
                "site_url": "https://example.com",
                "output_dir": output_dir,
                "templates_dir": Path("templates/site"),
                "static_dir": Path("static/site"),
            }

            result1 = build_digest_site(
                [_build_entry(entry_id="e1", title="First Article")],
                issue_title="Fluxa Digest | 2026-04-01 | run run-1",
                issue_date="2026-04-01",
                display_key="08:00-10:00",
                run_id="run-1",
                **common,
            )
            self.assertEqual(result1.total_count, 1)

            result2 = build_digest_site(
                [_build_entry(entry_id="e2", title="Second Article")],
                issue_title="Fluxa Digest | 2026-04-01 | run run-2",
                issue_date="2026-04-01",
                display_key="10:00-12:00",
                run_id="run-2",
                **common,
            )
            self.assertEqual(result2.total_count, 2)

            self.assertTrue((output_dir / "doc" / "2026-04-01-08-00-10-00" / "index.html").exists())
            self.assertTrue((output_dir / "doc" / "2026-04-01-10-00-12-00" / "index.html").exists())

            index_html = (output_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn("08:00-10:00", index_html)
            self.assertIn("10:00-12:00", index_html)

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest), 2)
            self.assertEqual([item["slug"] for item in manifest], [
                "2026-04-01-10-00-12-00",
                "2026-04-01-08-00-10-00",
            ])

    def test_same_window_overwrites_same_digest_slug(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "dist"
            common = {
                "site_url": "https://example.com",
                "output_dir": output_dir,
                "templates_dir": Path("templates/site"),
                "static_dir": Path("static/site"),
                "issue_date": "2026-04-01",
                "display_key": "08:00-10:00",
            }

            build_digest_site(
                [_build_entry(entry_id="e1", title="First Article")],
                issue_title="Fluxa Digest | 2026-04-01 | run run-1",
                run_id="run-1",
                **common,
            )
            result = build_digest_site(
                [_build_entry(entry_id="e2", title="Updated Article")],
                issue_title="Fluxa Digest | 2026-04-01 | run run-2",
                run_id="run-2",
                **common,
            )

            self.assertEqual(result.total_count, 1)
            digest_html = (output_dir / "doc" / "2026-04-01-08-00-10-00" / "index.html").read_text(encoding="utf-8")
            self.assertIn("Updated Article", digest_html)
            self.assertNotIn("First Article", digest_html)

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest), 1)
            self.assertEqual(manifest[0]["run_id"], "run-2")


if __name__ == "__main__":
    unittest.main()
