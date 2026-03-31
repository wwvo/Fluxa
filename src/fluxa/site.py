"""静态文章站点生成。

本模块负责把 RSS 新增条目生成为静态 HTML 页面，部署到自有服务器后可绕过 CNB 外链限制。
构建逻辑复用自 DocDock 的静态站方案，模板和样式位于 templates/site/ 和 static/site/。
"""

from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt

from fluxa.models import NormalizedEntry, PublishError

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
_SITE_TITLE = "Fluxa Digest"


@dataclass(slots=True, frozen=True)
class SiteBuildResult:
    """静态站构建结果。"""

    output_dir: Path
    entry_links: dict[str, str]


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
    """为新增 RSS 条目构建静态 HTML 站点。"""

    normalized_base = normalize_base_url(base_url)
    slugs = generate_slugs(entries)

    _ensure_output_dir(output_dir)
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
    entry_links: dict[str, str] = {}
    index_entries: list[dict[str, str | None]] = []

    for entry in entries:
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

        index_entries.append({**entry_data, "page_url": page_url})

    _write_text(
        output_dir / "index.html",
        index_template.render(**template_context, entries=index_entries),
    )

    return SiteBuildResult(output_dir=output_dir, entry_links=entry_links)


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


def generate_slugs(entries: Sequence[NormalizedEntry]) -> dict[str, str]:
    """为每个条目生成唯一 slug，冲突时追加数字后缀。"""

    slug_counts: dict[str, int] = {}
    result: dict[str, str] = {}
    for entry in entries:
        base = slugify(entry.title) or _hash_slug(entry.entry_id)
        count = slug_counts.get(base, 0)
        slug_counts[base] = count + 1
        slug = base if count == 0 else f"{base}-{count + 1}"
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


def _join_site_url(site_url: str, path: str) -> str:
    return f"{site_url.rstrip('/')}{path}"


def _ensure_output_dir(output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def _copy_static_assets(static_dir: Path, output_dir: Path) -> None:
    if not static_dir.exists():
        return
    target_dir = output_dir / "assets"
    shutil.copytree(static_dir, target_dir, dirs_exist_ok=True)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
