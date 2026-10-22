# refwatch

Watch **every branch and tag** of a GitHub repository and get a digest of new commits on QQ.
Plain Python scripts, no dependencies, no daemon, no AI in the loop — it runs unattended from a
scheduled task.

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

- **Python 3.9+** (standard library only — nothing to `pip install`)
- **Git** on `PATH`
- A **QQ bot** (AppID + AppSecret) and the recipient's `openid` — only if you want notifications
- For scheduled runs: Windows Task Scheduler (below), or `cron`/`systemd` on Linux/macOS

## Quick start (Windows)

```powershell
git clone https://github.com/cup113/refwatch
cd refwatch
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

`install.ps1` deploys to `%LOCALAPPDATA%\refwatch`, runs a silent baseline, then registers a
scheduled task that runs every 30 minutes. It is idempotent and **never overwrites** your
`config.json`, `state.json`, mirrors or logs.

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

   (Environment variables `QQBOT_APPID` / `QQBOT_SECRET` win over this file.)

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

## Scheduled runs (Windows)

The task action points **directly at `pythonw.exe`** — never at a `.cmd`/`.bat` wrapper, because
`cmd.exe` is a console program and would flash a window on every run:

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

Re-prove it any time with the bundled probe (it counts *console* windows while your command runs, so
a regression fails loudly instead of silently; unrelated windows are printed as `[noise]` and ignored):

```powershell
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

- **Change the watch list** — edit `repos` in `config.json`. A new repository gets a silent baseline
  on its first round.
- **Too noisy** — add patterns to `exclude_ref_patterns`, e.g. `refs/heads/codex/*`.
- **Reset a baseline** (after changing exclusions, or to skip a backlog): delete that repository's
  entry from `state.json`, or delete `state.json` entirely.
- **Broken mirror** — delete `repos/<owner>__<name>.git`; it is re-created on the next round. The old
  SHAs in `state.json` still exist, so you will get one digest covering the outage — delete the
  repository's `state.json` entry too if you do not want that.
- **Failure alerts** — after `fail_alert_threshold` consecutive fetch failures one message is sent,
  then silence until it recovers (the counter resets on success).
- **Logs** — `logs/watch-YYYYMMDD.log`; uncaught exceptions (which `pythonw` would otherwise swallow)
  go to `logs/error.log`.
- **Overlapping runs** — prevented by `.lock`; a lock older than `lock_stale_sec` is taken over.

## Development

```bash
python -m ruff check .
python -m unittest discover -s tests
```

The suite is offline: it creates throwaway repositories under the system temp dir (override with
`REFWATCH_TEST_TMP`) and uses them as the `origin`. Notes:

- Running the suite inside a restrictive Windows sandbox (ACL-restricted token, no named pipes) can
  fail in `git`'s *local* transport, which forks `sh.exe`. Run it in a normal terminal or CI.
- `REFWATCH_TEST_TMP` also helps when the system temp dir is not writable.

Layout: `watch.py` (fetch/diff/format), `qq_notify.py` (QQ API + credentials, usable standalone),
`install.ps1` (deploy + task), `tools/window_probe.py` (window regression check), `tests/`.

Adding a delivery channel means replacing one function — `deliver()` in `watch.py`
(see [docs/adr/0004](docs/adr/0004-qq-only-with-notifier-seam.md)).

## Not implemented (on purpose)

- Long digests as a **file** upload: currently truncated with a compare link instead.
- **Retry queue** for failed sends: the failure is logged, the next round collects again.
- LLM summaries: that would put an AI back in the loop; this tool is deliberately dumb.

## License

MIT — see [LICENSE](LICENSE).
