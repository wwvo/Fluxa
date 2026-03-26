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

## 工作流

仓库内置 `.github/workflows/rss-digest.yml`：

- `main` 分支保存代码、模板和 `feeds/feeds.yml`
- `rss-state` 分支只保存 `state/state.json`
- GitHub Actions 每 2 小时执行一次
- 检测到新增文章后，会为该轮执行直接创建一个独立 issue
- 同一次 workflow 重跑会按 `run_id` 更新同一个 issue，避免重复发
- 首次运行只 bootstrap，不会回补历史文章

## 开发

```bash
uv sync
uv run fluxa --help
uv run fluxa --bootstrap-only
uv run fluxa --repo owner/repo --run-id local-test --dry-run
```
