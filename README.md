<div align="center">

# Fluxa
*使用 Git 仓库与 GitHub Actions 驱动的轻量 RSS Digest 发布器*

[![CNB Repo](https://img.shields.io/badge/CNB-wwvo%2FFluxa-2F80ED?style=flat-square&logo=cloudnativebuild&logoColor=white)](https://cnb.cool/wwvo/Fluxa)
[![GitHub Repo](https://img.shields.io/badge/GitHub-wwvo%2FFluxa-181717?style=flat-square&logo=github&logoColor=white)](https://github.com/wwvo/Fluxa)
[![RSS Digest](https://img.shields.io/github/actions/workflow/status/wwvo/Fluxa/rss-digest.yml?style=flat-square&label=RSS%20Digest)](https://github.com/wwvo/Fluxa/actions/workflows/rss-digest.yml)
[![Python](https://img.shields.io/badge/python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/badge/uv-managed-4B5563?style=flat-square)](https://docs.astral.sh/uv/)

[核心特性](#核心特性) | [快速开始](#快速开始) | [使用方法](#使用方法) | [配置说明](#配置说明) | [FAQ](#faq--常见问题)

</div>

`Fluxa` 的目标很明确：定时主动拉取一组 RSS / Atom feed，把本轮新增文章汇总成一篇 Markdown，然后发布到 Issue。它不依赖数据库，状态直接保存在仓库自己的 `rss-state` 分支里，适合“配置即仓库、状态也尽量 Git 化”的轻量部署方式。

## 核心特性

- 使用 `feeds/feeds.yml` 管理全部 feed，适合 100+ 源的仓库式维护。
- 使用 `uv + Python 3.12`，依赖少，命令简单，本地与 GitHub Actions 运行方式一致。
- 使用 `state/state.json` 保存最小状态，不需要单独引入数据库。
- 首次运行自动进入 bootstrap，只建立 `seen_ids`，不回补历史文章。
- 支持 `ETag / Last-Modified` 条件请求，减少不必要的全量拉取。
- 内建 RSSHub fallback 策略，主源失败后可自动切换备用实例。
- 对 `406 / 415` 协商类错误会自动做一次“宽松请求头”重试。
- 某个 feed 从失败恢复时，会临时放大抓取窗口，尽量补回停机期间漏掉的文章。
- 支持 `github / cnb` 双发布后端：GitHub 使用 `gh`，CNB 使用 `cnb-rs`。
- issue 标题默认使用当前 2 小时抓取窗口，例如 `08:00-10:00`，比纯数字执行 ID 更易读。
- 同一次调度重跑会命中同一个 `run_id` 对应的 issue，避免重复创建。
- 同时提供 GitHub Actions 与 CNB 云原生构建流水线，方便按仓库托管位置选择运行环境。

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 查看 CLI 参数

```bash
uv run fluxa --help
```

### 3. 首次初始化状态

```bash
uv run fluxa --bootstrap-only
```

这一步会读取 `feeds/feeds.yml` 中的 feed，并把当前看到的条目标记为已见，但不会发布任何历史 issue。

### 4. 本地演练完整流程

```bash
uv run fluxa --repo wwvo/Fluxa --run-id local-test --dry-run
uv run fluxa --publisher cnb --repo wwvo/Issuo --run-id local-test --dry-run
```

`--dry-run` 会完整执行“加载配置 -> 拉取 feed -> 生成汇总 -> 渲染 issue”，但不会真正写入 issue，也不会保存状态文件。

> [!TIP]
> 真正执行发布前，先确认本机对应的 CLI 已登录：
> GitHub 发布用 `gh auth status`，CNB 发布用 `cnb-rs auth status` 或本地 `~/.cnb/config.toml` 登录态。

## 使用方法

### 常用命令

```bash
uv run fluxa --bootstrap-only
uv run fluxa --state-path state/state.json --repo wwvo/Fluxa --run-id local-test
uv run fluxa --state-path state/state.json --repo wwvo/Fluxa --run-id local-test --dry-run
uv run fluxa --publisher cnb --state-path state/state.json --repo wwvo/Issuo --run-id local-test
```

### 主要参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--config` | `feeds/feeds.yml` | feed 配置文件路径 |
| `--state-path` | `state/state.json` | 状态文件路径 |
| `--bootstrap-only` | `false` | 仅初始化状态，不发布 issue |
| `--publisher` | `github` | 发布后端，`github` 使用 `gh`，`cnb` 使用 `cnb-rs` |
| `--repo` | 环境变量推导 | issue 目标仓库，格式为 `OWNER/REPO` |
| `--templates-dir` | `templates` | Markdown 模板目录 |
| `--timezone` | `Asia/Shanghai` | issue 标题日期和展示时间使用的时区 |
| `--run-id` | 环境变量推导 | 本轮执行的幂等标识 |
| `--dry-run` | `false` | 演练模式，不保存状态也不发布 issue |

### 一次完整运行的主链路

1. `src/fluxa/config.py` 读取并校验 `feeds/feeds.yml`。
2. `src/fluxa/state.py` 读取 `state/state.json`。
3. `src/fluxa/runner.py` 决定是否进入 bootstrap，并触发轮询。
4. `src/fluxa/fetch.py` 并发抓取 feed，处理条件请求、fallback、retry 和差量计算。
5. `src/fluxa/render.py` 使用 Jinja2 将本轮结果渲染成 Markdown。
6. `src/fluxa/publish.py` 根据发布后端调用 `gh` 或 `cnb-rs` 创建 / 更新 issue。
7. `src/fluxa/state.py` 保存最新状态，供下一轮继续增量抓取。

## 技术栈

- Python `3.12`
- `uv`
- `httpx`
- `feedparser`
- `jinja2`
- `python-dateutil`
- `pyyaml`
- GitHub Actions
- GitHub CLI `gh`
- CNB 云原生构建流水线
- CNB CLI `cnb-rs`

## 架构概览

```text
feeds/feeds.yml
  -> load_config()
state/state.json
  -> load_state()
run_cycle()
  -> poll_feeds()
  -> normalize_entries()
  -> compute_entry_delta()
  -> render_run_issue()
  -> gh / cnb-rs issue
  -> save_state()
```

整个项目分成四层：

- 配置层：把 YAML 配置转成强类型对象。
- 抓取层：负责 HTTP、条件请求、并发、fallback、重试与去重。
- 展示层：把 `RunSummary` 转成 issue 模板需要的数据。
- 发布层：根据后端选择 `gh` 或 `cnb-rs` 写入 issue。

## 项目结构

```text
Fluxa/
├─ .cnb.yml
├─ .cnb/
│  ├─ web_trigger.yml
│  └─ workflows/sync-rss.yml
├─ .github/workflows/rss-digest.yml
├─ feeds/feeds.yml
├─ state/state.json
├─ templates/run_issue.md.j2
├─ src/fluxa/
│  ├─ main.py
│  ├─ config.py
│  ├─ runner.py
│  ├─ fetch.py
│  ├─ normalize.py
│  ├─ diff.py
│  ├─ render.py
│  ├─ publish.py
│  ├─ rsshub.py
│  └─ state.py
└─ tests/
```

### 关键目录职责

| 路径 | 作用 |
| --- | --- |
| `feeds/feeds.yml` | feed 列表和默认抓取参数 |
| `state/state.json` | 本地或状态分支中的持久化状态 |
| `templates/run_issue.md.j2` | 汇总 issue 的 Markdown 模板 |
| `src/fluxa/main.py` | CLI 入口与总控流程 |
| `src/fluxa/fetch.py` | RSS 抓取、fallback、重试、状态更新 |
| `src/fluxa/publish.py` | `gh / cnb-rs` 发布与 issue 幂等更新 |
| `src/fluxa/rsshub.py` | RSSHub 公共实例池与自动 fallback 规则 |
| `tests/` | 单元测试 |

## 配置说明

`Fluxa` 默认使用仓库中的 `feeds/feeds.yml` 作为唯一 feed 配置源。

### 配置示例

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

  - id: github-trending-weekly
    title: GitHub Trending 周榜
    url: https://rsshub.rssforever.com/github/trending/weekly/any
```

### `defaults` 字段

| 字段 | 说明 |
| --- | --- |
| `timeout_seconds` | 单个 feed 请求超时时间 |
| `max_entries_per_feed` | 单轮最多读取多少条原始 entry |
| `max_seen_ids` | 每个 feed 最多保留多少个已见条目 ID |
| `enabled` | 默认是否启用该 feed |

### `feeds` 字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | feed 的唯一标识，状态文件以它作为 key |
| `title` | 否 | 自定义展示标题；缺省时使用 feed 自带标题 |
| `url` | 是 | 主源 URL |
| `enabled` | 否 | 是否启用该 feed |
| `timeout_seconds` | 否 | 覆盖默认超时 |
| `max_entries_per_feed` | 否 | 覆盖默认单轮抓取窗口 |
| `max_seen_ids` | 否 | 覆盖默认已见记录上限 |
| `fallback_urls` | 否 | 手写备用源；仅在需要特殊覆盖时使用 |

> [!NOTE]
> 对于受管 RSSHub 源，不必在 `feeds.yml` 中为每个 route 手写一长串 `fallback_urls`。`src/fluxa/rsshub.py` 会根据主源 URL 的 route 自动拼出一组公共实例备用地址。

## 状态存储与 `rss-state` 分支

`Fluxa` 默认不引入数据库，而是把状态放进 `state/state.json`，并通过独立的 `rss-state` 分支持久化。

### 分支分工

| 分支 | 内容 |
| --- | --- |
| `main` | 代码、工作流、模板、`feeds/feeds.yml` |
| `rss-state` | 仅保存 `state/state.json` |

### 状态文件职责

状态文件负责回答三个问题：

- 这个 feed 是否已经 bootstrap 过。
- 哪些条目已经见过，不应重复发 issue。
- 某个来源 URL 上次成功时的 `ETag / Last-Modified / HTTP 状态 / 错误信息` 是什么。

### 状态结构示意

```json
{
  "schema_version": 1,
  "bootstrap_completed": true,
  "feeds": {
    "github-blog": {
      "etag": "\"abc\"",
      "last_modified": "Wed, 01 Jan 2025 00:00:00 GMT",
      "seen_ids": ["entry-1", "entry-2"],
      "last_checked_at": "2026-03-27T10:00:00+00:00",
      "last_success_at": "2026-03-27T10:00:00+00:00",
      "last_http_status": 200,
      "last_error": null,
      "last_success_source": "https://github.blog/feed/",
      "sources": {
        "https://github.blog/feed/": {
          "etag": "\"abc\"",
          "last_modified": "Wed, 01 Jan 2025 00:00:00 GMT",
          "last_checked_at": "2026-03-27T10:00:00+00:00",
          "last_success_at": "2026-03-27T10:00:00+00:00",
          "last_http_status": 200,
          "last_error": null
        }
      }
    }
  }
}
```

> [!NOTE]
> 顶层 `etag / last_modified` 主要用于兼容旧状态结构；真正细粒度的缓存状态按来源 URL 保存在 `sources` 中，避免不同 RSSHub 实例错误复用缓存头。

## 抓取策略

这一节是理解 `Fluxa` 工作方式的关键。

### 1. bootstrap

如果是首次运行，或者显式传入 `--bootstrap-only`，项目会进入 bootstrap 模式：

- 读取 feed 当前可见的文章。
- 仅更新 `seen_ids`。
- 不发布历史文章。

这样可以避免仓库第一次部署时把所有旧内容一次性冲进 issue。

### 2. 并发与限流

抓取层使用 `ThreadPoolExecutor` 并发执行：

- 全局最大 worker 数：`6`
- 单个 host 最大并发：`2`

这能提升总吞吐，同时避免把某个 RSSHub 实例或单站点瞬时打爆。

### 3. 条件请求与 `200 / 304`

每个来源 URL 都会单独保存 `ETag / Last-Modified`，下一轮请求时优先发送条件头。

| 状态码 | 含义 | Fluxa 的处理 |
| --- | --- | --- |
| `200 OK` | 服务端返回了实际内容 | 解析 feed、标准化 entry、做差量、更新缓存头 |
| `304 Not Modified` | 服务端确认内容没有变化 | 不下载新内容，不生成新文章，但会更新最近检查时间与成功状态 |

`304` 的价值在于节省流量、加快轮询，同时让“没有变化”与“抓取失败”明显区分开。

### 4. `406 / 415` 协商失败恢复

有些 feed 或代理实例会对 `Accept`、`If-None-Match`、`If-Modified-Since` 比较敏感，可能返回：

- `406 Not Acceptable`
- `415 Unsupported Media Type`

遇到这类状态时，`Fluxa` 会自动进行一次“宽松请求头重试”：

1. 去掉 `If-None-Match` 和 `If-Modified-Since`
2. 把 `Accept` 改成 `*/*`
3. 再请求一次同一来源

如果第二次仍然失败：

- 本轮会记为失败
- 该来源的缓存头会被清空
- 等下一轮再用“无条件请求”重新建立缓存

这能修复不少“源其实是好的，但协商方式不兼容”的场景。

### 5. 普通重试策略

除了 `406 / 415` 的特殊处理，以下错误会按退避方式重试：

- 网络传输错误
- `429 Too Many Requests`
- `5xx` 服务端错误

当前默认最多重试 2 次，总共 3 次请求机会。

### 6. fallback 与 RSSHub 实例切换

每个 feed 最终都会展开成一个来源列表：

- 优先尝试上次成功的来源
- 再尝试主源
- 如果是受管 RSSHub 路由，再自动追加公共实例池

因此某个 RSSHub 实例暂时挂掉时，只要同一路由在其他实例上可用，本轮仍可能恢复成功。

### 7. 恢复窗口放大

如果某个 feed 上一轮是失败状态，而这一轮重新恢复成功：

- `Fluxa` 会临时把 `max_entries_per_feed` 放大到原来的 `5` 倍
- 并设置上限为 `100`

这不是常态抓取窗口，而是为了尽量补回停机期间漏掉的新文章。

## GitHub Actions 工作流

项目内置工作流文件：[`.github/workflows/rss-digest.yml`](./.github/workflows/rss-digest.yml)

### 默认行为

- 触发方式：手动触发 + 每 2 小时定时执行
- cron：`0 */2 * * *`
- 运行环境：`ubuntu-latest`
- Python 版本：`3.12`
- 时区：`Asia/Shanghai`

### 工作流步骤

1. checkout `main`
2. 尝试 checkout `rss-state`
3. 如果 `rss-state` 不存在，则自动初始化该分支并写入空状态
4. 安装 Python 与 `uv`
5. 执行 `uv sync --frozen`
6. 执行 `uv run fluxa --state-path state-worktree/state/state.json --repo ${{ github.repository }} --run-id ${{ github.run_id }}`
7. 如果状态文件有变化，则提交并推送到 `rss-state`

### 发布到 Issue 的方式

项目不再往单个 issue 下追加 comment，而是为每次运行维护一篇独立 issue：

- issue 标题格式：`Fluxa Digest | YYYY-MM-DD | HH:00-HH:00`
- issue 正文中会保留 `Run ID`，并写入 `<!-- fluxa-run:<run_id> -->` 作为幂等标记
- 同一个 `run_id` 重跑时，会更新原 issue，而不是新建重复 issue

### 仓库设置要求

如果你在 GitHub Actions 中需要自动推送 `rss-state` 或自动创建 issue，请确认：

- `Settings -> Actions -> General -> Workflow permissions` 为 `Read and write permissions`
- workflow 拥有 `contents: write`
- workflow 拥有 `issues: write`

## CNB 云原生构建流水线

项目现在也内置了 CNB 配置：

- [`.cnb.yml`](./.cnb.yml)
- [`.cnb/web_trigger.yml`](./.cnb/web_trigger.yml)
- [`.cnb/workflows/sync-rss.yml`](./.cnb/workflows/sync-rss.yml)

### 默认行为

- 触发方式：手动按钮 + 每 2 小时定时执行
- 发布后端：`cnb`
- issue 目标仓库：`wwvo/Issuo`
- issue 标签：`RSS`
- issue 指派：`illegal_name_cnb.by9cbmyhqda`、`illegal_name_cnb.by9ca6eibfa`
- 状态分支：`rss-state`
- 时区：`Asia/Shanghai`

### 工作流步骤

1. `uv sync --frozen`
2. 运行单元测试
3. 安装或确认 `cnb-rs`
4. 准备 `rss-state` 工作区；若分支不存在则自动初始化
5. 执行 `uv run fluxa --publisher cnb --repo wwvo/Issuo`
6. 将新的 `state/state.json` 提交并推送到 CNB 仓库的 `rss-state` 分支

### 发布到 Issue 的方式

CNB 流水线不会再调用 GitHub API，而是直接使用 `cnb-rs` 写入 `wwvo/Issuo` 的 issue：

- issue 标题格式：`Fluxa Digest | YYYY-MM-DD | HH:00-HH:00`
- issue 正文同样会保留 `Run ID`，并写入 `<!-- fluxa-run:<run_id> -->` 标记
- 同一个 `run_id` 的重跑会尝试更新原 CNB issue，而不是重复创建
- 创建 issue 时会附带 `RSS` label，并自动指派 `illegal_name_cnb.by9cbmyhqda`、`illegal_name_cnb.by9ca6eibfa`

> [!WARNING]
> 不建议同时开启 GitHub Actions 定时任务和 CNB 定时任务。
> 因为两边会各自维护自己的 `rss-state` 分支，并且分别往 GitHub / CNB issue 发结果，容易造成重复抓取、状态漂移和双份通知。

## 开发与测试

### 本地开发

```bash
uv sync
uv run fluxa --help
cnb-rs version
```

### 运行测试

```bash
uv run python -m unittest discover -s tests
```

### 常见调试方式

```bash
uv run fluxa --bootstrap-only
uv run fluxa --repo wwvo/Fluxa --run-id debug-001 --dry-run
uv run fluxa --publisher cnb --repo wwvo/Issuo --run-id debug-cnb-001 --dry-run
```

`main.py` 还会把本轮概览、恢复成功 feed 与失败 feed 输出到控制台；在 GitHub Actions 中，如果存在 `GITHUB_STEP_SUMMARY`，也会同步写入运行摘要。

## FAQ / 常见问题

### 这个项目需要数据库吗？

默认不需要。`Fluxa` 的状态模型足够小，直接放进 `rss-state` 分支即可，维护成本远低于额外引入 SQLite、Redis 或外部数据库。

### 为什么第一次运行没有发任何文章？

这是 bootstrap 的预期行为。首次运行只建立 `seen_ids`，避免把旧文章一次性全部发出来。第二次开始才会真正按增量发布。

### `200` 和 `304` 有什么区别？

- `200` 表示服务端返回了新内容或完整内容，`Fluxa` 会解析它，并决定有没有新文章。
- `304` 表示服务端确认“自上次缓存以来没有变化”，`Fluxa` 不会解析正文，也不会产生新文章，但会把它视作一次成功检查。

### 为什么有些源会出现 `406` 或 `415`？

这通常不是“URL 一定坏了”，而是服务端、CDN、代理层或 RSSHub 实例对请求头比较敏感：

- `406` 常见于服务端不接受当前 `Accept` 协商结果
- `415` 常见于服务端错误地把当前请求视为“不支持的媒体类型”

`Fluxa` 遇到这类错误会先切换为宽松请求头再重试一次。

### 为什么某些 RSSHub feed 还是可能失败？

因为 fallback 只能解决“实例不可用但 route 仍然可用”的问题。如果：

- 所有公共实例都故障
- 某条 RSSHub route 本身失效
- 上游站点反爬或临时不可访问

那么本轮仍会失败。此时可以从 Actions 日志里的失败汇总定位具体 feed。

### CNB 流水线为什么直接发到 CNB issue？

因为 CNB 仓库本身就可以承接通知、归档和邮件提醒，直接在当前仓库落 issue 更符合“代码、流水线和通知都留在同一平台”的使用方式。

### 为什么 issue 而不是 issue comment？

因为单条 comment 持续累积后会越来越长，阅读和检索都不方便。按运行生成独立 issue，更适合归档、搜索、链接分享和排查单次执行结果。
