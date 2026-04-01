"""静态文章站点生成（增量模式）。

本模块负责把 RSS 新增条目生成为静态 HTML 页面，部署到自有服务器后可绕过 CNB 外链限制。
构建逻辑复用自 DocDock 的静态站方案，模板和样式位于 templates/site/ 和 static/site/。

增量策略：通过 output_dir/manifest.json 记录所有已发布文章。每次运行只为新条目生成 HTML
页面，首页索引从 manifest 重建以包含全部历史文章。已有文章的 HTML 页面不会被覆盖或删除。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt

from fluxa.models import NormalizedEntry

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
_SITE_TITLE = "Fluxa Digest"
_MANIFEST_FILE = "manifest.json"


@dataclass(slots=True, frozen=True)
class SiteBuildResult:
    """静态站构建结果。"""

    output_dir: Path
    entry_links: dict[str, str]
    new_count: int
    total_count: int


def build_digest_site(
    entries: Sequence[NormalizedEntry],
    *,
    site_url: str,
    base_url: str = "/",
    output_dir: Path,
    templates_dir: Path,
    static_dir: Path,
    site_title: str = _SITE_TITLE,
) -> SiteBuildResult:
    """增量构建静态 HTML 站点。只为新条目生成页面，保留所有历史文章。"""

    normalized_base = normalize_base_url(base_url)

    # 加载已有 manifest，跳过已发布的条目。
    existing_manifest = _load_manifest(output_dir)
    existing_entry_ids = {item["entry_id"] for item in existing_manifest}
    existing_slugs = {item["slug"] for item in existing_manifest}

    new_entries = [e for e in entries if e.entry_id not in existing_entry_ids]
    slugs = generate_slugs(new_entries, reserved_slugs=existing_slugs)

    output_dir.mkdir(parents=True, exist_ok=True)
    _copy_static_assets(static_dir, output_dir)

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    entry_template = env.get_template("entry.html.j2")
    index_template = env.get_template("index.html.j2")

    template_context = _build_template_context(site_title, normalized_base)

    # 为本轮新增条目生成 HTML 页面和 manifest 记录。
    entry_links: dict[str, str] = {}
    new_manifest_items: list[dict[str, Any]] = []

    for entry in new_entries:
        slug = slugs[entry.entry_id]
        page_url = build_url(normalized_base, f"/doc/{slug}")
        full_url = _join_site_url(site_url, page_url)
        entry_links[entry.entry_id] = full_url

        published_at = _format_datetime(entry)
        content_html = _render_summary_html(entry.summary)

        entry_data = {
            "title": entry.title,
            "url": entry.url,
            "summary": entry.summary,
            "feed_title": entry.feed_title,
            "published_at": published_at,
        }

        _write_text(
            output_dir / "doc" / slug / "index.html",
            entry_template.render(
                **template_context,
                entry=entry_data,
                content_html=content_html,
            ),
        )

        new_manifest_items.append({
            "entry_id": entry.entry_id,
            "slug": slug,
            "title": entry.title,
            "url": entry.url,
            "summary": entry.summary,
            "feed_id": entry.feed_id,
            "feed_title": entry.feed_title,
            "published_at": published_at,
            "page_url": page_url,
        })

    # 历史条目中如果有和本轮同 entry_id 的，也补上 entry_links（幂等）。
    for item in existing_manifest:
        entry_id = item["entry_id"]
        if entry_id not in entry_links:
            page_url = item.get("page_url") or build_url(
                normalized_base, f"/doc/{item['slug']}"
            )
            entry_links[entry_id] = _join_site_url(site_url, page_url)

    # 合并 manifest：新条目在前，历史在后。
    merged_manifest = new_manifest_items + existing_manifest

    # 用合并后的全量 manifest 重建首页索引。
    all_index_entries = _build_index_entries(merged_manifest, normalized_base)
    _write_text(
        output_dir / "index.html",
        index_template.render(**template_context, entries=all_index_entries),
    )

    # 持久化更新后的 manifest。
    _save_manifest(output_dir, merged_manifest)

    return SiteBuildResult(
        output_dir=output_dir,
        entry_links=entry_links,
        new_count=len(new_entries),
        total_count=len(merged_manifest),
    )


def normalize_base_url(base_url: str) -> str:
    """确保 base_url 以 / 开头，不以 / 结尾（根路径为 /）。"""

    base = base_url.strip() or "/"
    if not base.startswith("/"):
        base = f"/{base}"
    if len(base) > 1:
        base = base.rstrip("/")
    return base or "/"


def build_url(base_url: str, path: str) -> str:
    """拼接 base_url 和路径。"""

    if not path.startswith("/"):
        path = f"/{path}"
    if base_url == "/":
        return path
    return f"{base_url}{path}"


def slugify(value: str) -> str:
    """将文本转为 URL 安全的 slug。"""

    slug = _SLUG_PATTERN.sub("-", value.lower()).strip("-")
    return slug


def generate_slugs(
    entries: Sequence[NormalizedEntry],
    *,
    reserved_slugs: set[str] | None = None,
) -> dict[str, str]:
    """为每个条目生成唯一 slug，冲突时追加数字后缀。"""

    occupied = set(reserved_slugs) if reserved_slugs else set()
    slug_counts: dict[str, int] = {}
    result: dict[str, str] = {}
    for entry in entries:
        base = slugify(entry.title) or _hash_slug(entry.entry_id)
        count = slug_counts.get(base, 0)
        slug_counts[base] = count + 1
        slug = base if count == 0 else f"{base}-{count + 1}"
        # 继续递增直到不和已有 slug 冲突。
        while slug in occupied:
            count += 1
            slug_counts[base] = count
            slug = f"{base}-{count + 1}"
        occupied.add(slug)
        result[entry.entry_id] = slug
    return result


def _hash_slug(entry_id: str) -> str:
    return hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:12]


def _format_datetime(entry: NormalizedEntry) -> str | None:
    if entry.published_at is None:
        return None
    return entry.published_at.strftime("%Y-%m-%d %H:%M")


def _render_summary_html(summary: str | None) -> str:
    if not summary:
        return ""
    md = MarkdownIt("js-default")
    return cast(str, md.render(summary))


def _build_template_context(
    site_title: str,
    base_url: str,
) -> dict[str, str]:
    return {
        "site_title": site_title,
        "base_url": base_url,
        "home_url": build_url(base_url, "/"),
        "asset_url": build_url(base_url, "/assets/style.css"),
    }


def _build_index_entries(
    manifest: list[dict[str, Any]],
    base_url: str,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in manifest:
        page_url = item.get("page_url") or build_url(
            base_url, f"/doc/{item['slug']}"
        )
        entries.append({
            "title": item["title"],
            "url": item.get("url"),
            "summary": item.get("summary"),
            "feed_title": item.get("feed_title", ""),
            "published_at": item.get("published_at"),
            "page_url": page_url,
        })
    return entries


def _join_site_url(site_url: str, path: str) -> str:
    return f"{site_url.rstrip('/')}{path}"


def _load_manifest(output_dir: Path) -> list[dict[str, Any]]:
    manifest_path = output_dir / _MANIFEST_FILE
    if not manifest_path.exists():
        return []
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and "entry_id" in item]


def _save_manifest(output_dir: Path, manifest: list[dict[str, Any]]) -> None:
    manifest_path = output_dir / _MANIFEST_FILE
    content = json.dumps(manifest, ensure_ascii=False, indent=2)
    _write_text(manifest_path, f"{content}\n")


def _copy_static_assets(static_dir: Path, output_dir: Path) -> None:
    if not static_dir.exists():
        return
    target_dir = output_dir / "assets"
    shutil.copytree(static_dir, target_dir, dirs_exist_ok=True)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
