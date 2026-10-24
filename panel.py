#!/usr/bin/env python3
"""设置面板：把 config.json 变成能点的窗口。

分两层，刻意这样切：

  * **纯逻辑**（本文件上半部分）：表单值 ↔ 配置值的转换、读盘、日志尾部。不 import
    tkinter，所以能在任何平台测；
  * **widget 层**（`Panel`）：只有它在动 tkinter，且**只能跑在 Tk 主线程**上 —— 托盘
    用队列把菜单回调桥接过来（见 tray.py）。

面板不碰 state.json：重置基线属于运维动作，v1 不做。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import channels  # noqa: E402
import i18n  # noqa: E402
import qq_notify  # noqa: E402
import settings  # noqa: E402
import watch  # noqa: E402

try:  # 精简发行版/CI 镜像可能没有 tkinter：纯逻辑部分不该因此不可测。
    import tkinter as tk
    from tkinter import scrolledtext, ttk
except ImportError:  # pragma: no cover
    tk = None  # type: ignore[assignment]
    scrolledtext = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]

# 高级页里的数字项；interval_minutes 单独放在「调度」页。标签在 i18n 表的
# panel.field_<key>。
NUMERIC_FIELDS: tuple[str, ...] = (
    "max_commits_per_ref",
    "max_chunks_per_repo",
    "chunk_limit",
    "fetch_timeout_sec",
    "fail_alert_threshold",
    "lock_stale_sec",
)

BACKENDS = ("", "openssl", "schannel")

# 语言选择框显示的是人话，落盘的是代码；两个方向的映射都只有这两项。
LANG_DISPLAY = {"zh": "中文", "en": "English"}
LANG_CODES = {display: code for code, display in LANG_DISPLAY.items()}

# RoundReport.status -> i18n key；文案在 i18n 表的 panel.status_*。
_STATUS_KEYS = {
    "ok": "panel.status_ok",
    "dry-run": "panel.status_dry_run",
    "not-sent": "panel.status_not_sent",
    "no-recipient": "panel.status_no_recipient",
    "setup-failed": "panel.status_setup_failed",
    "no-repos": "panel.status_no_repos",
}


# ---------------------------------------------------------------- 纯逻辑


def form_from_config(cfg: dict, secrets: dict | None = None) -> dict:
    """配置 → 表单值。数字一律转成字符串（Entry 里就是文本），布尔保持布尔。"""
    found = secrets or {"appid": "", "secret": "", "source": ""}
    form: dict = {
        "repos": list(cfg.get("repos") or []),
        "channel": channels.normalize_channel(cfg.get("channel")),
        "openid": str(cfg.get("openid") or ""),
        "appid": str(found.get("appid") or ""),
        "secret": str(found.get("secret") or ""),
        "notify": bool(cfg.get("notify", True)),
        "autostart": bool(cfg.get("autostart", True)),
        "language": i18n.normalize_language(cfg.get("language")),
        "exclude_ref_patterns": list(cfg.get("exclude_ref_patterns") or []),
        "git_ssl_backend": str(cfg.get("git_ssl_backend", "") or ""),
    }
    for key in NUMERIC_FIELDS:
        form[key] = str(cfg.get(key, watch.DEFAULT_CONFIG.get(key, "")))
    form["interval_minutes"] = str(cfg.get("interval_minutes", watch.DEFAULT_CONFIG["interval_minutes"]))
    return form


def config_from_form(form: dict) -> dict:
    """表单值 → 要落盘的配置值。

    数字只是「尝试」转成 int：转不动就原样交给 settings.validate()，由它给出
    「xxx 必须是整数」这种可读的报错，而不是在这里抛 ValueError。
    """
    values: dict = {
        "repos": settings.normalize_repos(form.get("repos", [])),
        "channel": channels.normalize_channel(form.get("channel")),
        "openid": str(form.get("openid", "")).strip(),
        "notify": bool(form.get("notify", True)),
        "autostart": bool(form.get("autostart", True)),
        "language": i18n.normalize_language(form.get("language")),
        "exclude_ref_patterns": list(form.get("exclude_ref_patterns", [])),
        "git_ssl_backend": str(form.get("git_ssl_backend", "") or ""),
    }
    for key in ("interval_minutes", *NUMERIC_FIELDS):
        raw = form.get(key, "")
        text = str(raw).strip()
        try:
            values[key] = int(text)
        except (TypeError, ValueError):
            values[key] = text  # 让 validate() 去报错
    return values


def load_form(root: Path, config_path: Path | None = None) -> tuple[dict, str | None]:
    """读盘得到表单值。配置文件坏了也不炸：显示默认值 + 一条提示。"""
    notice: str | None = None
    try:
        cfg = settings.load(root, config_path)
    except (OSError, ValueError) as exc:
        cfg = dict(watch.DEFAULT_CONFIG)
        notice = i18n.t("zh", "panel.notice_load_failed", error=exc)  # 配置坏了，语言只能按默认
    try:
        secrets = settings.read_secrets(root)
    except OSError as exc:  # 读 .env 出错不该让面板起不来
        secrets = {"appid": "", "secret": "", "source": "", "error": str(exc)}
    form = form_from_config(cfg, secrets)
    # 所选通道的 .env 凭据填进表单（qq 走上面的 read_secrets 旧路，不重复读）。
    code = channels.normalize_channel(cfg.get("channel"))
    if code != "qq":
        env_keys = [str(spec["env"]) for spec in channels.CREDENTIALS.get(code, ())]
        values = qq_notify.read_env_values(env_keys, Path(root))
        for key in env_keys:
            form[key.lower()] = values.get(key, "")
    return form, notice


def format_report(report, lang: str = "zh") -> str:  # noqa: ANN001 - watch.RoundReport
    """一轮结束后的一句话，给状态行用。"""
    status = str(report.status)
    head = i18n.t(lang, _STATUS_KEYS[status]) if status in _STATUS_KEYS else status
    if report.skipped == "lock":
        return i18n.t(lang, "panel.report_skip_lock")
    details = [head, i18n.t(lang, "panel.report_repos", count=report.repos)]
    if report.failures:
        details.append(i18n.t(lang, "panel.report_failures", count=report.failures))
    if report.sent:
        details.append(i18n.t(lang, "panel.report_sent", count=report.sent))
    elif report.parts:
        details.append(i18n.t(lang, "panel.report_pending", count=len(report.parts)))
    return " · ".join(details)


def preview_text(report, lang: str = "zh") -> str:  # noqa: ANN001 - watch.RoundReport
    """预览抽屉的内容：分片原样拼起来。"""
    if not report.parts:
        return i18n.t(lang, "panel.preview_empty")
    return "\n\n".join(
        f"{i18n.t(lang, 'panel.preview_part', index=index, chars=len(part))}\n{part}"
        for index, part in enumerate(report.parts, 1)
    )


def log_files(root: Path) -> list[Path]:
    logdir = Path(root) / "logs"
    if not logdir.is_dir():
        return []
    return sorted(logdir.glob("watch-*.log"), key=lambda path: path.name)


def tail_log(root: Path, lines: int = 200, lang: str = "zh") -> str:
    """最新一天的日志尾部；没有日志时给一句人话，而不是抛异常。"""
    files = log_files(root)
    if not files:
        return i18n.t(lang, "panel.log_missing")
    try:
        content = files[-1].read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return i18n.t(lang, "panel.log_failed", error=exc)
    tail = content.splitlines()[-lines:]
    return "\n".join(tail) or i18n.t(lang, "panel.log_empty")


def git_available() -> bool:
    return shutil.which("git") is not None


# ---------------------------------------------------------------- widget 层


class Panel:
    """唯一的 Tk 窗口。关闭 = 隐藏，不销毁 —— 销毁会让 mainloop 失去泵。

    lang 在构造时定死（标签建一次）；改了语言保存后由托盘销毁重建本窗口。
    """

    def __init__(
        self,
        root: tk.Tk,
        root_dir: Path,
        config_path: Path,
        on_saved: Callable[[dict], None],
        on_run: Callable[[bool], bool],
        get_status: Callable[[], str],
        task_hint: str | Callable[[], str] = "",
        lang: str = "zh",
    ) -> None:
        self._lang = i18n.normalize_language(lang)
        if tk is None:  # pragma: no cover - 没有 tkinter 时托盘根本不会用面板
            # 与托盘启动弹框同一句：这条只有在绕过 tray.main 直接构造面板时才可能看到。
            raise RuntimeError(self._t("tray.no_tkinter"))
        self._tk_root = root
        self._root_dir = Path(root_dir)
        self._config_path = Path(config_path)
        self._on_saved = on_saved
        self._on_run = on_run
        self._get_status = get_status
        # 计划任务的检测要起子进程：只在建窗口时算一次，不要每次重画都算。
        self._task_hint = task_hint() if callable(task_hint) else str(task_hint)
        self._window: tk.Toplevel | None = None
        self._repos: list[str] = []
        self._vars: dict[str, object] = {}
        self._notice = ""
        self._build()

    def _t(self, key: str, **kwargs: object) -> str:
        return i18n.t(self._lang, key, **kwargs)

    # -- 构建 ----------------------------------------------------------
    def _build(self) -> None:
        window = tk.Toplevel(self._tk_root)
        window.title(self._t("panel.title"))
        window.protocol("WM_DELETE_WINDOW", self.hide)
        window.withdraw()
        window.columnconfigure(0, weight=1)
        window.rowconfigure(2, weight=1)
        self._window = window

        self._status_label = tk.Label(window, anchor="w", justify="left", fg="#0f172a", wraplength=740)
        self._status_label.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 0))
        self._notice_label = tk.Label(window, anchor="w", justify="left", fg="#b45309", wraplength=740)

        notebook = ttk.Notebook(window)
        notebook.grid(row=2, column=0, sticky="nsew", padx=14, pady=(10, 0))
        self._notebook = notebook
        notebook.add(self._build_repos_tab(notebook), text=self._t("panel.tab_repos"))
        notebook.add(self._build_notify_tab(notebook), text=self._t("panel.tab_notify"))
        notebook.add(self._build_schedule_tab(notebook), text=self._t("panel.tab_schedule"))
        notebook.add(self._build_advanced_tab(notebook), text=self._t("panel.tab_advanced"))

        self._error_label = tk.Label(window, anchor="w", justify="left", fg="#b91c1c", wraplength=740)
        self._error_label.grid(row=3, column=0, sticky="ew", padx=14)

        buttons = tk.Frame(window)
        buttons.grid(row=4, column=0, sticky="ew", padx=14, pady=10)
        tk.Button(buttons, text=self._t("panel.save"), width=9, command=self.save).pack(side="left")
        tk.Button(buttons, text=self._t("panel.cancel"), width=7, command=self.hide).pack(side="left", padx=(8, 0))
        tk.Button(buttons, text=self._t("panel.run_now_button"), command=lambda: self._trigger(dry_run=False)).pack(
            side="right"
        )
        tk.Button(buttons, text=self._t("panel.preview_button"), width=7, command=lambda: self._trigger(dry_run=True)).pack(
            side="right", padx=(0, 8)
        )
        tk.Button(buttons, text=self._t("panel.log_button"), width=7, command=self.toggle_log).pack(
            side="right", padx=(0, 8)
        )

        self._log_box = scrolledtext.ScrolledText(window, height=9, state="disabled", wrap="none")
        self._log_visible = False
        self._tick()

        # 让窗口按内容定尺寸：写死 780x620 在别的字体/DPI 下会切掉右边一截。
        window.update_idletasks()
        width = max(760, window.winfo_reqwidth())
        height = max(600, window.winfo_reqheight())
        window.geometry(f"{width}x{height}")
        window.minsize(min(width, 680), min(height, 520))

    def _build_repos_tab(self, parent: tk.Widget) -> tk.Widget:
        frame = tk.Frame(parent)
        tk.Label(frame, text=self._t("panel.repos_label"), anchor="w").pack(fill="x", padx=12, pady=(10, 6))
        body = tk.Frame(frame)
        body.pack(fill="both", expand=True, padx=12)
        scroll = tk.Scrollbar(body)
        scroll.pack(side="right", fill="y")
        listing = tk.Listbox(body, height=10, yscrollcommand=scroll.set, selectmode="extended", activestyle="none")
        listing.pack(side="left", fill="both", expand=True)
        scroll.config(command=listing.yview)
        self._repo_list = listing

        entry_row = tk.Frame(frame)
        entry_row.pack(fill="x", padx=12, pady=8)
        self._repo_entry = tk.Entry(entry_row)
        self._repo_entry.pack(side="left", fill="x", expand=True)
        self._repo_entry.bind("<Return>", lambda _event: self.add_repo())
        tk.Button(entry_row, text=self._t("panel.add"), width=7, command=self.add_repo).pack(side="left", padx=(8, 0))

        actions = tk.Frame(frame)
        actions.pack(fill="x", padx=12, pady=(0, 10))
        tk.Button(actions, text=self._t("panel.remove"), command=self.remove_selected).pack(side="left")
        tk.Button(actions, text=self._t("panel.up"), width=7, command=lambda: self.move_selected(-1)).pack(
            side="left", padx=(8, 0)
        )
        tk.Button(actions, text=self._t("panel.down"), width=7, command=lambda: self.move_selected(1)).pack(
            side="left", padx=(8, 0)
        )
        tk.Label(actions, text=self._t("panel.repos_hint"), fg="#64748b").pack(side="right")
        return frame

    def _build_notify_tab(self, parent: tk.Widget) -> tk.Widget:
        frame = tk.Frame(parent)
        frame.columnconfigure(1, weight=1)
        self._vars["notify"] = tk.BooleanVar(value=True)
        # qq 的三个字段单独建变量：其它通道的字段随 _render_channel_fields 按需建。
        for var in ("openid", "appid", "secret"):
            self._vars.setdefault(var, tk.StringVar())

        # 通道下拉：显示人话、落盘代码 —— 与语言下拉同一套双向映射。
        self._vars["channel"] = tk.StringVar()
        self._channel_display = {code: self._t(f"channel.{code}") for code in channels.CHANNELS}
        self._channel_codes = {display: code for code, display in self._channel_display.items()}
        box = self._field(
            frame,
            0,
            self._t("panel.field_channel"),
            ttk.Combobox,
            textvariable=self._vars["channel"],
            values=list(self._channel_display.values()),
            width=24,
            state="readonly",
            wide=False,
        )
        box.bind("<<ComboboxSelected>>", lambda _event: self._render_channel_fields())

        # 动态凭据行：值都在 _vars 里，切通道只重建 widget，不丢输入。
        self._channel_body = tk.Frame(frame)
        self._channel_body.grid(row=1, column=0, columnspan=2, sticky="ew")

        tk.Checkbutton(frame, text=self._t("panel.notify_label"), variable=self._vars["notify"]).grid(
            row=2, column=1, sticky="w", pady=(14, 2)
        )

        self._env_hint = tk.Label(frame, anchor="w", justify="left", fg="#64748b", wraplength=820)
        self._env_hint.grid(row=3, column=1, sticky="w", pady=(8, 0))
        # 「凭据来源」行：随通道显示现在用的凭据从哪读来的（环境变量 / 某个 .env）。
        self._secret_hint = tk.Label(frame, anchor="w", justify="left", fg="#475569", wraplength=820)
        self._secret_hint.grid(row=4, column=1, sticky="w", pady=(6, 0))
        return frame

    def _render_channel_fields(self) -> None:
        """按当前所选通道重建凭据行（含 qq 的 openid 行 —— 它是 config 键，不是凭据）。"""
        code = self._channel_codes.get(self._vars["channel"].get(), "qq")
        for child in self._channel_body.winfo_children():
            child.destroy()
        self._channel_body.columnconfigure(1, weight=1)
        row = 0
        if code == "qq":
            self._field(
                self._channel_body, row, self._t("panel.field_openid"), tk.Entry,
                textvariable=self._vars["openid"],
            )
            row += 1
        for spec in channels.CREDENTIALS.get(code, ()):
            env_key = str(spec["env"])
            self._vars.setdefault(env_key.lower(), tk.StringVar())
            label = self._t(str(spec["label"]))
            if spec["masked"]:

                def masked_row(holder: tk.Widget, var: str = env_key.lower()) -> tk.Widget:
                    inner = tk.Frame(holder)
                    entry = tk.Entry(inner, textvariable=self._vars[var], show="*")
                    entry.pack(side="left", fill="x", expand=True)
                    tk.Button(
                        inner,
                        text=self._t("panel.show"),
                        width=6,
                        command=lambda e=entry: e.config(show="" if e.cget("show") else "*"),
                    ).pack(side="left", padx=(8, 0))
                    return inner

                self._field(self._channel_body, row, label, masked_row)
            else:
                self._field(
                    self._channel_body, row, label, tk.Entry,
                    textvariable=self._vars[env_key.lower()],
                )
            row += 1
        env_keys = " / ".join(str(spec["env"]) for spec in channels.CREDENTIALS.get(code, ()))
        self._env_hint.config(text=self._t("panel.channel_env_hint", keys=env_keys))
        self._secret_hint.grid()  # 内容随通道由 show() 刷新；这里只确保它可见

    def _build_schedule_tab(self, parent: tk.Widget) -> tk.Widget:
        frame = tk.Frame(parent)
        frame.columnconfigure(1, weight=1)
        self._vars["interval_minutes"] = tk.StringVar()
        self._vars["autostart"] = tk.BooleanVar(value=True)
        self._vars["language"] = tk.StringVar()
        self._field(
            frame, 0, self._t("panel.field_interval"), tk.Entry, textvariable=self._vars["interval_minutes"], width=8, wide=False
        )
        tk.Checkbutton(frame, text=self._t("panel.autostart_label"), variable=self._vars["autostart"]).grid(
            row=1, column=1, sticky="w", pady=(12, 2)
        )
        self._field(
            frame,
            2,
            self._t("panel.field_language"),
            ttk.Combobox,
            textvariable=self._vars["language"],
            values=list(LANG_DISPLAY.values()),
            width=12,
            state="readonly",
            wide=False,
        )
        self._task_label = tk.Label(frame, anchor="w", justify="left", fg="#475569", wraplength=820)
        self._task_label.grid(row=3, column=1, sticky="w", pady=(10, 0))
        self.set_task_hint(self._task_hint)
        return frame

    def _build_advanced_tab(self, parent: tk.Widget) -> tk.Widget:
        frame = tk.Frame(parent)
        frame.columnconfigure(1, weight=1)
        for index, key in enumerate(NUMERIC_FIELDS):
            self._vars[key] = tk.StringVar()
            self._field(
                frame,
                index,
                self._t(f"panel.field_{key}"),
                tk.Entry,
                textvariable=self._vars[key],
                width=10,
                wide=False,
            )

        tk.Label(frame, text=self._t("panel.patterns_label"), anchor="w").grid(
            row=len(NUMERIC_FIELDS), column=0, columnspan=2, sticky="w", padx=12, pady=(14, 4)
        )
        self._patterns = tk.Text(frame, height=4, wrap="none")
        self._patterns.grid(row=len(NUMERIC_FIELDS) + 1, column=0, columnspan=2, sticky="ew", padx=12)

        self._vars["git_ssl_backend"] = tk.StringVar()
        self._field(
            frame,
            len(NUMERIC_FIELDS) + 2,
            "git_ssl_backend",
            ttk.Combobox,
            textvariable=self._vars["git_ssl_backend"],
            values=BACKENDS,
            width=12,
            state="readonly",
            wide=False,
        )
        return frame

    @staticmethod
    def _field(
        parent: tk.Widget,
        row: int,
        label: str,
        factory: Callable[..., tk.Widget],
        *,
        wide: bool = True,
        **kwargs: object,
    ) -> tk.Widget:
        """一行 = 左边标签 + 右边控件。

        控件必须由 factory 用**同一个 parent** 创建：Tk 的 pack/grid 默认落在控件的
        master 上，把在别处创建好的控件塞进 row 会静默排到父容器里 —— 整个面板会散架。
        """
        tk.Label(parent, text=label, anchor="w").grid(row=row, column=0, sticky="w", padx=(12, 10), pady=5)
        widget = factory(parent, **kwargs)  # type: ignore[arg-type]
        widget.grid(row=row, column=1, sticky="ew" if wide else "w", padx=(0, 12), pady=5)
        return widget

    # -- 数据 ----------------------------------------------------------
    def _fill(self, form: dict, notice: str | None) -> None:
        self._repos = list(form["repos"])
        self._refresh_repos()
        for key in ("openid", "appid", "secret", "interval_minutes", "git_ssl_backend"):
            self._vars[key].set(form.get(key, ""))
        channel = channels.normalize_channel(form.get("channel"))
        self._vars["channel"].set(self._channel_display.get(channel, channel))
        # 所有通道的凭据变量都建好：表单里没出现的通道先空着，切过去时已输入的值不丢。
        for code in channels.CHANNELS:
            for spec in channels.CREDENTIALS.get(code, ()):
                var = str(spec["env"]).lower()
                self._vars.setdefault(var, tk.StringVar())
                if var in form:
                    self._vars[var].set(form.get(var, ""))
        self._vars["notify"].set(bool(form.get("notify", True)))
        self._vars["autostart"].set(bool(form.get("autostart", True)))
        language = i18n.normalize_language(form.get("language"))
        self._vars["language"].set(LANG_DISPLAY.get(language, language))
        for key in NUMERIC_FIELDS:
            self._vars[key].set(form.get(key, watch.DEFAULT_CONFIG.get(key, "")))
        self._patterns.delete("1.0", "end")
        self._patterns.insert("1.0", "\n".join(form.get("exclude_ref_patterns", [])))
        self._error_label.config(text="")
        self._set_notice(notice or "")
        self._render_channel_fields()

    def _collect(self) -> dict:
        # 语言/通道选择框显示的是人话；不认识的显示值按原样交给 normalize 兜底。
        language_display = str(self._vars["language"].get())
        channel_display = str(self._vars["channel"].get())
        form = {
            "repos": list(self._repos),
            "channel": self._channel_codes.get(channel_display, channel_display),
            "openid": self._vars["openid"].get(),
            "notify": bool(self._vars["notify"].get()),
            "autostart": bool(self._vars["autostart"].get()),
            "language": LANG_CODES.get(language_display, language_display),
            "exclude_ref_patterns": self._patterns.get("1.0", "end").splitlines(),
            "git_ssl_backend": self._vars["git_ssl_backend"].get(),
        }
        for key in ("interval_minutes", *NUMERIC_FIELDS):
            form[key] = self._vars[key].get()
        return form

    def _refresh_repos(self) -> None:
        self._repo_list.delete(0, "end")
        for slug in self._repos:
            self._repo_list.insert("end", slug)

    # -- 动作 ----------------------------------------------------------
    def add_repo(self) -> None:
        slug = settings.normalize_repo(self._repo_entry.get())
        if not slug:
            return
        if slug.lower() not in {item.lower() for item in self._repos}:
            self._repos.append(slug)
            self._refresh_repos()
        self._repo_entry.delete(0, "end")

    def remove_selected(self) -> None:
        for index in sorted(self._repo_list.curselection(), reverse=True):
            del self._repos[index]
        self._refresh_repos()

    def move_selected(self, delta: int) -> None:
        selection = self._repo_list.curselection()
        if len(selection) != 1:
            return
        index = selection[0]
        target = index + delta
        if not 0 <= target < len(self._repos):
            return
        self._repos[index], self._repos[target] = self._repos[target], self._repos[index]
        self._refresh_repos()
        self._repo_list.selection_set(target)

    def save(self) -> None:
        values = config_from_form(self._collect())
        # 校验报错用表单里新选的语言：正在切语言的人用目标语言看报错。
        errors = settings.validate(values, lang=i18n.normalize_language(values.get("language")))
        if errors:
            self._error_label.config(text="\n".join(errors[:6]))
            return
        # 所选通道的凭据 → .env：键名来自 channels.CREDENTIALS（openid 是 config 键，走上面）。
        code = channels.normalize_channel(values.get("channel"))
        if code == "qq":
            secrets = {
                "QQBOT_APPID": str(self._vars["appid"].get()),
                "QQBOT_SECRET": str(self._vars["secret"].get()),
            }
        else:
            secrets = {
                str(spec["env"]): str(self._vars[str(spec["env"]).lower()].get())
                for spec in channels.CREDENTIALS.get(code, ())
            }
        try:
            settings.save_config(self._config_path, values)
            settings.save_secrets(self._root_dir, secrets)
        except OSError as exc:
            self._error_label.config(text=self._t("panel.notice_save_failed", error=exc))
            return
        self._error_label.config(text="")
        self._set_notice(self._t("panel.notice_saved", path=self._config_path))
        self._on_saved(values)

    def _trigger(self, dry_run: bool) -> None:
        if self._on_run(dry_run):
            self._set_notice(self._t("panel.notice_preview_running" if dry_run else "panel.notice_running"))
        else:
            self._set_notice(self._t("common.round_busy"))

    def toggle_log(self) -> None:
        if self._log_visible:
            self._log_box.grid_remove()
            self._log_visible = False
            return
        self._write_log(tail_log(self._root_dir, lang=self._lang))
        self._log_box.grid(row=5, column=0, sticky="ew", padx=14, pady=(0, 12))
        self._log_visible = True

    def _write_log(self, text: str) -> None:
        self._log_box.config(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.insert("end", text)
        self._log_box.config(state="disabled")

    def append_log(self, text: str) -> None:
        """托盘把事件推过来（已经通过队列桥接，本方法一定在 Tk 线程）。"""
        if self._log_visible:
            self._log_box.config(state="normal")
            self._log_box.insert("end", text + "\n")
            self._log_box.see("end")
            self._log_box.config(state="disabled")

    def set_notice(self, text: str) -> None:
        self._set_notice(text)

    def _set_notice(self, text: str) -> None:
        self._notice = text
        if text:
            self._notice_label.config(text=text)
            self._notice_label.grid(row=1, column=0, sticky="ew", padx=14, pady=(4, 0))
        else:
            self._notice_label.grid_remove()

    def set_task_hint(self, text: str) -> None:
        self._task_hint = text
        if hasattr(self, "_task_label"):
            self._task_label.config(text=text)

    def _tick(self) -> None:
        """每秒刷新状态行（调度器状态在别的线程里变）。"""
        try:
            self._status_label.config(text=self._get_status())
        except RuntimeError:  # pragma: no cover - 窗口已销毁
            return
        self._tk_root.after(1000, self._tick)

    def show(self) -> None:
        form, notice = load_form(self._root_dir, self._config_path)
        self._fill(form, notice)
        # ENV_SOURCE 是「凭据来自环境变量」的哨兵，翻成当前语言；文件路径是数据，原样。
        source = settings.read_secrets(self._root_dir, channels.normalize_channel(form.get("channel"))).get("source")
        if source == qq_notify.ENV_SOURCE:
            source = self._t("panel.cred_from_env")
        elif not source:
            source = self._t("panel.cred_none")
        self._secret_hint.config(text=self._t("panel.cred_source", source=source))
        if not git_available():
            self._set_notice(self._t("panel.notice_no_git"))
        window = self._window
        if window is None:  # pragma: no cover - 构造时一定会有窗口
            return
        window.deiconify()
        window.lift()
        window.attributes("-topmost", True)
        window.after(300, lambda: window.attributes("-topmost", False))
        window.focus_force()

    def hide(self) -> None:
        if self._window is not None:
            self._window.withdraw()

    def destroy(self) -> None:
        if self._window is not None:
            self._window.destroy()
            self._window = None

    @property
    def visible(self) -> bool:
        return self._window is not None and bool(self._window.winfo_viewable())
