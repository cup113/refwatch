# refwatch —— GitHub 仓库监视器（推送 QQ）

监视指定仓库的**所有分支和 tag**，发现新提交就把「提交清单 + compare 链接」推到 QQ。
纯脚本，零依赖，无人值守，**不经过 AI**。

```
[acme/widget] 2 个 ref 有更新  (时间 UTC+0800)
* main  3 个新提交
  1. a1b2c3d 10-21 09:12 alice +42/-7
     fix(ui): 输入框在窄屏下换行
  2. b2c3d4e 10-21 11:03 bob +5/-5
     chore: bump deps
  3. c3d4e5f 10-22 08:40 carol merge
     Merge pull request #328 from dependabot/npm_and_yarn/vite-6
  compare: https://github.com/acme/widget/compare/9f8e7d6c5b4a..c3d4e5f6a7b8
  最新: https://github.com/acme/widget/commit/c3d4e5f6a7b8
* 新分支: feat/login  (c3d4e5f 登录页骨架)
```

## 为什么状态是 ref → SHA，而不是 diff

市面上多数「盯仓库」工具比的是 diff。diff 分不清 force-push、正常提交和分支删除。
refwatch 给每个 ref 记住上次看到的 SHA，再和这次看到的比，于是得到四种事件：

| 事件 | 含义 | 怎么判定 |
|---|---|---|
| **update** | 分支上多了提交 | 旧 SHA 是新 SHA 的祖先 |
| **rewritten** | force-push / rebase / tag 被移动 | 旧 SHA 不是新 SHA 的祖先 |
| **new** | 新分支或新 tag | 这个 ref 在状态里根本没有 |
| **deleted** | 分支或 tag 没了 | 状态里有，`fetch --prune` 之后不见了 |

每个仓库**首次运行是静默的**：只记录基线，不会把几年的历史一次性推给你。
详见 [docs/adr/0001](docs/adr/0001-state-is-ref-to-sha.md)。

## 环境要求

- **Python 3.9+**（只用标准库，不需要 `pip install` 任何东西）
- **Git** 在 `PATH` 里
- **QQ 机器人**（AppID + AppSecret）和接收方的 `openid` —— 只在你要收推送时需要
- 定时运行：Windows 计划任务（见下），或 Linux/macOS 的 `cron` / `systemd`

## 快速开始（Windows）

