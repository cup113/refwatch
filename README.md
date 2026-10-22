# refwatch

Watch **every branch and tag** of a GitHub repository and get a digest of new commits on QQ.

Two ways to run it, over one engine (there is exactly one implementation of a round):

- **Tray app** (the Windows default): sits in the notification area, lets you edit the watch list and
  credentials, run a round on demand and preview what would be sent — installable as a single `exe`.
  See [Tray app](#tray-app-windows).
- **Command line** (headless / servers): `python watch.py`, **standard library only, zero third-party
  dependencies**, driven by Task Scheduler or cron. The packaged `refwatch.exe` keeps the same headless
  entry point: `refwatch.exe --one-round`.

No AI anywhere in the loop.

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

## Why the state is a ref → SHA map

Most "watch a repo" tools diff commits. That cannot tell a force-push from a normal update from a
deleted branch. refwatch stores, per ref, the SHA it saw last time, and compares that to the SHA it
sees now. That yields four distinct events:

| event | meaning | how it is detected |
|---|---|---|
| **update** | new commits on a branch | old SHA is an ancestor of the new one |
| **rewritten** | force-push / rebase / moved tag | old SHA is not an ancestor of the new one |
| **new** | branch or tag appeared | ref is not in the state at all |
| **deleted** | branch or tag is gone | ref was in the state, `fetch --prune` made it vanish |

The first run for a repository is **silent**: it records a baseline instead of pushing years of
history at you. See [docs/adr/0001](docs/adr/0001-state-is-ref-to-sha.md).

## Requirements

- **Python 3.9+** — the CLI uses the **standard library only**, nothing to `pip install`
- **Git** on `PATH` (both shapes need it: `git.exe` is never bundled)
- A **QQ bot** (AppID + AppSecret) and the recipient's `openid` — only if you want notifications
- Scheduling: the tray app schedules itself; otherwise Windows Task Scheduler, or `cron` on Linux/macOS

The tray additionally needs `pystray` and `Pillow` (`requirements-gui.txt`). **Only the tray needs
them**; `python watch.py` never does, and the installer bundles them into the exe.

## Quick start (Windows)

### A. Tray app (recommended)

```powershell
git clone https://github.com/cup113/refwatch
cd refwatch
python -m pip install -r requirements-gui.txt
pythonw tray.py
```

Or use the released `refwatch-setup-<version>.exe` (per-user install, no administrator needed) and
leave "start at login" checked.

Open "设置…" from the tray, fill in repositories and credentials, save — effective immediately, no
restart. Details: [Tray app](#tray-app-windows).

### B. Command line / headless (Task Scheduler or cron)

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

`install.ps1` deploys to `%LOCALAPPDATA%\refwatch`, runs a silent baseline, then registers a
scheduled task that runs every 30 minutes. It is idempotent and **never overwrites** your
`config.json`, `state.json`, mirrors or logs. It deploys the tray modules too, so you can switch to
the tray later with `pythonw tray.py`.

> The tray's schedule and the scheduled task **replace each other**; running both only makes them
> skip each other on `.lock`. On first start the tray detects the task and asks once whether to
> remove it.

Then fill in the two files it created:

1. `config.json` — at least `repos` and `openid`:

   ```json
   {
     "repos": ["owner/name"],
     "openid": "YOUR_QQ_OPENID"
   }
   ```

2. `.env` next to it — the bot credentials:

   ```
   QQBOT_APPID=123456789
   QQBOT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
   ```

   (Environment variables `QQBOT_APPID` / `QQBOT_SECRET` win over this file. The tray panel writes
   this same file.)

Check it without sending anything:

```powershell
python -X utf8 "$env:LOCALAPPDATA\refwatch\watch.py" --root "$env:LOCALAPPDATA\refwatch" --dry-run
```

Options: `-Dest 'D:\gh-watch'` (deploy elsewhere), `-Minutes 60`, `-NoTask` (deploy only).

### Getting the QQ pieces

The bot credentials come from the QQ open platform (`AppID` / `AppSecret`). The `openid` is the
recipient's C2C id as reported by the bot's message events — send the bot one message and read it
from the event payload. Proactive messages are rate limited per relationship (20/minute,
1000/day), which is exactly why this tool sends a digest with links instead of raw diffs.

## Configuration

`config.json` sits next to `watch.py`. Every key is optional except `repos`:

| key | default | meaning |
|---|---|---|
| `repos` | `[]` | `["owner/name", ...]` to watch |
| `exclude_ref_patterns` | `["refs/heads/dependabot/*", "refs/heads/renovate/*"]` | fnmatch patterns to skip |
| `max_commits_per_ref` | `15` | newest N commits listed per branch |
| `max_chunks_per_repo` | `2` | messages per repository per run |
| `chunk_limit` | `4500` | characters per message |
| `openid` | `""` | recipient; falls back to `QQBOT_OPENID` |
| `notify` | `true` | `false` = record state, never send |
| `fetch_timeout_sec` | `300` | per-repository `git fetch` timeout |
| `fail_alert_threshold` | `3` | consecutive failures before one alert message |
| `lock_stale_sec` | `1800` | a lock older than this is considered abandoned |
| `git_ssl_backend` | `"openssl"` | Windows only; `""` disables the `-c http.sslBackend=` injection |
| `interval_minutes` | `30` | the **schedule** of the tray app; `install.ps1 -Minutes` writes it too |
| `autostart` | `true` | start at login; the tray reconciles `HKCU\...\Run` to this value |
| `task_prompt_done` | `false` | bookmark: the old scheduled task has been asked about already |

`refs/pull/*` is **never** tracked, no matter what: GitHub broadcasts every PR as a ref (hundreds on
busy repositories), and tracking them means a message per PR push. That is also why the mirror is
created with `git init --bare` + explicit refspecs instead of `git clone --mirror`
(see [docs/adr/0002](docs/adr/0002-heads-and-tags-only.md)).

## Running it

```bash
python watch.py                        # one round (config.json next to the script)
python watch.py --root D:\data         # data dir: state.json, repos/, logs/
python watch.py --config other.json    # alternate config file
python watch.py --repo owner/name      # only this repository
python watch.py --dry-run              # print messages; do not send, do not advance state
python watch.py --no-notify            # advance state/log only
```

Exit codes: `0` ok, `1` could not send (credentials/token), `2` configuration problem.

## Tray app (Windows)

```powershell
pythonw tray.py                 # resident (this is refwatch.exe in a release)
python tray.py --one-round      # one round, then exit (scheduled task / window probe)
```

Right-click the icon:

| item | what it does |
|---|---|
| status / next run | read-only; tells you whether a round is running or scheduling is paused |
| 立即运行一轮 | **manual round**: does not change the **schedule** and does not move the next run |
| 预览（不发送） | same semantics as `--dry-run`: renders what would be sent, **sends nothing and does not advance the baseline** |
| 打开设置… | the settings panel (double-clicking the icon does the same) |
| 打开日志 / 打开数据目录 | open Explorer |
| 开机自启 | toggle; `autostart` in `config.json` is the truth, the registry value is its projection |
| 暂停调度 | stops automatic scheduling only; manual rounds still work (in-memory, resets on restart) |
| 退出 | stop the scheduler, remove the icon |

The settings panel has four tabs: repositories (paste a GitHub URL if you like — it is normalised to
`owner/name`), notifications (`openid`, AppID/AppSecret masked, whether to notify at all), schedule
(`interval_minutes`, autostart, whether a same-named scheduled task was detected), and advanced
(exclusion patterns, caps and timeouts).

Hard rules it follows:

- Saving is a **merge write**: keys the panel does not know about survive; a broken file is backed up
  to `config.json.bak.<timestamp>` first. Writes are atomic, so a concurrent round never reads half a file.
- **Single instance**: a second launch shows a message box saying it is already running and that the
  icon may be folded into the taskbar's `^` area.
- Crashes are visible: there is no console, so an uncaught exception goes to `logs/crash.log`
  (credentials redacted) *and* a message box — never "double-click does nothing".
- Tray and scheduled task are **alternatives**; both running just make them skip each other on
  `.lock`. On first start it detects the `refwatch` task and asks once whether to remove it.

Building the exe (CI does this on every `v*` tag):

```powershell
python -m pip install -r requirements-gui.txt nuitka
python tools\make_icon.py                    # only when changing the icon (artifacts are committed)
python -m nuitka --standalone --enable-plugin=tk-inter --windows-console-mode=disable `
    --windows-icon-from-ico=assets/refwatch.ico --output-dir=build --output-filename=refwatch.exe tray.py
iscc /DAPP_VERSION=v1.2.0 installer.iss      # requires Inno Setup 6
```

Then smoke-test the bundle — it is the only check that proves no module went missing:

```powershell
build\tray.dist\refwatch.exe --one-round --dry-run --root <empty dir>
# expect exit code 2 (no repositories configured); exit code 1 means it failed at startup
```

## Scheduled runs (Windows)

The task action points **directly at `pythonw.exe`** — never at a `.cmd`/`.bat` wrapper, because
`cmd.exe` is a console program and would flash a window on every run. With the packaged exe, point
the action at `refwatch.exe -Argument '--one-round --root "..."'` instead: it is a GUI-subsystem
binary too, and `--one-round` exits after the round. **Do not** register this task while the tray app
is resident.

```powershell
$act = New-ScheduledTaskAction -Execute "$env:LOCALAPPDATA\Python\bin\pythonw.exe" `
       -Argument '-X utf8 "C:\path\to\watch.py" --root "C:\path\to\data"'
$trg = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30)
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
       -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
       -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'refwatch' -Action $act -Trigger $trg -Settings $set -Force
```

`pythonw` alone is not enough, though — see below.

### Why there is no black window flash

`pythonw.exe` has no console, so it never shows a window itself. But every `git` call it spawns is a
**console-subsystem program**, and Windows allocates a *new console window* for a console process
whose parent has none. With 5–8 git invocations per repository per round, that is a handful of black
flashes every 30 minutes. `watch.py` therefore starts git with `CREATE_NO_WINDOW`; measured on
Windows 11, one `git fetch` spawned from `pythonw`:

| scenario | new visible windows |
|---|---|
| no flag | 1 × `ConsoleWindowClass` (`git.exe`) |
| `CREATE_NO_WINDOW` | 0 |

The tray app is the same kind of process (GUI subsystem), so the rule still holds: its own child
processes (`schtasks …`) go through the same flag, funnelled into `tray.run_hidden()`, with a
source-level guard in `tests/test_spawn.py`.

Re-prove it any time with the bundled probe (it counts *console* windows while your command runs, so
a regression fails loudly instead of silently; unrelated windows are printed as `[noise]` and ignored).
The target command **must exit on its own** — a resident tray process would keep the probe waiting:

```powershell
python tools\window_probe.py -- python -X utf8 tray.py --one-round --dry-run --root <empty dir>
python tools\window_probe.py -- pythonw.exe -X utf8 watch.py --root . --dry-run
# [probe] 目标退出码 0，观察 4.2s，新控制台窗口 0 个，其它新窗口 0 个
```

Details and rejected alternatives: [docs/adr/0003](docs/adr/0003-windowless-scheduled-run.md).

### Linux / macOS

No window problem exists there; use cron:

```cron
*/30 * * * * cd /opt/refwatch && python3 watch.py --root /opt/refwatch >> logs/cron.log 2>&1
```

## Operations

- **Change the watch list** — the tray's "打开设置…", or edit `repos` in `config.json`. A new
  repository gets a silent baseline on its first round.
- **Too noisy** — add patterns to `exclude_ref_patterns`, e.g. `refs/heads/codex/*`.
- **Reset a baseline** (after changing exclusions, or to skip a backlog): delete that repository's
  entry from `state.json`, or delete `state.json` entirely. The panel deliberately has no button for
  this — it is destructive, so v1 leaves it to editing the file.
- **Broken mirror** — delete `repos/<owner>__<name>.git`; it is re-created on the next round. The old
  SHAs in `state.json` still exist, so you will get one digest covering the outage — delete the
  repository's `state.json` entry too if you do not want that.
- **Failure alerts** — after `fail_alert_threshold` consecutive fetch failures one message is sent,
  then silence until it recovers (the counter resets on success).
- **Logs** — `logs/watch-YYYYMMDD.log`; uncaught exceptions (which `pythonw` would otherwise swallow)
  go to `logs/error.log` (CLI) or `logs/crash.log` (tray, credentials redacted).
- **Overlapping runs** — prevented by `.lock`; a lock older than `lock_stale_sec` is taken over.
- **Do not want it resident** — quit the tray and run `install.ps1` again to go back to a scheduled
  task; the configuration and baselines are untouched either way.
- **Temporarily quiet** — "暂停调度" in the tray (manual rounds still work), or `notify: false`.

## Development

```bash
python -m ruff check .
python -m unittest discover -s tests           # zero third-party deps: this is what CI runs
python -m pip install -r requirements-gui.txt  # only needed for the tray / packaging
python -m unittest discover -s tests           # with GUI deps installed, tray contract tests run too
```

The suite is offline: it creates throwaway repositories under the system temp dir (override with
`REFWATCH_TEST_TMP`) and uses them as the `origin`. Notes:

- Running the suite inside a restrictive Windows sandbox (ACL-restricted token, no named pipes) can
  fail in `git`'s *local* transport, which forks `sh.exe`. Run it in a normal terminal or CI.
- `REFWATCH_TEST_TMP` also helps when the system temp dir is not writable.
- Tray tests (menu callback arity, single-instance mutex, icon, redaction) skip themselves when
  `pystray`/`PIL` are missing, so the zero-dependency matrix stays green; `tests/test_icon.py` and
  the structural guards in `tests/test_spawn.py` use only the standard library.

Layout:

| file | role |
|---|---|
| `watch.py` | engine + CLI: fetch/compare/format; `run_round()` is the only implementation of a round |
| `qq_notify.py` | QQ API + credential lookup, usable standalone |
| `settings.py` | `config.json` / `.env` / autostart registry reads and writes (no GUI imports) |
| `scheduler.py` | fires a round every `interval_minutes` (pause, manual round, no re-entry) |
| `panel.py` | tkinter settings panel (top half is pure, testable logic) |
| `tray.py` | tray entry: single instance, menu, thread bridge, crash visibility, `--one-round` |
| `install.ps1` | deploy + register a scheduled task (headless shape) |
| `installer.iss` | Inno Setup installer (tray shape) |
| `tools/window_probe.py` | window regression check (flashes never reach any log, so enumerate) |
| `tools/make_icon.py` | generates `assets/refwatch.ico` / `.png` (artifacts are committed) |

Adding a delivery channel means replacing one function — `deliver()` in `watch.py`
(see [docs/adr/0004](docs/adr/0004-qq-only-with-notifier-seam.md)).

## Not implemented (on purpose)

- Long digests as a **file** upload: currently truncated with a compare link instead.
- **Retry queue** for failed sends: the failure is logged, the next round collects again.
- LLM summaries: that would put an AI back in the loop; this tool is deliberately dumb.
- Destructive maintenance in the panel (reset baseline / delete mirror / unregister task): v1 only
  edits configuration and previews.
- Code signing, auto-update, single-file exe: see
  [docs/adr/0007](docs/adr/0007-distribution-nuitka-standalone-inno.md).

## License

MIT — see [LICENSE](LICENSE).
