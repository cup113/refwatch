# refwatch

Watch **every branch and tag** of a GitHub repository and get a digest of new commits pushed to the
channel you pick (QQ / WeCom / DingTalk / Feishu / ServerChan / Bark).

Two shapes, one engine, no AI in the loop:

- **Tray app** (the Windows default): lives in the notification area, edit the watch list in a window,
  run a round or preview one whenever you like.
- **Command line**: `python watch.py`, standard library only, driven by Task Scheduler or cron.

What lands on your phone:

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

And what you configure it with:

![Settings panel](docs/screenshots/panel-repos.png)

---

## Quick start

### A. Tray app (recommended)

Needs **Git** on `PATH`; the source shape also needs Python 3.9+ and two packages.

```powershell
git clone https://github.com/cup113/refwatch
cd refwatch
python -m pip install -r requirements-gui.txt
pythonw tray.py
```

Or grab the [latest release](https://github.com/cup113/refwatch/releases/latest):
`refwatch-setup-<version>.exe` (per-user install, no administrator needed) or the portable
`refwatch-<version>-win64.zip`.
Then right-click the icon → "打开设置…" → fill in repositories and credentials → save; effective immediately.

### B. Command line / headless (Task Scheduler or cron)

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Deploys to `%LOCALAPPDATA%\refwatch`, builds a silent baseline, registers a task that runs every 30
minutes. Idempotent, and it **never overwrites** `config.json` / `state.json` / mirrors / logs.
Options: `-Dest 'D:\gh-watch'`, `-Minutes 60`, `-NoTask`.

> The tray and the scheduled task are **alternatives** — running both only makes them skip each other
> on `.lock`. On first start the tray asks once whether to remove the old task.

Both shapes need `repos` (`["owner/name"]`). The **push channel** is picked by `channel` in
`config.json` (default `qq`; the settings panel's Notifications tab has the same dropdown). All
credentials live in `.env` inside the data directory; environment variables win.

| channel | `channel` | `.env` keys | notes |
|---|---|---|---|
| QQ official bot | `qq` | `QQBOT_APPID` / `QQBOT_SECRET`, plus `openid` in config | `openid` is the recipient's C2C id from the bot's message events; credentials from a bot app on the [QQ open platform](https://q.qq.com/) |
| WeCom group bot | `wecom` | `WECOM_WEBHOOK` | the full webhook URL from the group's "add bot" dialog; 2048-**byte** per message, longer digests are split again |
| DingTalk group bot | `dingtalk` | `DINGTALK_WEBHOOK` (optional `DINGTALK_SECRET`) | the secret only if the bot uses signed mode |
| Feishu group bot | `feishu` | `FEISHU_WEBHOOK` (optional `FEISHU_SECRET`) | the secret only if signature verification is on |
| ServerChan | `serverchan` | `SERVERCHAN_SENDKEY` | pushes to your WeChat; [5 messages/day on the free tier](https://sct.ftqq.com/) |
| Bark (iOS) | `bark` | `BARK_KEY` (optional `BARK_SERVER`) | copy the device key from the app; point `BARK_SERVER` at a self-hosted server if you run one |

Smoke-test one channel on its own, without a full round:

```powershell
python "$env:LOCALAPPDATA\refwatch\channels.py" --channel wecom --root "$env:LOCALAPPDATA\refwatch" "test"
```

Dry-run it once — nothing is sent:

```powershell
python -X utf8 "$env:LOCALAPPDATA\refwatch\watch.py" --root "$env:LOCALAPPDATA\refwatch" --dry-run
```

---

## What it watches: ref → SHA, not diffs

Each ref remembers the SHA it had last time. That separates four cases a diff cannot tell apart:

| event | meaning | how it is detected |
|---|---|---|
| **update** | new commits on a branch | old SHA is an ancestor of the new one |
| **rewritten** | force-push / rebase / moved tag | old SHA is *not* an ancestor |
| **new** | branch or tag appeared | the ref is not in the baseline |
| **deleted** | branch or tag is gone | it was in the baseline, `fetch --prune` removed it |

The first round for a repository is **silent**: it records a baseline instead of pushing years of
history at you. `refs/pull/*` is **never** tracked (GitHub broadcasts every PR as a ref — hundreds on
busy repositories). Rationale: [ADR-0001](docs/adr/0001-state-is-ref-to-sha.md),
[ADR-0002](docs/adr/0002-heads-and-tags-only.md).

---

## Configuration

`config.json` lives in the data directory (`%LOCALAPPDATA%\refwatch` for the tray). Everything except
`repos` is optional, and everything the tray can edit is in the settings panel.

| key | default | meaning |
|---|---|---|
| `repos` | `[]` | `["owner/name", ...]` to watch |
| `exclude_ref_patterns` | `dependabot/*`, `renovate/*` | fnmatch patterns to skip |
| `max_commits_per_ref` | `15` | newest N commits listed per branch |
| `max_chunks_per_repo` | `2` | messages per repository per round |
| `chunk_limit` | `4500` | characters per message |
| `openid` | `""` | QQ channel recipient; falls back to `QQBOT_OPENID` |
| `channel` | `"qq"` | push channel: `qq` / `wecom` / `dingtalk` / `feishu` / `serverchan` / `bark`; invalid falls back to `qq` |
| `notify` | `true` | `false` = record state, never send |
| `fetch_timeout_sec` | `300` | per-repository `git fetch` timeout |
| `fail_alert_threshold` | `3` | consecutive failures before one alert |
| `lock_stale_sec` | `1800` | a lock older than this is abandoned |
| `git_ssl_backend` | `"openssl"` | Windows only; `""` disables the injection |
| `language` | `"zh"` | language of all runtime text (digest, logs, tray, panel); invalid falls back to `zh` |
| `interval_minutes` | `30` | the tray's schedule (`install.ps1 -Minutes` writes it too) |
| `autostart` | `true` | start at login; the tray reconciles `HKCU\...\Run` to it |
| `task_prompt_done` | `false` | bookmark: the old scheduled task has been asked about |

---

## Tray app

Right-click the icon:

| item | what it does |
|---|---|
| status / next run | read-only |
| 立即运行一轮 | a manual round: does not change the schedule or move the next run |
| 预览（不发送） | same as `--dry-run`: renders what would be sent, **sends nothing, keeps the baseline** |
| 打开设置… | the settings panel (double-click does the same) |
| 界面语言 → 中文 / English | switch the UI language from the tray (writes `config.json`'s `language`) |
| 打开日志 / 打开数据目录 | open Explorer |
| 开机自启 | toggle; `autostart` is the truth, the registry value is its projection |
| 暂停调度 | stops automatic scheduling only; manual rounds still work (resets on restart) |
| 退出 | stop the scheduler, remove the icon |

The menu and panel follow `language` (the Chinese item names above are the `zh` default):
saving a new language redraws the menu immediately and rebuilds the panel in it; digests
and log lines switch from the next round on. The tray's `界面语言` submenu switches directly,
so an English user never has to start from the Chinese panel.

Panel tabs: [repositories](docs/screenshots/panel-repos.png) ·
[notifications](docs/screenshots/panel-notify.png) · [schedule](docs/screenshots/panel-schedule.png) ·
[advanced](docs/screenshots/panel-advanced.png) · [log drawer](docs/screenshots/panel-log.png)

The same five tabs in English: [repositories](docs/screenshots/panel-repos-en.png) ·
[notifications](docs/screenshots/panel-notify-en.png) · [schedule](docs/screenshots/panel-schedule-en.png) ·
[advanced](docs/screenshots/panel-advanced-en.png) · [log](docs/screenshots/panel-log-en.png)

Guarantees worth knowing:

- **Merge writes**: keys the panel does not know survive; a broken file is backed up to
  `config.json.bak.<timestamp>` first; writes are atomic so a concurrent round never reads half a file.
- **Credentials only go to `.env`**, never into `config.json`; the panel shows where it read them from.
- **Unsaved changes are visible**: editing any field (credentials included) shows "unsaved changes";
  closing, cancelling, quitting or switching language from the tray asks first instead of discarding silently.
- **Single instance**: a second launch tells you it is already running and that the icon may be folded
  into the taskbar's `^` area.
- Crashes are visible: uncaught exceptions go to `logs/crash.log` (credentials redacted) and to a
  message box — never "double-click does nothing".
- The tray's own `schtasks` children also carry `CREATE_NO_WINDOW`, so no black window flashes
  (same rule as [ADR-0003](docs/adr/0003-windowless-scheduled-run.md)).

Building the exe (CI does it on every `v*` tag):

```powershell
python -m pip install -r requirements-gui.txt nuitka
python -m nuitka --standalone --enable-plugin=tk-inter --windows-console-mode=disable `
    --windows-icon-from-ico=assets/refwatch.ico --output-dir=build --output-filename=refwatch.exe tray.py
iscc /DAPP_VERSION=v1.0.0 installer.iss        # requires Inno Setup 6
build\tray.dist\refwatch.exe --one-round --dry-run --root <empty dir>   # smoke: expect exit code 2
```

---

## Operations

| I want to… | how |
|---|---|
| change the watch list | tray "打开设置…", or edit `repos` in `config.json` (a new repo starts silent) |
| make it quieter | add patterns to `exclude_ref_patterns`, e.g. `refs/heads/codex/*` |
| switch push channel | the Notifications tab dropdown, or `channel` in `config.json`; credentials go to `.env` per the table above |
| reset a baseline | delete that repository's entry in `state.json`, or the whole file (no button on purpose) |
| fix a broken mirror | delete `repos/<owner>__<name>.git`; it is rebuilt next round (delete its state entry to skip the backlog) |
| mute temporarily | "暂停调度" in the tray, or `notify: false` |
| see what happened | `logs/watch-YYYYMMDD.log`; exceptions in `logs/crash.log` (tray) or `logs/error.log` (CLI) |
| go back to a scheduled task | quit the tray and re-run `install.ps1`; config and baselines are untouched |

Exit codes: `0` ok, `1` could not send (credentials/token), `2` configuration problem.

CLI flags:

```bash
python watch.py                        # one round
python watch.py --root D:\data         # data dir (state.json, repos/, logs/)
python watch.py --config other.json    # alternate config file
python watch.py --repo owner/name      # only this repository
python watch.py --dry-run              # print only; do not send, do not advance the baseline
python watch.py --no-notify            # advance state, send nothing
```

Linux / macOS: use cron (no tray, and no black-window problem there):

```cron
*/30 * * * * cd /opt/refwatch && python3 watch.py --root /opt/refwatch >> logs/cron.log 2>&1
```

---

## Development

```bash
python -m ruff check .
python -m unittest discover -s tests           # zero third-party deps: what CI runs
python -m pip install -r requirements-gui.txt  # only for the tray / packaging
```

The suite is offline: throwaway repositories under the system temp dir (override with
`REFWATCH_TEST_TMP`) serve as the `origin`.

- In an ACL-restricted Windows sandbox, git's *local* transport forks `sh.exe` and fails — run the
  suite in a normal terminal or CI.
- Tray tests skip themselves without `pystray`/`PIL`; `test_icon` and the structural guards in
  `test_spawn` use only the standard library.

| file | role |
|---|---|
| `watch.py` | engine + CLI; `run_round()` is the only implementation of a round |
| `qq_notify.py` | QQ API + the HTTP / `.env` helpers shared by all channels, usable standalone |
| `channels.py` | the channel registry and one sender per channel; has a `--channel` smoke CLI |
| `i18n.py` | the two-language string table + `t()`: single source of all runtime text |
| `settings.py` | `config.json` / `.env` / autostart registry reads and writes (no GUI imports) |
| `scheduler.py` | fires a round every `interval_minutes` |
| `panel.py` | tkinter settings panel (top half is pure, testable logic) |
| `tray.py` | tray entry: single instance, menu, thread bridge, `--one-round` passthrough |
| `install.ps1` / `installer.iss` | headless deploy / tray installer |
| `tools/` | `window_probe.py` (window regression check), `make_icon.py` (icon generator, artifacts committed) |

A delivery channel is one function — `_prepare_<channel>()` in `channels.py`
([ADR-0008](docs/adr/0008-multi-channel-push.md); the seam started as `deliver()` in
[ADR-0004](docs/adr/0004-qq-only-with-notifier-seam.md)). Tray history and trade-offs:
[ADR-0005](docs/adr/0005-tray-app-replaces-scheduled-task.md),
[ADR-0006](docs/adr/0006-tray-stack-pystray-pillow-tk.md),
[ADR-0007](docs/adr/0007-distribution-nuitka-standalone-inno.md).

Release history: [CHANGELOG.md](CHANGELOG.md).

---

## Not implemented (on purpose)

- Long digests as a **file** upload: truncated with a compare link instead.
- **Retry queue** for failed sends: logged, collected again next round.
- **Pushing to several channels at once**: `channel` is single-choice; Telegram, a generic webhook
  and Pushover are on the candidate list for later (ADR-0008).
- LLM summaries: that would put an AI back in the loop.
- Destructive maintenance in the panel (reset baseline / delete mirror): v1 edits config and previews only.
- Code signing, auto-update, single-file exe.

## License

MIT — see [LICENSE](LICENSE).
