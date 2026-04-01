"""静态 Digest 站点生成（增量模式）。

本模块负责把每次运行的 RSS 摘要 issue 生成为一个静态 HTML 页面，部署到 GitHub Pages
后可绕过 CNB 外链限制。每次运行产出一个 digest 页面，历史页面通过 manifest.json 累积保留。

构建逻辑复用自 DocDock 的静态站方案，模板和样式位于 templates/site/ 和 static/site/。
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from fluxa.models import NormalizedEntry

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
_SITE_TITLE = "Fluxa Digest"
_MANIFEST_FILE = "manifest.json"


@dataclass(slots=True, frozen=True)
class SiteBuildResult:
    """静态站构建结果。"""

    output_dir: Path
    digest_url: str
    total_count: int


def build_digest_site(
    entries: Sequence[NormalizedEntry],
    *,
    site_url: str,
    base_url: str = "/",
    output_dir: Path,
    templates_dir: Path,
    static_dir: Path,
    issue_title: str,
    issue_date: str,
    display_key: str,
    run_id: str,
    site_title: str = _SITE_TITLE,
) -> SiteBuildResult:
    """为本次运行构建一个 digest HTML 页面，追加到增量站点中。"""

    normalized_base = normalize_base_url(base_url)
    slug = _build_digest_slug(issue_date, display_key)

    # 加载已有 manifest，检查本轮是否已构建过（幂等）。
    existing_manifest = _load_manifest(output_dir)
    existing_slugs = {item["slug"] for item in existing_manifest}

    output_dir.mkdir(parents=True, exist_ok=True)
    _copy_static_assets(static_dir, output_dir)

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    digest_template = env.get_template("digest.html.j2")
    index_template = env.get_template("index.html.j2")
    template_context = _build_template_context(site_title, normalized_base)

    page_url = build_url(normalized_base, f"/doc/{slug}")
    full_url = _join_site_url(site_url, page_url)

    # 按 feed 分组条目，供模板渲染。
    grouped_entries = _group_entries_by_feed(entries)

    # 生成本轮 digest 页面（幂等：覆盖同 slug 的旧页面）。
    _write_text(
        output_dir / "doc" / slug / "index.html",
        digest_template.render(
            **template_context,
            issue_title=issue_title,
            issue_date=issue_date,
            display_key=display_key,
            run_id=run_id,
            new_count=len(entries),
            grouped_entries=grouped_entries,
        ),
    )

    # 更新 manifest：如果同 slug 已存在则更新，否则插入到最前面。
    new_item: dict[str, Any] = {
        "slug": slug,
        "issue_title": issue_title,
        "issue_date": issue_date,
        "display_key": display_key,
        "run_id": run_id,
        "new_count": len(entries),
        "page_url": page_url,
    }

    if slug in existing_slugs:
        merged_manifest = [
            new_item if item["slug"] == slug else item
            for item in existing_manifest
        ]
    else:
        merged_manifest = [new_item, *existing_manifest]

    # 用全量 manifest 重建首页索引。
    all_index_entries = _build_index_entries(merged_manifest, normalized_base)
    _write_text(
        output_dir / "index.html",
        index_template.render(**template_context, digests=all_index_entries),
    )
    _save_manifest(output_dir, merged_manifest)

    return SiteBuildResult(
        output_dir=output_dir,
        digest_url=full_url,
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

    return _SLUG_PATTERN.sub("-", value.lower()).strip("-")


def _build_digest_slug(issue_date: str, display_key: str) -> str:
    """为 digest 页面生成 slug，如 2026-04-01-08-00-10-00。"""

    raw = f"{issue_date}-{display_key}"
    return slugify(raw) or issue_date


def _group_entries_by_feed(
    entries: Sequence[NormalizedEntry],
) -> list[dict[str, Any]]:
    """按 feed 分组条目，供模板渲染。"""

    groups: dict[str, list[dict[str, str | None]]] = {}
    titles: dict[str, str] = {}

    for entry in entries:
        groups.setdefault(entry.feed_id, [])
        titles.setdefault(entry.feed_id, entry.feed_title)
        groups[entry.feed_id].append({
            "title": entry.title,
            "url": entry.url,
            "published_at": (
                entry.published_at.strftime("%Y-%m-%d %H:%M")
                if entry.published_at
                else None
            ),
            "summary": entry.summary,
        })

    return [
        {
            "feed_id": feed_id,
            "feed_title": titles[feed_id],
            "entries": feed_entries,
            "count": len(feed_entries),
        }
        for feed_id, feed_entries in groups.items()
    ]


def _build_template_context(site_title: str, base_url: str) -> dict[str, str]:
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
            "slug": item["slug"],
            "issue_title": item.get("issue_title", ""),
            "issue_date": item.get("issue_date", ""),
            "display_key": item.get("display_key", ""),
            "new_count": item.get("new_count", 0),
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
    return [item for item in raw if isinstance(item, dict) and "slug" in item]


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
