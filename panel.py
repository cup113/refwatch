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

import settings  # noqa: E402
import watch  # noqa: E402

try:  # 精简发行版/CI 镜像可能没有 tkinter：纯逻辑部分不该因此不可测。
    import tkinter as tk
    from tkinter import scrolledtext, ttk
except ImportError:  # pragma: no cover
    tk = None  # type: ignore[assignment]
    scrolledtext = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]

TITLE = "refwatch 设置"

# 高级页里的数字项；interval_minutes 单独放在「调度」页。
NUMERIC_FIELDS: tuple[tuple[str, str], ...] = (
    ("max_commits_per_ref", "每个分支最多列出多少条提交"),
    ("max_chunks_per_repo", "每仓库每轮最多发几条"),
    ("chunk_limit", "单条消息字数上限"),
    ("fetch_timeout_sec", "单仓库 fetch 超时（秒）"),
    ("fail_alert_threshold", "连续失败多少次后告警"),
    ("lock_stale_sec", "锁残留判定（秒）"),
)

BACKENDS = ("", "openssl", "schannel")

_STATUS_TEXT = {
    "ok": "完成",
    "dry-run": "预览完成（未发送、未推进基线）",
    "not-sent": "完成（按设置没有发送）",
    "no-openid": "未发送：还没填 openid",
    "token-failed": "未发送：取 QQ access_token 失败",
    "no-repos": "没配仓库",
}


# ---------------------------------------------------------------- 纯逻辑


def form_from_config(cfg: dict, secrets: dict | None = None) -> dict:
    """配置 → 表单值。数字一律转成字符串（Entry 里就是文本），布尔保持布尔。"""
    found = secrets or {"appid": "", "secret": "", "source": ""}
    form: dict = {
        "repos": list(cfg.get("repos") or []),
        "openid": str(cfg.get("openid") or ""),
        "appid": str(found.get("appid") or ""),
        "secret": str(found.get("secret") or ""),
        "notify": bool(cfg.get("notify", True)),
        "autostart": bool(cfg.get("autostart", True)),
        "exclude_ref_patterns": list(cfg.get("exclude_ref_patterns") or []),
        "git_ssl_backend": str(cfg.get("git_ssl_backend", "") or ""),
    }
    for key, _label in NUMERIC_FIELDS:
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
        "openid": str(form.get("openid", "")).strip(),
        "notify": bool(form.get("notify", True)),
        "autostart": bool(form.get("autostart", True)),
        "exclude_ref_patterns": list(form.get("exclude_ref_patterns", [])),
        "git_ssl_backend": str(form.get("git_ssl_backend", "") or ""),
    }
    for key in ("interval_minutes", *(key for key, _ in NUMERIC_FIELDS)):
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
        notice = f"读取配置失败，下面显示的是默认值：{exc}"
    try:
        secrets = settings.read_secrets(root)
    except OSError as exc:  # 读 .env 出错不该让面板起不来
        secrets = {"appid": "", "secret": "", "source": "", "error": str(exc)}
    return form_from_config(cfg, secrets), notice


def format_report(report) -> str:  # noqa: ANN001 - watch.RoundReport
    """一轮结束后的一句话，给状态行用。"""
    head = _STATUS_TEXT.get(report.status, report.status)
    if report.skipped == "lock":
        return "跳过：上一轮仍在运行"
    details = [head, f"{report.repos} 个仓库"]
    if report.failures:
        details.append(f"{report.failures} 个失败")
    if report.sent:
        details.append(f"已推送 {report.sent} 条")
    elif report.parts:
        details.append(f"{len(report.parts)} 条待发")
    return " · ".join(details)


def preview_text(report) -> str:  # noqa: ANN001 - watch.RoundReport
    """预览抽屉的内容：分片原样拼起来。"""
    if not report.parts:
        return "（这一轮没有要发送的内容）"
    return "\n\n".join(f"----- 第 {index} 条（{len(part)} 字）-----\n{part}" for index, part in enumerate(report.parts, 1))


def log_files(root: Path) -> list[Path]:
    logdir = Path(root) / "logs"
    if not logdir.is_dir():
        return []
    return sorted(logdir.glob("watch-*.log"), key=lambda path: path.name)


def tail_log(root: Path, lines: int = 200) -> str:
    """最新一天的日志尾部；没有日志时给一句人话，而不是抛异常。"""
    files = log_files(root)
    if not files:
        return "还没有日志 —— 跑一轮之后这里就会有内容。"
    try:
        content = files[-1].read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"读日志失败：{exc}"
    tail = content.splitlines()[-lines:]
    return "\n".join(tail) or "（日志是空的）"


