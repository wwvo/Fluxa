from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from fluxa.models import NormalizedEntry
from fluxa.site import (
    SiteBuildResult,
    build_digest_site,
    build_url,
    generate_slugs,
    normalize_base_url,
    slugify,
)


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

    def test_slugify_strips_leading_trailing_dashes(self) -> None:
        self.assertEqual(slugify("---hello---"), "hello")


class GenerateSlugsTests(unittest.TestCase):
    def test_unique_slugs(self) -> None:
        entries = [
            _build_entry(entry_id="e1", title="First Article"),
            _build_entry(entry_id="e2", title="Second Article"),
        ]
        slugs = generate_slugs(entries)
        self.assertEqual(slugs["e1"], "first-article")
        self.assertEqual(slugs["e2"], "second-article")

    def test_collision_handling(self) -> None:
        entries = [
            _build_entry(entry_id="e1", title="Same Title"),
            _build_entry(entry_id="e2", title="Same Title"),
        ]
        slugs = generate_slugs(entries)
        self.assertEqual(slugs["e1"], "same-title")
        self.assertEqual(slugs["e2"], "same-title-2")

    def test_cjk_fallback_to_hash(self) -> None:
        entries = [_build_entry(entry_id="e1", title="中文标题")]
        slugs = generate_slugs(entries)
        self.assertEqual(len(slugs["e1"]), 12)


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
    def test_end_to_end_build(self) -> None:
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
            )

            self.assertIsInstance(result, SiteBuildResult)
            self.assertEqual(len(result.entry_links), 2)
            self.assertEqual(
                result.entry_links["e1"],
                "https://example.com/doc/first-article",
            )
            self.assertTrue((output_dir / "index.html").exists())
            self.assertTrue((output_dir / "doc" / "first-article" / "index.html").exists())
            self.assertTrue((output_dir / "doc" / "second-article" / "index.html").exists())
            self.assertTrue((output_dir / "assets" / "style.css").exists())

            index_html = (output_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn("First Article", index_html)
            self.assertIn("Second Article", index_html)

            entry_html = (output_dir / "doc" / "first-article" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("First Article", entry_html)
            self.assertIn("Demo Feed", entry_html)

    def test_empty_entries_builds_index_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "dist"
            result = build_digest_site(
                [],
                site_url="https://example.com",
                output_dir=output_dir,
                templates_dir=Path("templates/site"),
                static_dir=Path("static/site"),
            )

            self.assertEqual(result.entry_links, {})
            self.assertTrue((output_dir / "index.html").exists())

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
            )

            self.assertEqual(
                result.entry_links["e1"],
                "https://example.com/preview/doc/hello-world",
            )


if __name__ == "__main__":
    unittest.main()
