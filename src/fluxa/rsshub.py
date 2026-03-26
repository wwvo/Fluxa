"""RSSHub 公共实例与回退策略。"""

from __future__ import annotations

from urllib.parse import SplitResult, urlsplit, urlunsplit

_RSSHUB_INSTANCE_BASES = (
    "https://rsshub-balancer.virworks.moe",
    "https://rsshub.ktachibana.party",
    "https://hub.slarker.me",
    "https://rsshub.rssforever.com",
    "https://rsshub.isrss.com",
    "https://rsshub.umzzz.com",
)

_RSSHUB_HOSTS = frozenset(
    {
        "rsshub.app",
        *(urlsplit(base_url).netloc.lower() for base_url in _RSSHUB_INSTANCE_BASES),
    }
)


def resolve_fallback_urls(
    url: str,
    explicit_fallback_urls: tuple[str, ...],
) -> tuple[str, ...]:
    """为 feed 生成最终 fallback_urls。"""

    if explicit_fallback_urls:
        return _dedupe_urls(explicit_fallback_urls, exclude={url})

    split_result = _parse_managed_rsshub_url(url)
    if split_result is None:
        return ()

    # 维护时只需要关心实例池；具体 route 直接复用主源 URL 上的 path/query/fragment。
    generated_urls = [
        _join_base_and_route(base_url, split_result)
        for base_url in _RSSHUB_INSTANCE_BASES
    ]
    return _dedupe_urls(generated_urls, exclude={url})


def _parse_managed_rsshub_url(url: str) -> SplitResult | None:
    split_result = urlsplit(url)
    if split_result.scheme not in {"http", "https"}:
        return None
    if split_result.netloc.lower() not in _RSSHUB_HOSTS:
        return None
    if not split_result.path or split_result.path == "/":
        return None
    return split_result


def _join_base_and_route(base_url: str, split_result: SplitResult) -> str:
    base_split = urlsplit(base_url)
    path = (
        split_result.path
        if split_result.path.startswith("/")
        else f"/{split_result.path}"
    )
    return urlunsplit(
        (
            base_split.scheme,
            base_split.netloc,
            path,
            split_result.query,
            split_result.fragment,
        )
    )


def _dedupe_urls(
    urls: tuple[str, ...] | list[str],
    *,
    exclude: set[str] | None = None,
) -> tuple[str, ...]:
    excluded = exclude or set()
    deduped: list[str] = []
    seen: set[str] = set(excluded)
    for url in urls:
        normalized_url = url.strip()
        if not normalized_url or normalized_url in seen:
            continue
        seen.add(normalized_url)
        deduped.append(normalized_url)
    return tuple(deduped)
