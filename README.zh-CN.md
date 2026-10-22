# refwatch —— GitHub 仓库监视器（推送 QQ）

监视指定仓库的**所有分支和 tag**，发现新提交就把「提交清单 + compare 链接」推到 QQ。

两种运行形态，共用同一套引擎（「一轮」只有一份实现）：

- **托盘应用**（Windows 默认）：常驻通知区域，右键就能改配置、立即跑一轮、预览将要发送的内容；
  可以打包成 exe，装完即用。见 [托盘应用](#托盘应用windows)。
- **命令行**（无头 / 服务器）：`python watch.py`，**只用标准库、零第三方依赖**，
  交给计划任务或 cron。托盘形态也保留了同一条无头入口：`refwatch.exe --one-round`。

两种形态都**不经过 AI**。

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

- **Python 3.9+** —— 命令行形态**只用标准库**，不需要 `pip install` 任何东西
- **Git** 在 `PATH` 里（**两种形态都需要**：`git.exe` 不随 exe 分发）
- **QQ 机器人**（AppID + AppSecret）和接收方的 `openid` —— 只在你要收推送时需要
- 定时运行：托盘应用自己调度；或者 Windows 计划任务 / Linux 的 `cron`

托盘应用另需两个第三方包（`requirements-gui.txt`）：`pystray`、`Pillow`。
**只有托盘需要**；`python watch.py` 永远不需要它们。用安装包时它们已经打进 exe 里了。

## 快速开始（Windows）

### A. 托盘应用（推荐）

```powershell
git clone https://github.com/cup113/refwatch
cd refwatch
python -m pip install -r requirements-gui.txt
pythonw tray.py
```

也可以用发行版里的 `refwatch-setup-<version>.exe`（每用户安装，不需要管理员），
安装时勾上「登录时自动启动」即可。

托盘里点「打开设置…」填仓库和凭据，保存后**立刻生效**，不用重启。详见
[托盘应用](#托盘应用windows)。

### B. 命令行 / 无头（计划任务或 cron）

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

`install.ps1` 会部署到 `%LOCALAPPDATA%\refwatch`、跑一次静默基线、注册一个每 30 分钟运行
的计划任务。脚本幂等，并且**绝不会覆盖**你的 `config.json`、`state.json`、镜像仓和日志。
它也会把程序文件里的托盘模块一起装上，所以之后随时可以 `pythonw tray.py` 改用托盘。

> 托盘的调度和计划任务是**互相替代**的：两个都在跑只会在 `.lock` 上互相跳过。
> 托盘首次启动会检测同名任务并问一次要不要移除。

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

   （环境变量 `QQBOT_APPID` / `QQBOT_SECRET` 优先级高于这个文件。托盘面板写入的也是这个文件。）

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
| `interval_minutes` | `30` | **调度周期**（托盘形态）；`install.ps1 -Minutes` 也写它 |
| `autostart` | `true` | 登录时自启；托盘以它为准校正 `HKCU\...\Run` |
| `task_prompt_done` | `false` | 书签：旧计划任务已经询问过，不再每次都弹框 |

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

## 托盘应用（Windows）

```powershell
pythonw tray.py                 # 常驻（发行版里就是 refwatch.exe）
python tray.py --one-round      # 跑一轮就退出（计划任务 / 窗口探针用）
```

右键图标：

| 菜单项 | 作用 |
|---|---|
| 状态 / 下次运行 | 只读；一眼看出是不是在跑、是不是暂停了 |
| 立即运行一轮 | **手动一轮**：不改变**调度周期**，也不挪动下一次的时间点 |
| 预览（不发送） | 与 `--dry-run` 完全同义：只把将要发送的内容放进日志抽屉，**不发送、不推进基线** |
| 打开设置… | 设置面板（双击图标同效） |
| 打开日志 / 打开数据目录 | 直接开资源管理器 |
| 开机自启 | 开关；`config.json` 的 `autostart` 是真相，注册表是它的投影 |
| 暂停调度 | 只停自动调度，手动一轮仍可用；进程内状态，重启即恢复 |
| 退出 | 停调度、关图标 |

设置面板四页：

- **仓库**：增删被监视仓库（可以直接粘贴 GitHub 链接，会归一成 `owner/name`），上下移调顺序。
- **通知**：`openid`、AppID/AppSecret（掩码显示，可切换）、是否推送。凭据写进数据目录的
  `.env`，**不会**进 `config.json`；面板上会显示「当前凭据来源」。
- **调度**：`interval_minutes`、开机自启，以及是否检测到同名计划任务。
- **高级**：排除规则与各种上限/超时（默认值见上一节）。

几条行为上的硬约束：

- 保存是**合并写**：面板不认识的键（比如你手加的）原样保留；文件被写坏了先备份成
  `config.json.bak.<时间戳>` 再写。落盘是原子替换，并发的另一轮不会读到半个文件。
- **单实例**：第二次双击会弹框告诉你「已经在运行」，图标可能在任务栏 `^` 折叠区里。
- 崩溃可见：托盘没有控制台，所以未捕获异常写 `logs/crash.log`（凭据已打码）并弹框，
  而不是「双击没反应」。
- 托盘与计划任务**二选一**，同时存在只会在 `.lock` 上互相跳过。首次启动检测到 `refwatch`
  任务会问一次要不要移除（`task_prompt_done` 记住已问过）。

打包（CI 在打 tag 时自动做，本地想复现的话）：

```powershell
python -m pip install -r requirements-gui.txt nuitka
python tools\make_icon.py                    # 只有改图标才需要重跑（产物已入库）
python -m nuitka --standalone --enable-plugin=tk-inter --windows-console-mode=disable `
    --windows-icon-from-ico=assets/refwatch.ico --output-dir=build --output-filename=refwatch.exe tray.py
iscc /DAPP_VERSION=v1.2.0 installer.iss      # 需要 Inno Setup 6
```

构建后请跑一次冒烟 —— 那是唯一能证明「包没漏模块」的检查：

```powershell
build\tray.dist\refwatch.exe --one-round --dry-run --root <空目录>
# 期望退出码 2（配置里没有仓库）；退出码 1 说明启动期就出错了
```


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

`pythonw` 不是唯一选择：用安装包里的 `refwatch.exe` 时，任务动作直接写
`-Argument '--one-round --root "..."'` 即可 —— 它同样是 GUI 子系统，且 `--one-round` 跑完就退出。
托盘应用常驻时**不要**再注册这个任务。

### 为什么不再闪黑窗口

`pythonw.exe` 没有控制台，它自己永远不会显示窗口。但它 spawn 的每个 `git` 都是**控制台子系统
程序**，而 Windows 会给「父进程没有控制台」的控制台程序**新分配**一个控制台窗口。每仓库一轮
5~8 次 git 调用，于是每 30 分钟闪好几下。所以 `watch.py` 用 `CREATE_NO_WINDOW` 启动 git；
在 Windows 11 上实测，从 `pythonw` 里 spawn 一次 `git fetch`：

| 场景 | 新出现的可见窗口 |
|---|---|
| 不加标志 | 1 个 `ConsoleWindowClass`（`git.exe`） |
| 加 `CREATE_NO_WINDOW` | 0 个 |

托盘应用是同一类进程（GUI 子系统），所以这条规则照旧：它起的 `schtasks` 等子进程也走同一个
flag，单点封装在 `tray.run_hidden()`，`tests/test_spawn.py` 里有源码级守卫。

随时可以用仓库自带的探针复验（它在目标命令运行期间数**控制台窗口**，回归会立刻暴露而不是悄悄发生；
别人家的窗口会标成 `[noise]` 并忽略）。注意**目标命令必须是跑一轮就退出的那种**，
常驻的托盘进程会让探针一直挂着：

```powershell
python tools\window_probe.py -- python -X utf8 tray.py --one-round --dry-run --root <空目录>
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

- **改监视清单**：托盘点「打开设置…」，或编辑 `config.json` 的 `repos`。新仓库首次运行自动静默建基线。
- **嫌吵**：往 `exclude_ref_patterns` 加模式，例如 `refs/heads/codex/*`。
- **重建基线**（改了排除规则、或不想被积压的变化刷屏）：删掉 `state.json` 里对应仓库的条目，
  或整个删掉 `state.json`。托盘面板**故意不提供**这个按钮：它是破坏性动作，v1 留给手改文件。
- **镜像仓坏了**：删掉 `repos\<owner>__<name>.git`，下一轮自动重建。`state.json` 里的旧 SHA
  还在，所以会把这期间的积压一次性报出来；不想被刷屏就同时删掉该仓库的状态条目。
- **失败告警**：连续 `fail_alert_threshold` 次抓取失败会推一条，之后不再重复，恢复后自动重置。
- **日志**：`logs/watch-YYYYMMDD.log`；无控制台形态下未捕获异常写 `logs/error.log`
  （CLI）或 `logs/crash.log`（托盘，凭据已打码）。
- **重叠运行**：由 `.lock` 拦住；超过 `lock_stale_sec` 的锁会被接管。
- **临时不想被推**：托盘点「暂停调度」（手动一轮仍可用）；或在配置里把 `notify` 设成 `false`。
- **不想常驻了**：托盘菜单退出，`Unregister-ScheduledTask` 反面操作 —— 用 `install.ps1` 重新
  注册计划任务即可，配置与基线都不动。

## 开发

```bash
python -m ruff check .
python -m unittest discover -s tests           # 零第三方依赖，CI 的主体
python -m pip install -r requirements-gui.txt  # 只有要跑托盘/打包才需要
python -m unittest discover -s tests           # 装了 GUI 依赖后，托盘契约测试也会执行
```

测试完全离线：在系统临时目录（可用 `REFWATCH_TEST_TMP` 改）里建丢弃式仓库当 `origin`。
三点注意：

- 在限制较严的 Windows 沙箱里（ACL 受限令牌、禁命名管道），`git` 的**本地传输**会 fork
  `sh.exe` 而失败 —— 请在普通终端或 CI 里跑测试。
- 系统临时目录不可写时，用 `REFWATCH_TEST_TMP` 指到别处。
- 托盘相关测试（菜单回调参数、单实例互斥量、图标、脱敏）在没有 `pystray`/`PIL` 的环境里
  自动跳过，所以零依赖矩阵照样全绿；`tests/test_icon.py` 与 `tests/test_spawn.py` 的结构守卫
  只用标准库，任何环境都跑。

目录：

| 文件 | 作用 |
|---|---|
| `watch.py` | 引擎 + CLI：抓取/比对/格式化，`run_round()` 是「一轮」的唯一实现 |
| `qq_notify.py` | QQ API + 凭据读取，可单独当命令行工具用 |
| `settings.py` | `config.json` / `.env` / 自启注册表的读写校验（无 GUI 依赖） |
| `scheduler.py` | 按 `interval_minutes` 触发一轮（暂停/手动一轮/不重入） |
| `panel.py` | tkinter 设置面板（上半部分是纯逻辑，可测） |
| `tray.py` | 托盘入口：单实例、菜单、线程桥接、崩溃可见、`--one-round` 透传 |
| `install.ps1` | 部署 + 注册计划任务（无头形态） |
| `installer.iss` | Inno Setup 安装包（托盘形态） |
| `tools/window_probe.py` | 窗口回归检查（黑窗口不会出现在日志里，只能枚举） |
| `tools/make_icon.py` | 生成 `assets/refwatch.ico` / `.png`（产物入库） |

要加一种推送通道，只需替换一个函数 —— `watch.py` 里的 `deliver()`
（见 [docs/adr/0004](docs/adr/0004-qq-only-with-notifier-seam.md)）。

## 刻意没做

- 超长摘要降级为**文件**发送：目前用「截断 + compare 链接」代替。
- 发送失败的**重试队列**：只记日志，下一轮重新攒。
- LLM 语义化摘要：那会把 AI 放回环内，与本工具的定位冲突。
- 面板里的破坏性运维（重置基线 / 删镜像仓 / 解注册任务）：v1 只做配置与预览。
- 代码签名与自动更新、单文件 exe：见
  [docs/adr/0007](docs/adr/0007-distribution-nuitka-standalone-inno.md)。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