def git_available() -> bool:
    return shutil.which("git") is not None


# ---------------------------------------------------------------- widget 层


class Panel:
    """唯一的 Tk 窗口。关闭 = 隐藏，不销毁 —— 销毁会让 mainloop 失去泵。"""

    def __init__(
        self,
        root: tk.Tk,
        root_dir: Path,
        config_path: Path,
        on_saved: Callable[[dict], None],
        on_run: Callable[[bool], bool],
        get_status: Callable[[], str],
        task_hint: str | Callable[[], str] = "",
    ) -> None:
        if tk is None:  # pragma: no cover - 没有 tkinter 时托盘根本不会用面板
            raise RuntimeError("这台机器没有 tkinter，无法显示设置面板")
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

    # -- 构建 ----------------------------------------------------------
    def _build(self) -> None:
        window = tk.Toplevel(self._tk_root)
        window.title(TITLE)
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
        notebook.add(self._build_repos_tab(notebook), text="仓库")
        notebook.add(self._build_notify_tab(notebook), text="通知")
        notebook.add(self._build_schedule_tab(notebook), text="调度")
        notebook.add(self._build_advanced_tab(notebook), text="高级")

        self._error_label = tk.Label(window, anchor="w", justify="left", fg="#b91c1c", wraplength=740)
        self._error_label.grid(row=3, column=0, sticky="ew", padx=14)

        buttons = tk.Frame(window)
        buttons.grid(row=4, column=0, sticky="ew", padx=14, pady=10)
        tk.Button(buttons, text="保存", width=9, command=self.save).pack(side="left")
        tk.Button(buttons, text="取消", width=7, command=self.hide).pack(side="left", padx=(8, 0))
        tk.Button(buttons, text="立即运行一轮", command=lambda: self._trigger(dry_run=False)).pack(side="right")
        tk.Button(buttons, text="预览", width=7, command=lambda: self._trigger(dry_run=True)).pack(
            side="right", padx=(0, 8)
        )
        tk.Button(buttons, text="日志", width=7, command=self.toggle_log).pack(side="right", padx=(0, 8))

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
        tk.Label(
            frame, text="被监视仓库（owner/name；也可以直接粘贴 GitHub 链接）", anchor="w"
        ).pack(fill="x", padx=12, pady=(10, 6))
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
        tk.Button(entry_row, text="添加", width=7, command=self.add_repo).pack(side="left", padx=(8, 0))

        actions = tk.Frame(frame)
        actions.pack(fill="x", padx=12, pady=(0, 10))
        tk.Button(actions, text="删除选中", command=self.remove_selected).pack(side="left")
        tk.Button(actions, text="上移", width=7, command=lambda: self.move_selected(-1)).pack(side="left", padx=(8, 0))
        tk.Button(actions, text="下移", width=7, command=lambda: self.move_selected(1)).pack(side="left", padx=(8, 0))
        tk.Label(actions, text="新仓库首次运行是静默的（只建基线）", fg="#64748b").pack(side="right")
        return frame

    def _build_notify_tab(self, parent: tk.Widget) -> tk.Widget:
        frame = tk.Frame(parent)
        frame.columnconfigure(1, weight=1)
        self._vars["openid"] = tk.StringVar()
        self._vars["appid"] = tk.StringVar()
        self._vars["secret"] = tk.StringVar()
        self._vars["notify"] = tk.BooleanVar(value=True)

        self._field(frame, 0, "接收方 openid", tk.Entry, textvariable=self._vars["openid"])
        self._field(frame, 1, "机器人 AppID", tk.Entry, textvariable=self._vars["appid"])

        def secret_row(holder: tk.Widget) -> tk.Widget:
            row = tk.Frame(holder)
            entry = tk.Entry(row, textvariable=self._vars["secret"], show="*")
            entry.pack(side="left", fill="x", expand=True)
            self._secret_entry = entry
            tk.Button(row, text="显示", width=6, command=self._toggle_secret).pack(side="left", padx=(8, 0))
            return row

        self._field(frame, 2, "机器人 AppSecret", secret_row)

        tk.Checkbutton(
            frame, text="启用 QQ 推送（关闭 = 只记录状态，从不发送）", variable=self._vars["notify"]
        ).grid(row=3, column=1, sticky="w", pady=(14, 2))

        self._secret_hint = tk.Label(frame, anchor="w", justify="left", fg="#475569", wraplength=820)
        self._secret_hint.grid(row=4, column=1, sticky="w", pady=(6, 0))
        tk.Label(
            frame,
            anchor="w",
            justify="left",
            fg="#64748b",
            wraplength=820,
            text="凭据写在数据目录的 .env 里（QQBOT_APPID / QQBOT_SECRET），环境变量优先；"
            "这里的值不会进 config.json。",
        ).grid(row=5, column=1, sticky="w", pady=(6, 0))
        return frame

    def _build_schedule_tab(self, parent: tk.Widget) -> tk.Widget:
        frame = tk.Frame(parent)
        frame.columnconfigure(1, weight=1)
        self._vars["interval_minutes"] = tk.StringVar()
        self._vars["autostart"] = tk.BooleanVar(value=True)
        self._field(
            frame, 0, "调度周期（分钟）", tk.Entry, textvariable=self._vars["interval_minutes"], width=8, wide=False
        )
        tk.Checkbutton(frame, text="登录时自动启动（写 HKCU 的 Run 项）", variable=self._vars["autostart"]).grid(
            row=1, column=1, sticky="w", pady=(12, 2)
        )
        self._task_label = tk.Label(frame, anchor="w", justify="left", fg="#475569", wraplength=820)
        self._task_label.grid(row=2, column=1, sticky="w", pady=(10, 0))
        self.set_task_hint(self._task_hint)
        return frame

    def _build_advanced_tab(self, parent: tk.Widget) -> tk.Widget:
        frame = tk.Frame(parent)
        frame.columnconfigure(1, weight=1)
        for index, (key, label) in enumerate(NUMERIC_FIELDS):
            self._vars[key] = tk.StringVar()
            self._field(
                frame, index, label, tk.Entry, textvariable=self._vars[key], width=10, wide=False
            )

        tk.Label(frame, text="exclude_ref_patterns（每行一个 fnmatch 模式）", anchor="w").grid(
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
        self._vars["notify"].set(bool(form.get("notify", True)))
        self._vars["autostart"].set(bool(form.get("autostart", True)))
        for key, _label in NUMERIC_FIELDS:
            self._vars[key].set(form.get(key, watch.DEFAULT_CONFIG.get(key, "")))
        self._patterns.delete("1.0", "end")
        self._patterns.insert("1.0", "\n".join(form.get("exclude_ref_patterns", [])))
        self._error_label.config(text="")
        self._set_notice(notice or "")

    def _collect(self) -> dict:
        form = {
            "repos": list(self._repos),
            "openid": self._vars["openid"].get(),
            "notify": bool(self._vars["notify"].get()),
            "autostart": bool(self._vars["autostart"].get()),
            "exclude_ref_patterns": self._patterns.get("1.0", "end").splitlines(),
            "git_ssl_backend": self._vars["git_ssl_backend"].get(),
        }
        for key in ("interval_minutes", *(key for key, _ in NUMERIC_FIELDS)):
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

    def _toggle_secret(self) -> None:
        self._secret_entry.config(show="" if self._secret_entry.cget("show") else "*")

    def save(self) -> None:
        values = config_from_form(self._collect())
        errors = settings.validate(values)
        if errors:
            self._error_label.config(text="\n".join(errors[:6]))
            return
        try:
            settings.save_config(self._config_path, values)
            settings.save_secrets(
                self._root_dir, str(self._vars["appid"].get()), str(self._vars["secret"].get())
            )
        except OSError as exc:
            self._error_label.config(text=f"保存失败：{exc}")
            return
        self._error_label.config(text="")
        self._set_notice(f"已保存到 {self._config_path}")
        self._on_saved(values)

    def _trigger(self, dry_run: bool) -> None:
        if self._on_run(dry_run):
            self._set_notice("预览运行中…" if dry_run else "运行中…")
        else:
            self._set_notice("上一轮还没结束，先等它跑完。")

    def toggle_log(self) -> None:
        if self._log_visible:
            self._log_box.grid_remove()
            self._log_visible = False
            return
        self._write_log(tail_log(self._root_dir))
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
        source = settings.read_secrets(self._root_dir).get("source") or "（还没找到）"
        self._secret_hint.config(text=f"当前凭据来源：{source}")
        if not git_available():
            self._set_notice("没找到 git —— 抓取会全部失败，请先安装 Git for Windows。")
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
