# Fluxa

`Fluxa` 是一个基于 `uv + Python + GitHub Actions` 的轻量 RSS Digest 工具。

当前目标：

- 从仓库中的 feed 配置主动拉取 RSS / Atom
- 使用 Git 分支保存最小状态
- 按批次汇总新增文章
- 通过 `gh` 发布到 GitHub Issue

## 开发

```bash
uv sync
uv run fluxa --help
```

