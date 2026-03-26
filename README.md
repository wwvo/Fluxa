# Fluxa

`Fluxa` 是一个基于 `uv + Python + GitHub Actions` 的轻量 RSS Digest 工具。

当前目标：

- 从仓库中的 feed 配置主动拉取 RSS / Atom
- 使用 Git 分支保存最小状态
- 按批次汇总新增文章
- 通过 `gh` 发布到 GitHub Issue

## 配置

`feeds/feeds.yml` 使用仓库内的 YAML 管理所有 RSS 源：

```yaml
defaults:
  timeout_seconds: 20
  max_entries_per_feed: 20
  max_seen_ids: 300
  enabled: true

feeds:
  - id: github-blog
    title: GitHub Blog
    url: https://github.blog/feed/
```

状态文件默认写入 `state/state.json`，后续会由独立的 `rss-state` 分支保存。

首次运行会自动进入 bootstrap 模式：抓取当前 feed，但只写入 `seen_ids`，不回补历史文章。

## 开发

```bash
uv sync
uv run fluxa --help
uv run fluxa --bootstrap-only
```