```powershell
git clone https://github.com/cup113/refwatch
cd refwatch
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

`install.ps1` 会部署到 `%LOCALAPPDATA%\refwatch`、跑一次静默基线、注册一个每 30 分钟运行
的计划任务。脚本幂等，并且**绝不会覆盖**你的 `config.json`、`state.json`、镜像仓和日志。

然后填两个文件：

1. `config.json` —— 至少填 `repos` 和 `openid`：

   ```json
   {
     "repos": ["owner/name"],
     "openid": "YOUR_QQ_OPENID"
   }
   ```

2. 同目录的 `.env` —— 机器人凭据：

   ```
   QQBOT_APPID=123456789
   QQBOT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
   ```

   （环境变量 `QQBOT_APPID` / `QQBOT_SECRET` 优先级高于这个文件。）

先试跑一次，不发送任何消息：

```powershell
python -X utf8 "$env:LOCALAPPDATA\refwatch\watch.py" --root "$env:LOCALAPPDATA\refwatch" --dry-run
```

参数：`-Dest 'D:\gh-watch'`（装到别处）、`-Minutes 60`、`-NoTask`（只部署不注册）。

### QQ 那几样东西哪来

AppID / AppSecret 来自 QQ 开放平台的机器人应用；`openid` 是接收方的 C2C id，机器人收到对方
一条消息后可从事件体里读到。主动消息有频控（单关系 20 条/分钟、1000 条/天），这正是本工具
发「摘要 + 链接」而不是 diff 本体的原因。

## 配置项

`config.json` 与 `watch.py` 同目录。除 `repos` 外都可省略：

| 键 | 默认值 | 含义 |
|---|---|---|
| `repos` | `[]` | 要监视的 `["owner/name", ...]` |
| `exclude_ref_patterns` | `["refs/heads/dependabot/*", "refs/heads/renovate/*"]` | 要跳过的 fnmatch 模式 |
| `max_commits_per_ref` | `15` | 每个分支最多列多少条新提交 |
| `max_chunks_per_repo` | `2` | 每仓库每轮最多发几条消息 |
| `chunk_limit` | `4500` | 单条消息字数上限 |
| `openid` | `""` | 接收方；也可用环境变量 `QQBOT_OPENID` |
| `notify` | `true` | `false` = 只记录状态，从不发送 |
| `fetch_timeout_sec` | `300` | 单仓库 `git fetch` 超时 |
| `fail_alert_threshold` | `3` | 连续失败多少次后发一条告警 |
| `lock_stale_sec` | `1800` | 超过这个时长的锁视为残留 |
| `git_ssl_backend` | `"openssl"` | 仅 Windows；置空则不注入 `-c http.sslBackend=` |

`refs/pull/*` **永远不跟踪**：GitHub 会把每个 PR 都广播成 ref（热闹的仓库有几百个），
跟踪它等于每推一次 PR 就发一条消息。这也是为什么镜像仓是 `git init --bare` + 显式 refspec，
而不是 `git clone --mirror`（后者等于 `+refs/*:refs/*`）——见
[docs/adr/0002](docs/adr/0002-heads-and-tags-only.md)。

## 手动运行

```bash
python watch.py                        # 跑一轮（配置取脚本目录下的 config.json）
python watch.py --root D:\data         # 数据目录：state.json、repos/、logs/
python watch.py --config other.json    # 另一个配置文件
python watch.py --repo owner/name      # 只检查这个仓库
python watch.py --dry-run              # 只打印消息，不发送、不推进状态
python watch.py --no-notify            # 只推进状态/写日志
```

退出码：`0` 正常；`1` 发送失败（凭据/token）；`2` 配置有问题。

## 定时运行（Windows）

任务动作**直接指向 `pythonw.exe`**，绝不要包一层 `.cmd`/`.bat`：`cmd.exe` 是控制台程序，
每轮都会闪一个黑窗口。

```powershell
$act = New-ScheduledTaskAction -Execute "$env:LOCALAPPDATA\Python\bin\pythonw.exe" `
       -Argument '-X utf8 "C:\path\to\watch.py" --root "C:\path\to\data"'
$trg = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30)
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
       -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
       -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'refwatch' -Action $act -Trigger $trg -Settings $set -Force
```

但光有 `pythonw` **不够**，原因见下一节。

### 为什么不再闪黑窗口

`pythonw.exe` 没有控制台，它自己永远不会显示窗口。但它 spawn 的每个 `git` 都是**控制台子系统
程序**，而 Windows 会给「父进程没有控制台」的控制台程序**新分配**一个控制台窗口。每仓库一轮
5~8 次 git 调用，于是每 30 分钟闪好几下。所以 `watch.py` 用 `CREATE_NO_WINDOW` 启动 git；
在 Windows 11 上实测，从 `pythonw` 里 spawn 一次 `git fetch`：

| 场景 | 新出现的可见窗口 |
|---|---|
| 不加标志 | 1 个 `ConsoleWindowClass`（`git.exe`） |
| 加 `CREATE_NO_WINDOW` | 0 个 |

随时可以用仓库自带的探针复验（它在目标命令运行期间数**控制台窗口**，回归会立刻暴露而不是悄悄发生；
别人家的窗口会标成 `[noise]` 并忽略）：

```powershell
python tools\window_probe.py -- pythonw.exe -X utf8 watch.py --root . --dry-run
# [probe] 目标退出码 0，观察 4.2s，新控制台窗口 0 个，其它新窗口 0 个
```

细节与被否掉的方案见 [docs/adr/0003](docs/adr/0003-windowless-scheduled-run.md)。

### Linux / macOS

那边没有窗口问题，用 cron：

```cron
*/30 * * * * cd /opt/refwatch && python3 watch.py --root /opt/refwatch >> logs/cron.log 2>&1
```

## 运维

- **改监视清单**：编辑 `config.json` 的 `repos`。新仓库首次运行自动静默建基线。
- **嫌吵**：往 `exclude_ref_patterns` 加模式，例如 `refs/heads/codex/*`。
- **重建基线**（改了排除规则、或不想被积压的变化刷屏）：删掉 `state.json` 里对应仓库的条目，
  或整个删掉 `state.json`。
- **镜像仓坏了**：删掉 `repos\<owner>__<name>.git`，下一轮自动重建。`state.json` 里的旧 SHA
  还在，所以会把这期间的积压一次性报出来；不想被刷屏就同时删掉该仓库的状态条目。
- **失败告警**：连续 `fail_alert_threshold` 次抓取失败会推一条，之后不再重复，恢复后自动重置。
- **日志**：`logs/watch-YYYYMMDD.log`；`pythonw` 会吞掉未捕获异常，所以那些写
  `logs/error.log`。
- **重叠运行**：由 `.lock` 拦住；超过 `lock_stale_sec` 的锁会被接管。

## 开发

```bash
python -m ruff check .
python -m unittest discover -s tests
```

测试完全离线：在系统临时目录（可用 `REFWATCH_TEST_TMP` 改）里建丢弃式仓库当 `origin`。
两点注意：

- 在限制较严的 Windows 沙箱里（ACL 受限令牌、禁命名管道），`git` 的**本地传输**会 fork
  `sh.exe` 而失败 —— 请在普通终端或 CI 里跑测试。
- 系统临时目录不可写时，用 `REFWATCH_TEST_TMP` 指到别处。

目录：`watch.py`（抓取/比对/格式化）、`qq_notify.py`（QQ API + 凭据，可单独使用）、
`install.ps1`（部署 + 注册任务）、`tools/window_probe.py`（窗口回归检查）、`tests/`。

要加一种推送通道，只需替换一个函数 —— `watch.py` 里的 `deliver()`
（见 [docs/adr/0004](docs/adr/0004-qq-only-with-notifier-seam.md)）。

## 刻意没做

- 超长摘要降级为**文件**发送：目前用「截断 + compare 链接」代替。
- 发送失败的**重试队列**：只记日志，下一轮重新攒。
- LLM 语义化摘要：那会把 AI 放回环内，与本工具的定位冲突。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
