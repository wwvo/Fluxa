from __future__ import annotations

import json
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

    def test_reserved_slugs_avoided(self) -> None:
        entries = [_build_entry(entry_id="e1", title="Hello World")]
        slugs = generate_slugs(entries, reserved_slugs={"hello-world"})
        self.assertEqual(slugs["e1"], "hello-world-2")


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
            self.assertEqual(result.new_count, 2)
            self.assertEqual(result.total_count, 2)
            self.assertEqual(
                result.entry_links["e1"],
                "https://example.com/doc/first-article",
            )
            self.assertTrue((output_dir / "index.html").exists())
            self.assertTrue((output_dir / "doc" / "first-article" / "index.html").exists())
            self.assertTrue((output_dir / "doc" / "second-article" / "index.html").exists())
            self.assertTrue((output_dir / "assets" / "style.css").exists())
            self.assertTrue((output_dir / "manifest.json").exists())

            index_html = (output_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn("First Article", index_html)
            self.assertIn("Second Article", index_html)

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
            self.assertEqual(result.new_count, 0)
            self.assertEqual(result.total_count, 0)
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


class IncrementalBuildTests(unittest.TestCase):
    """验证增量构建：历史文章保留，新文章追加。"""

    def test_second_build_preserves_first_build_pages(self) -> None:
        """第二次构建应保留第一次的文章页面和 manifest 记录。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "dist"
            site_args = {
                "site_url": "https://example.com",
                "output_dir": output_dir,
                "templates_dir": Path("templates/site"),
                "static_dir": Path("static/site"),
            }

            # 第一次构建：2 篇文章
            first_entries = [
                _build_entry(entry_id="e1", title="First Article"),
                _build_entry(entry_id="e2", title="Second Article"),
            ]
            result1 = build_digest_site(first_entries, **site_args)
            self.assertEqual(result1.new_count, 2)
            self.assertEqual(result1.total_count, 2)

            # 第二次构建：1 篇新文章
            second_entries = [
                _build_entry(entry_id="e3", title="Third Article"),
            ]
            result2 = build_digest_site(second_entries, **site_args)
            self.assertEqual(result2.new_count, 1)
            self.assertEqual(result2.total_count, 3)

            # 验证所有 3 篇文章的页面都存在
            self.assertTrue((output_dir / "doc" / "first-article" / "index.html").exists())
            self.assertTrue((output_dir / "doc" / "second-article" / "index.html").exists())
            self.assertTrue((output_dir / "doc" / "third-article" / "index.html").exists())

            # 验证首页索引包含全部 3 篇
            index_html = (output_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn("First Article", index_html)
            self.assertIn("Second Article", index_html)
            self.assertIn("Third Article", index_html)

            # 验证 manifest 包含全部 3 篇
            manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(manifest), 3)
            manifest_ids = [item["entry_id"] for item in manifest]
            self.assertEqual(manifest_ids, ["e3", "e1", "e2"])

            # 验证 entry_links 也包含历史条目
            self.assertIn("e1", result2.entry_links)
            self.assertIn("e2", result2.entry_links)
            self.assertIn("e3", result2.entry_links)

    def test_duplicate_entry_is_skipped(self) -> None:
        """重复条目不应生成新页面，也不应在 manifest 中重复。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "dist"
            site_args = {
                "site_url": "https://example.com",
                "output_dir": output_dir,
                "templates_dir": Path("templates/site"),
                "static_dir": Path("static/site"),
            }

            entries = [_build_entry(entry_id="e1", title="Same Article")]
            result1 = build_digest_site(entries, **site_args)
            self.assertEqual(result1.new_count, 1)

            # 传入相同 entry_id 的条目
            result2 = build_digest_site(entries, **site_args)
            self.assertEqual(result2.new_count, 0)
            self.assertEqual(result2.total_count, 1)

            # manifest 不应有重复
            manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(manifest), 1)

    def test_slug_collision_with_existing_manifest(self) -> None:
        """新条目的 slug 和历史条目冲突时，应自动追加后缀。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "dist"
            site_args = {
                "site_url": "https://example.com",
                "output_dir": output_dir,
                "templates_dir": Path("templates/site"),
                "static_dir": Path("static/site"),
            }

            # 第一次：title="Hello World" → slug="hello-world"
            first = [_build_entry(entry_id="e1", title="Hello World")]
            build_digest_site(first, **site_args)

            # 第二次：同标题不同 entry_id → slug 应为 "hello-world-2"
            second = [_build_entry(entry_id="e2", title="Hello World")]
            result = build_digest_site(second, **site_args)

            self.assertTrue((output_dir / "doc" / "hello-world" / "index.html").exists())
            self.assertTrue((output_dir / "doc" / "hello-world-2" / "index.html").exists())
            self.assertIn("/doc/hello-world-2", result.entry_links["e2"])


if __name__ == "__main__":
    unittest.main()
