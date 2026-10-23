#!/usr/bin/env python3
"""语言表 —— 「全中文或全英文」的唯一真相（issue #2）。

规则（详见 CONTEXT.md 的「语言」词条）：

  * config.json 的 `language`（"zh" | "en"，默认 "zh"）翻转**全部**运行时文案：
    摘要、日志、托盘、设置面板、面板事件行，以及 qq_notify 抛出的错误串
    （后者是英文中性诊断文本，人话前缀由 watch 的捕获点翻译）。
  * **数据不翻译**：commit subject/author、repo slug、ref 名、URL、git 与 QQ API
    的原始输出是数据，不属于任何一层文案。
  * 键以 `_one` 结尾的是单数变体：代码在计数为 1 时选它。中文没有单复数变化，
    所以 zh 表里 `_one` 与复数条目同文 —— 这样代码里不需要按语言分叉。

两张表的键集合必须严格一致：tests/test_i18n.py 的奇偶测试守着，所以 t() 对
缺 key 直接 AssertionError，不做静默回退；占位符名字也一并比对。
"""

from __future__ import annotations

LANGUAGES = ("zh", "en")

STRINGS: dict[str, dict[str, str]] = {
    "zh": {
        # ---- 摘要（发到 QQ 的消息） --------------------------------
        "digest.header": "[{slug}] {count} 个 ref 有更新  (时间 UTC{tz})",
        "digest.header_one": "[{slug}] {count} 个 ref 有更新  (时间 UTC{tz})",
        "digest.branch_deleted": "分支已删除: {ref}",
        "digest.tag_deleted": "tag已删除: {ref}",
        "digest.new_tag": "新 tag: {ref}",
        "digest.new_branch": "新分支: {ref}",
        "digest.tag_moved": "标签被移动: {ref}  {old} -> {new}",
        "digest.rewritten": "分支历史被改写: {ref}  {old} -> {new}",
        "digest.update_head": "{ref}  {total} 个新提交{span}",
        "digest.update_head_one": "{ref}  {total} 个新提交{span}",
        "digest.span": " 跨度 {days} 天",
        "digest.older_hidden": "  ... 另有 {count} 个较早提交未列出",
        "digest.older_hidden_one": "  ... 另有 {count} 个较早提交未列出",
        "digest.truncated": "...(内容过长已截断)",
        "digest.latest": "最新: {url}",
        "digest.fail_alert": (
            "[refwatch] {slug} 已连续 {count} 次抓取失败，最后一次错误：\n{error}\n"
            "（修复后会自动恢复，不会再重复提醒）"
        ),
        # ---- 日志行（logs/*.log 与 stdout） ------------------------
        "log.config_legacy_name": "配置文件用的是旧名字 {name}，建议改名为 config.json（下个版本不再支持）",
        "log.language_invalid": "language 配置值 {value} 不合法（只能是 zh/en），已按 zh 处理",
        "log.git_timeout": "git {args} 超时 ({timeout}s)",
        "log.baseline_first": "{slug}: 首次建立基线，记录 {count} 个 ref（静默，不通知）",
        "log.baseline_missing": "{slug}: 尚无基线，本轮静默建立（{count} 个 ref）",
        "log.no_changes": "{slug}: 无变化（{count} 个 ref）",
        "log.repo_changed": "{slug}: {count} 个 ref 有变化，消息 {chars} 字",
        "log.no_repos": "配置里没有仓库清单（{path} 的 repos）",
        "log.lock_busy": "上一轮仍在运行（.lock 未过期），本轮跳过",
        "log.check_failed": "{slug}: 检查失败（连续 {count} 次）: {error}",
        "log.round_pending": "本轮结束：{repos} 个仓库，{failures} 个失败，{pending} 条待发（未发送）",
        "log.round_quiet": "本轮结束：{repos} 个仓库，无变化",
        "log.no_openid": "配置里没有 openid（或环境变量 QQBOT_OPENID），本轮消息未发送",
        "log.token_failed": "取 QQ access_token 失败，本轮消息未发送: {error}",
        "log.sent_chars": "已推送 {chars} 字",
        "log.send_failed": "推送失败: {error}",
        "log.round_done": "本轮结束：{repos} 个仓库，{failures} 个失败，推送 {sent}/{total} 条",
        "log.dry_run_part": "\n===== DRY-RUN {slug} [{index}/{total}] ({chars} 字) =====\n{part}",
        "log.dry_run_notice": "\n===== DRY-RUN 通知 =====\n{part}",
        "log.uncaught": "未捕获异常",
        # ---- 共用短句 ----------------------------------------------
        "common.round_busy": "上一轮还没结束，先等它跑完。",
        # ---- 托盘：菜单 / 状态 / 弹框 ------------------------------
        "tray.menu_run_now": "立即运行一轮",
        "tray.menu_preview": "预览（不发送）",
        "tray.menu_settings": "打开设置…",
        "tray.menu_logs": "打开日志",
        "tray.menu_data": "打开数据目录",
        "tray.menu_autostart": "开机自启",
        "tray.menu_pause": "暂停调度",
        "tray.menu_quit": "退出",
        "tray.status_running": "状态：运行中…",
        "tray.status_paused": "状态：已暂停",
        "tray.status_ok": "状态：正常",
        "tray.next_paused": "下次运行：已暂停",
        "tray.next_at": "下次运行：{time}",
        "tray.next_none": "下次运行：——",
        "tray.note_resumed": "已恢复调度",
        "tray.note_paused": "已暂停调度（手动一轮仍可用）",
        "tray.note_config_unreadable": "配置读不出来，本轮跳过：{error}",
        "tray.note_autostart_failed": "改开机自启失败：{error}",
        "tray.note_autostart_on": "开机自启：已开启",
        "tray.note_autostart_off": "开机自启：已关闭",
        "tray.note_task_removed": "已移除旧的计划任务",
        "tray.note_open_failed": "打开 {target} 失败：{error}",
        "tray.windows_only": "refwatch 托盘应用只支持 Windows；无头用法请直接跑 watch.py。",
        "tray.no_tkinter": "这台机器的 Python 没有 tkinter，无法显示托盘与设置面板。",
        "tray.already_running": "refwatch 已经在运行。\n\n图标在通知区域 —— 看不到就点任务栏的「^」展开。",
        "tray.start_failed_title": "refwatch 启动失败",
        "tray.start_failed_body": "{detail}\n\n完整堆栈：{path}",
        "tray.task_ask": (
            "检测到旧的计划任务 refwatch（每 N 分钟跑一次 watch.py）。\n\n"
            "托盘应用已经接管调度，两者同时存在只会在 .lock 上互相跳过。\n"
            "现在移除该计划任务吗？"
        ),
        "tray.hint_task_found": (
            "检测到同名计划任务 refwatch：它和托盘会互相抢锁。\n"
            "建议在「任务计划程序」里移除它（或删掉后重启本程序会再问一次）。"
        ),
        "tray.hint_task_absent": "没有检测到同名计划任务，调度由本程序负责。",
        "tray.line_running": "运行中…",
        "tray.line_paused": "已暂停",
        "tray.line_next": "下次 {time}",
        "tray.line_unscheduled": "未调度",
        # ---- 面板日志抽屉的事件行 ----------------------------------
        "event.round_start": "{stamp} 开始一轮（{repos} 个仓库{suffix}）",
        "event.round_start_suffix": "，预览",
        "event.repo_start": "{stamp} {repo} 检查中…",
        "event.repo_done": "{stamp} {repo} 完成（{parts} 条消息）",
        "event.repo_error": "{stamp} {repo} 失败：{error}",
        "event.sent": "{stamp} 已推送 {index}/{total}（{chars} 字）",
        "event.round_done": "{stamp} 一轮结束：{status}",
        # ---- 设置面板 ----------------------------------------------
        "panel.title": "refwatch 设置",
        "panel.tab_repos": "仓库",
        "panel.tab_notify": "通知",
        "panel.tab_schedule": "调度",
        "panel.tab_advanced": "高级",
        "panel.save": "保存",
        "panel.cancel": "取消",
        "panel.run_now_button": "立即运行一轮",
        "panel.preview_button": "预览",
        "panel.log_button": "日志",
        "panel.repos_label": "被监视仓库（owner/name；也可以直接粘贴 GitHub 链接）",
        "panel.add": "添加",
        "panel.remove": "删除选中",
        "panel.up": "上移",
        "panel.down": "下移",
        "panel.repos_hint": "新仓库首次运行是静默的（只建基线）",
        "panel.field_openid": "接收方 openid",
        "panel.field_appid": "机器人 AppID",
        "panel.field_secret": "机器人 AppSecret",
        "panel.show": "显示",
        "panel.notify_label": "启用 QQ 推送（关闭 = 只记录状态，从不发送）",
        "panel.secrets_hint": (
            "凭据写在数据目录的 .env 里（QQBOT_APPID / QQBOT_SECRET），环境变量优先；"
            "这里的值不会进 config.json。"
        ),
        "panel.cred_source": "当前凭据来源：{source}",
        "panel.cred_from_env": "环境变量",
        "panel.cred_none": "（还没找到）",
        "panel.field_interval": "调度周期（分钟）",
        "panel.autostart_label": "登录时自动启动（写 HKCU 的 Run 项）",
        "panel.field_language": "界面语言",
        "panel.field_max_commits_per_ref": "每个分支最多列出多少条提交",
        "panel.field_max_chunks_per_repo": "每仓库每轮最多发几条",
        "panel.field_chunk_limit": "单条消息字数上限",
        "panel.field_fetch_timeout_sec": "单仓库 fetch 超时（秒）",
        "panel.field_fail_alert_threshold": "连续失败多少次后告警",
        "panel.field_lock_stale_sec": "锁残留判定（秒）",
        "panel.patterns_label": "exclude_ref_patterns（每行一个 fnmatch 模式）",
        "panel.notice_load_failed": "读取配置失败，下面显示的是默认值：{error}",
        "panel.notice_save_failed": "保存失败：{error}",
        "panel.notice_saved": "已保存到 {path}",
        "panel.notice_preview_running": "预览运行中…",
        "panel.notice_running": "运行中…",
        "panel.notice_no_git": "没找到 git —— 抓取会全部失败，请先安装 Git for Windows。",
        "panel.status_ok": "完成",
        "panel.status_dry_run": "预览完成（未发送、未推进基线）",
        "panel.status_not_sent": "完成（按设置没有发送）",
        "panel.status_no_openid": "未发送：还没填 openid",
        "panel.status_token_failed": "未发送：取 QQ access_token 失败",
        "panel.status_no_repos": "没配仓库",
        "panel.report_skip_lock": "跳过：上一轮仍在运行",
        "panel.report_repos": "{count} 个仓库",
        "panel.report_failures": "{count} 个失败",
        "panel.report_sent": "已推送 {count} 条",
        "panel.report_pending": "{count} 条待发",
        "panel.preview_part": "----- 第 {index} 条（{chars} 字）-----",
        "panel.preview_empty": "（这一轮没有要发送的内容）",
        "panel.log_missing": "还没有日志 —— 跑一轮之后这里就会有内容。",
        "panel.log_failed": "读日志失败：{error}",
        "panel.log_empty": "（日志是空的）",
        # ---- 设置校验（settings.validate 的报错） ------------------
        "validate.int": "{key} 必须是整数",
        "validate.int_range": "{key} 必须在 {low}..{high} 之间（现在是 {value}）",
        "validate.bad_slug": "仓库名不合法：{slug}（要 owner/name）",
        "validate.patterns_type": "exclude_ref_patterns 必须是字符串列表",
        "validate.backend": "git_ssl_backend 只能是 openssl / schannel / 空",
        "validate.bool": "{key} 必须是 true/false",
        "validate.openid_type": "openid 必须是字符串",
        "validate.language": "language 只能是 zh / en",
        "validate.secret_conflict": "{key}（该值应导出为 .env，不是 config.json 的键）",
    },
    "en": {
        # ---- digest (the QQ message) -------------------------------
        "digest.header": "[{slug}] {count} refs updated  (UTC{tz})",
        "digest.header_one": "[{slug}] {count} ref updated  (UTC{tz})",
        "digest.branch_deleted": "branch deleted: {ref}",
        "digest.tag_deleted": "tag deleted: {ref}",
        "digest.new_tag": "new tag: {ref}",
        "digest.new_branch": "new branch: {ref}",
        "digest.tag_moved": "tag moved: {ref}  {old} -> {new}",
        "digest.rewritten": "branch history rewritten: {ref}  {old} -> {new}",
        "digest.update_head": "{ref}  {total} new commits{span}",
        "digest.update_head_one": "{ref}  {total} new commit{span}",
        "digest.span": " spanning {days} days",
        "digest.older_hidden": "  ... {count} older commits not shown",
        "digest.older_hidden_one": "  ... {count} older commit not shown",
        "digest.truncated": "...(message truncated)",
        "digest.latest": "latest: {url}",
        "digest.fail_alert": (
            "[refwatch] {slug}: {count} consecutive fetch failures, last error:\n{error}\n"
            "(auto-recovers once fixed; no repeat alerts)"
        ),
        # ---- log lines (logs/*.log and stdout) ---------------------
        "log.config_legacy_name": "config file uses the legacy name {name}; rename it to config.json (support ends next release)",
        "log.language_invalid": "invalid language config value {value} (only zh/en); falling back to zh",
        "log.git_timeout": "git {args} timed out ({timeout}s)",
        "log.baseline_first": "{slug}: initial baseline recorded, {count} refs (silent, no notification)",
        "log.baseline_missing": "{slug}: no baseline yet, recorded silently this round ({count} refs)",
        "log.no_changes": "{slug}: no changes ({count} refs)",
        "log.repo_changed": "{slug}: {count} refs changed, message {chars} chars",
        "log.no_repos": "no repository list in config (repos in {path})",
        "log.lock_busy": "previous round still running (.lock not stale); this round skipped",
        "log.check_failed": "{slug}: check failed ({count} consecutive): {error}",
        "log.round_pending": "round done: {repos} repos, {failures} failed, {pending} messages pending (not sent)",
        "log.round_quiet": "round done: {repos} repos, no changes",
        "log.no_openid": "no openid in config (or env QQBOT_OPENID); nothing sent this round",
        "log.token_failed": "failed to get QQ access_token; nothing sent this round: {error}",
        "log.sent_chars": "sent {chars} chars",
        "log.send_failed": "send failed: {error}",
        "log.round_done": "round done: {repos} repos, {failures} failed, sent {sent}/{total}",
        "log.dry_run_part": "\n===== DRY-RUN {slug} [{index}/{total}] ({chars} chars) =====\n{part}",
        "log.dry_run_notice": "\n===== DRY-RUN notice =====\n{part}",
        "log.uncaught": "uncaught exception",
        # ---- shared short sentences --------------------------------
        "common.round_busy": "Previous round still running; wait for it to finish.",
        # ---- tray: menu / status / dialogs -------------------------
        "tray.menu_run_now": "Run a round now",
        "tray.menu_preview": "Preview (no send)",
        "tray.menu_settings": "Open settings…",
        "tray.menu_logs": "Open logs",
        "tray.menu_data": "Open data folder",
        "tray.menu_autostart": "Autostart on login",
        "tray.menu_pause": "Pause schedule",
        "tray.menu_quit": "Quit",
        "tray.status_running": "Status: running…",
        "tray.status_paused": "Status: paused",
        "tray.status_ok": "Status: OK",
        "tray.next_paused": "Next run: paused",
        "tray.next_at": "Next run: {time}",
        "tray.next_none": "Next run: —",
        "tray.note_resumed": "Schedule resumed",
        "tray.note_paused": "Schedule paused (manual rounds still work)",
        "tray.note_config_unreadable": "Config unreadable; round skipped: {error}",
        "tray.note_autostart_failed": "Failed to change autostart: {error}",
        "tray.note_autostart_on": "Autostart: on",
        "tray.note_autostart_off": "Autostart: off",
        "tray.note_task_removed": "Old scheduled task removed",
        "tray.note_open_failed": "Failed to open {target}: {error}",
        "tray.windows_only": "refwatch tray app is Windows-only; for headless use run watch.py directly.",
        "tray.no_tkinter": "This machine's Python has no tkinter; the tray and settings panel cannot show.",
        "tray.already_running": "refwatch is already running.\n\nThe icon is in the notification area — unfold the taskbar \"^\" if you cannot see it.",
        "tray.start_failed_title": "refwatch failed to start",
        "tray.start_failed_body": "{detail}\n\nFull traceback: {path}",
        "tray.task_ask": (
            "Found the old scheduled task refwatch (running watch.py every N minutes).\n\n"
            "The tray app now owns scheduling; keeping both only makes them skip each other on .lock.\n"
            "Remove the task now?"
        ),
        "tray.hint_task_found": (
            "A scheduled task named refwatch exists: it and the tray fight over the lock.\n"
            "Remove it in Task Scheduler (or delete it and restart this app to be asked again)."
        ),
        "tray.hint_task_absent": "No same-name scheduled task found; scheduling belongs to this app.",
        "tray.line_running": "running…",
        "tray.line_paused": "paused",
        "tray.line_next": "next {time}",
        "tray.line_unscheduled": "not scheduled",
        # ---- panel log drawer event lines --------------------------
        "event.round_start": "{stamp} round started ({repos} repos{suffix})",
        "event.round_start_suffix": ", preview",
        "event.repo_start": "{stamp} {repo} checking…",
        "event.repo_done": "{stamp} {repo} done ({parts} messages)",
        "event.repo_error": "{stamp} {repo} failed: {error}",
        "event.sent": "{stamp} sent {index}/{total} ({chars} chars)",
        "event.round_done": "{stamp} round done: {status}",
        # ---- settings panel ----------------------------------------
        "panel.title": "refwatch settings",
        "panel.tab_repos": "Repositories",
        "panel.tab_notify": "Notifications",
        "panel.tab_schedule": "Schedule",
        "panel.tab_advanced": "Advanced",
        "panel.save": "Save",
        "panel.cancel": "Cancel",
        "panel.run_now_button": "Run a round now",
        "panel.preview_button": "Preview",
        "panel.log_button": "Log",
        "panel.repos_label": "Watched repositories (owner/name; GitHub links paste fine)",
        "panel.add": "Add",
        "panel.remove": "Remove selected",
        "panel.up": "Up",
        "panel.down": "Down",
        "panel.repos_hint": "A new repository's first round is silent (baseline only)",
        "panel.field_openid": "Recipient openid",
        "panel.field_appid": "Bot AppID",
        "panel.field_secret": "Bot AppSecret",
        "panel.show": "Show",
        "panel.notify_label": "Enable QQ push (off = record state only, never send)",
        "panel.secrets_hint": (
            "Credentials live in .env in the data directory (QQBOT_APPID / QQBOT_SECRET); "
            "environment variables win; these values never enter config.json."
        ),
        "panel.cred_source": "Credential source: {source}",
        "panel.cred_from_env": "environment variables",
        "panel.cred_none": "(none found yet)",
        "panel.field_interval": "Interval (minutes)",
        "panel.autostart_label": "Start at login (writes an HKCU Run value)",
        "panel.field_language": "Language",
        "panel.field_max_commits_per_ref": "newest commits listed per branch",
        "panel.field_max_chunks_per_repo": "messages per repository per round",
        "panel.field_chunk_limit": "characters per message",
        "panel.field_fetch_timeout_sec": "per-repository fetch timeout (seconds)",
        "panel.field_fail_alert_threshold": "consecutive failures before one alert",
        "panel.field_lock_stale_sec": "lock staleness threshold (seconds)",
        "panel.patterns_label": "exclude_ref_patterns (one fnmatch pattern per line)",
        "panel.notice_load_failed": "Failed to read config; defaults shown below: {error}",
        "panel.notice_save_failed": "Save failed: {error}",
        "panel.notice_saved": "Saved to {path}",
        "panel.notice_preview_running": "Preview running…",
        "panel.notice_running": "Running…",
        "panel.notice_no_git": "git not found — every fetch will fail; install Git for Windows first.",
        "panel.status_ok": "done",
        "panel.status_dry_run": "preview done (nothing sent, baseline kept)",
        "panel.status_not_sent": "done (sending disabled by settings)",
        "panel.status_no_openid": "not sent: openid missing",
        "panel.status_token_failed": "not sent: QQ access_token request failed",
        "panel.status_no_repos": "no repositories configured",
        "panel.report_skip_lock": "skipped: previous round still running",
        "panel.report_repos": "{count} repos",
        "panel.report_failures": "{count} failed",
        "panel.report_sent": "sent {count}",
        "panel.report_pending": "{count} pending",
        "panel.preview_part": "----- part {index} ({chars} chars) -----",
        "panel.preview_empty": "(nothing to send this round)",
        "panel.log_missing": "No logs yet — content appears after a round.",
        "panel.log_failed": "Failed to read the log: {error}",
        "panel.log_empty": "(log is empty)",
        # ---- settings validation (settings.validate errors) --------
        "validate.int": "{key} must be an integer",
        "validate.int_range": "{key} must be in {low}..{high} (got {value})",
        "validate.bad_slug": "invalid repository name: {slug} (want owner/name)",
        "validate.patterns_type": "exclude_ref_patterns must be a list of strings",
        "validate.backend": "git_ssl_backend must be openssl / schannel / empty",
        "validate.bool": "{key} must be true/false",
        "validate.openid_type": "openid must be a string",
        "validate.language": "language must be zh / en",
        "validate.secret_conflict": "{key} (belongs in .env, not a config.json key)",
    },
}


def normalize_language(value: object) -> str:
    """非法/缺省一律回到 zh —— 现有用户的行为不变。"""
    text = str(value if value is not None else "").strip().lower()
    return text if text in LANGUAGES else "zh"


def t(lang: str, message_key: str, **kwargs: object) -> str:
    """取一条文案并填充占位符。缺 key 是程序错误：直接 AssertionError，不静默回退。

    参数名刻意叫 message_key 而不是 key：占位符里常用 {key}（如校验报错），
    同名会让 kwargs 撞车。
    """
    table = STRINGS[normalize_language(lang)]
    try:
        template = table[message_key]
    except KeyError:
        raise AssertionError(f"i18n key {message_key!r} missing in {lang!r} table") from None
    return template.format(**kwargs) if kwargs else template
