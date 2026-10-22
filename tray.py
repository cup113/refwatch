#!/usr/bin/env python3
"""refwatch 托盘应用 —— Windows 上的默认形态。

它在通知区域常驻，按 `interval_minutes` 自己跑轮次，并提供图形化的配置入口。
一轮的全部逻辑仍然在 `watch.run_round()` 里：本文件只负责「什么时候跑」和「怎么显示」。

命令行：
    refwatch.exe                      # 托盘常驻（等价于 --tray）
    refwatch.exe --one-round          # 只跑一轮就退出（计划任务 / 窗口探针用）
    refwatch.exe --one-round --dry-run --root <dir>   # 预览一轮，不发送、不推进基线
    python tray.py --one-round ...    # 源码形态同理

几条不能忘的约束（改动前先读注释指向的文档）：
  * Tk 的所有操作都在主线程；pystray 的菜单回调在它自己的线程 —— 必须走 _in_tk 桥接。
  * 任何控制台子进程（schtasks 等）都要带 CREATE_NO_WINDOW，理由同 docs/adr/0003。
  * 没有控制台，所以启动期异常必须落盘 + 弹框，否则用户看到的是「双击没反应」。
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

# 第三方依赖：**顶层导入、不 lazy**。`pystray` 在包导入时就会挑好后端
# （`Icon = backend().Icon`），所以这一行同时把 pystray._win32 / PIL / six 拉进来 ——
# exe 的冒烟测试 `refwatch.exe --one-round` 因此会真正加载它们，打包少了模块当场失败，
# 而不是等用户双击图标才发现。
from PIL import Image
from pystray import Icon, Menu, MenuItem

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import panel  # noqa: E402
import scheduler as scheduler_module  # noqa: E402
import settings  # noqa: E402
import watch  # noqa: E402

MUTEX_NAME = "Local\\refwatch-tray"
TASK_NAME = "refwatch"
ICON_NAME = "refwatch.ico"

# 与 watch.py 同一份（ADR-0003）：GUI 进程 spawn 控制台程序不给这个 flag 就会闪窗。
_SPAWN_KW: dict = dict(watch._WINDOWS_SPAWN_KW)

_MB_ICONERROR = 0x10
_MB_ICONQUESTION = 0x20
_MB_ICONINFORMATION = 0x40
_MB_YESNO = 0x04
_MB_TOPMOST = 0x00040000
_IDYES = 6

_SECRET_LINE = re.compile(
    r"(?i)(?P<key>qqbot_appid|qqbot_secret|app_?secret|client_?secret|access_token|secret|token)"
    r"(?P<sep>\s*[=:]\s*[\"']?)(?P<value>[^\s\"',}]+)"
)
# Authorization 头整行到行尾都是凭据（`QQBot <token>` 里带空格，单独处理）。
_AUTH_LINE = re.compile(r"(?im)(?P<key>authorization)(?P<sep>\s*[:=]\s*)(?P<value>.+)$")


# ---------------------------------------------------------------- 可见性


def show_message(title: str, message: str, *, error: bool = False) -> None:
    """没有控制台时的唯一出路：置顶弹框（标准库 ctypes，不引 pywin32）。"""
    windll = getattr(ctypes, "windll", None)
    if windll is None:  # pragma: no cover - 非 Windows
        return
    flags = (_MB_ICONERROR if error else _MB_ICONINFORMATION) | _MB_TOPMOST
    with contextlib.suppress(Exception):
        windll.user32.MessageBoxW(None, message, title, flags)


def ask_yes_no(title: str, message: str) -> bool:
    windll = getattr(ctypes, "windll", None)
    if windll is None:  # pragma: no cover - 非 Windows
        return False
    with contextlib.suppress(Exception):
        answer = windll.user32.MessageBoxW(None, message, title, _MB_YESNO | _MB_ICONQUESTION | _MB_TOPMOST)
        return int(answer) == _IDYES
    return False


def redact(text: str) -> str:
    """写盘/弹框前把凭据打码 —— 崩溃堆栈里可能带着 cfg 或 .env 的内容。"""
    text = _AUTH_LINE.sub(lambda match: f"{match.group('key')}{match.group('sep')}***", text)
    return _SECRET_LINE.sub(lambda match: f"{match.group('key')}{match.group('sep')}***", text)


def write_crash_log(root: Path, text: str) -> Path | None:
    try:
        logdir = Path(root) / "logs"
        logdir.mkdir(parents=True, exist_ok=True)
        path = logdir / "crash.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} 未捕获异常\n{redact(text)}\n")
        return path
    except OSError:
        return None


# ---------------------------------------------------------------- 子进程


def run_hidden(args: list[str], timeout: float = 10.0) -> tuple[int, str]:
    """跑一个隐藏的控制台程序并收下输出（GUI 进程里必须带 CREATE_NO_WINDOW）。"""
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout, **_SPAWN_KW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def task_present(name: str = TASK_NAME) -> bool:
    code, _out = run_hidden(["schtasks", "/query", "/tn", name], timeout=5.0)
    return code == 0


def remove_task(name: str = TASK_NAME) -> bool:
    code, _out = run_hidden(["schtasks", "/delete", "/tn", name, "/f"], timeout=10.0)
    return code == 0


class SingleInstance:
    """进程级互斥量：同名互斥量已存在 → 已经有托盘在跑。"""

    ERROR_ALREADY_EXISTS = 183

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self.name = name
        self._handle: int | None = None

    def acquire(self) -> bool:
        if os.name != "nt":  # pragma: no cover - 非 Windows
            return False
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            return False
        if kernel32.GetLastError() == self.ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        with contextlib.suppress(Exception):
            ctypes.windll.kernel32.CloseHandle(self._handle)
        self._handle = None


# ---------------------------------------------------------------- 事件文案


def format_event(event: dict) -> str:
    """把 on_event 的载荷变成面板日志抽屉里的一行。"""
    stamp = f"{datetime.now():%H:%M:%S}"
    kind = event.get("kind")
    if kind == "round-start":
        suffix = "，预览" if event.get("dry_run") else ""
        return f"{stamp} 开始一轮（{event.get('repos')} 个仓库{suffix}）"
    if kind == "repo-start":
        return f"{stamp} {event.get('repo')} 检查中…"
    if kind == "repo-done":
        return f"{stamp} {event.get('repo')} 完成（{event.get('parts')} 条消息）"
    if kind == "repo-error":
        return f"{stamp} {event.get('repo')} 失败：{event.get('error')}"
    if kind == "sent":
        return f"{stamp} 已推送 {event.get('index')}/{event.get('total')}（{event.get('chars')} 字）"
    if kind == "round-done":
        return f"{stamp} 一轮结束：{event.get('status')}"
    return f"{stamp} {kind}"


def enable_dpi_awareness() -> None:
    """让 Tk 按物理像素布局。

    不做这一步，Windows 会把整个窗口按位图拉伸（高分屏上发虚），而且 Tk 报告的
    尺寸与屏幕像素对不上 —— 截图、控件对齐、窗口大小都会跟着错。
    """
    if os.name != "nt":  # pragma: no cover - 非 Windows
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        return
    except (AttributeError, OSError):
        pass
    with contextlib.suppress(Exception):
        ctypes.windll.user32.SetProcessDPIAware()  # Win7 兜底


def load_icon_image(path: Path | None = None) -> Image.Image:
    """托盘图标。资产缺失时给一个能用的替代，不让程序因为图标起不来。"""
    candidate = Path(path) if path is not None else settings.app_dir() / "assets" / ICON_NAME
    if candidate.is_file():
        try:
            with Image.open(candidate) as image:
                return image.convert("RGBA").copy()
        except OSError:
            pass
    fallback = Image.new("RGBA", (32, 32), (15, 23, 42, 255))
    return fallback


# ---------------------------------------------------------------- 应用


class App:
    def __init__(self, root: Path, config_path: Path | None = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.config_path = Path(config_path) if config_path else settings.resolve_config_path(self.root)
        self.cfg = settings.load(self.root, self.config_path)

        self._dialogs: queue.Queue = queue.Queue()
        self._round_guard = threading.Lock()
        self._last_report = None  # watch.RoundReport
        self._note = ""
        self._panel: panel.Panel | None = None

        self._tk_root = tk.Tk()
        self._tk_root.withdraw()
        self._tk_root.title("refwatch")

        self.scheduler = scheduler_module.Scheduler(
            self._run_scheduled_round,
            interval_minutes=int(self.cfg.get("interval_minutes") or 30),
            on_change=self._redraw_menu,
        )
        self.icon = Icon("refwatch", load_icon_image(), "refwatch", self.build_menu())
        self._tk_root.after(100, self._pump)

    # -- 线程桥接 ------------------------------------------------------
    def _in_tk(self, callback: Callable[[], None]) -> None:
        """把回调丢到 Tk 线程执行（pystray 的菜单回调在它自己的线程里）。"""
        self._dialogs.put(callback)

    def _pump(self) -> None:
        try:
            while True:
                callback = self._dialogs.get_nowait()
                try:
                    callback()
                except Exception:  # noqa: BLE001 - 回调里的异常不能杀掉泵
                    write_crash_log(self.root, traceback.format_exc())
        except queue.Empty:
            pass
        self._tk_root.after(100, self._pump)

    # -- 菜单 ----------------------------------------------------------
    def build_menu(self) -> Menu:
        """构造菜单。回调参数个数是硬契约：text/checked 收 MenuItem（1 参），
        action 收 (icon, item)（2 参）—— 多了 pystray 会在构造期就抛 ValueError。"""
        return Menu(
            MenuItem(self._status_text, None, enabled=False),
            MenuItem(self._next_text, None, enabled=False),
            Menu.SEPARATOR,
            MenuItem("立即运行一轮", self._on_run_now),
            MenuItem("预览（不发送）", self._on_preview),
            MenuItem("打开设置…", self._on_open_panel, default=True),
            Menu.SEPARATOR,
            MenuItem("打开日志", self._on_open_logs),
            MenuItem("打开数据目录", self._on_open_data),
            MenuItem("开机自启", self._on_toggle_autostart, checked=self._autostart_checked),
            MenuItem("暂停调度", self._on_toggle_paused, checked=self._paused_checked),
            Menu.SEPARATOR,
            MenuItem("退出", self._on_quit),
        )

    def _status_text(self, _item: object) -> str:
        if self.scheduler.running:
            return "状态：运行中…"
        if self.scheduler.paused:
            return "状态：已暂停"
        return "状态：正常"

    def _next_text(self, _item: object) -> str:
        if self.scheduler.paused:
            return "下次运行：已暂停"
        when = self.scheduler.next_run_at()
        return f"下次运行：{when:%H:%M}" if when else "下次运行：——"

    def _autostart_checked(self, _item: object) -> bool:
        return bool(self.cfg.get("autostart", True))

    def _paused_checked(self, _item: object) -> bool:
        return self.scheduler.paused

    def _redraw_menu(self) -> None:
        with contextlib.suppress(Exception):
            self.icon.update_menu()

    # -- 动作（都跑在 pystray 线程，必须桥接） --------------------------
    def _on_run_now(self, _icon: object, _item: object) -> None:
        if not self.scheduler.trigger_now():
            self._set_note("上一轮还没结束，先等它跑完。")

    def _on_preview(self, _icon: object, _item: object) -> None:
        self.launch(dry_run=True)

    def _on_open_panel(self, _icon: object, _item: object) -> None:
        self._in_tk(self.open_panel)

    def _on_open_logs(self, _icon: object, _item: object) -> None:
        self._in_tk(lambda: self._open_path(self.root / "logs"))

    def _on_open_data(self, _icon: object, _item: object) -> None:
        self._in_tk(lambda: self._open_path(self.root))

    def _on_toggle_autostart(self, _icon: object, _item: object) -> None:
        enabled = not bool(self.cfg.get("autostart", True))
        self._in_tk(lambda: self.set_autostart(enabled))

    def _on_toggle_paused(self, _icon: object, _item: object) -> None:
        if self.scheduler.paused:
            self.scheduler.resume()
            self._set_note("已恢复调度")
        else:
            self.scheduler.pause()
            self._set_note("已暂停调度（手动一轮仍可用）")

    def _on_quit(self, _icon: object, _item: object) -> None:
        self._in_tk(self.request_quit)

    # -- 一轮 ----------------------------------------------------------
    def _run_scheduled_round(self) -> None:
        self._run_round(dry_run=False)

    def launch(self, dry_run: bool) -> bool:
        """另起线程跑一轮（预览或手动），立刻返回；已经在跑则拒绝。"""
        if self._round_guard.locked():
            self._set_note("上一轮还没结束，先等它跑完。")
            return False
        threading.Thread(
            target=self._guarded_round, args=(dry_run,), name="refwatch-preview", daemon=True
        ).start()
        return True

    def _guarded_round(self, dry_run: bool) -> None:
        if not self._round_guard.acquire(blocking=False):
            return
        try:
            self._run_round(dry_run)
        finally:
            self._round_guard.release()

    def _run_round(self, dry_run: bool) -> None:
        try:
            cfg = settings.load(self.root, self.config_path)  # 每轮重读：面板一保存就生效
        except (OSError, ValueError) as exc:
            self._set_note(f"配置读不出来，本轮跳过：{exc}")
            return
        self.cfg = cfg
        report = watch.run_round(self.root, cfg, dry_run=dry_run, on_event=self._on_event)
        self._last_report = report
        self._in_tk(lambda: self._after_round(report))

    def _on_event(self, event: dict) -> None:
        if not self.panel_visible():
            return
        line = format_event(event)
        self._in_tk(lambda: self._append_log(line))

    def _after_round(self, report) -> None:  # noqa: ANN001 - watch.RoundReport
        self._set_note(panel.format_report(report))
        if self.panel_visible():
            self._append_log(panel.format_report(report))
            if report.status == "dry-run":
                self._append_log(panel.preview_text(report))

    # -- 面板 ----------------------------------------------------------
    def panel_visible(self) -> bool:
        return self._panel is not None and self._panel.visible

    def open_panel(self) -> None:
        if self._panel is None:
            self._panel = panel.Panel(
                self._tk_root,
                self.root,
                self.config_path,
                on_saved=self.on_saved,
                on_run=self.launch,
                get_status=self.status_line,
                task_hint=self.task_hint,
            )
        self._panel.show()

    def _append_log(self, line: str) -> None:
        if self._panel is not None:
            self._panel.append_log(line)

    def on_saved(self, values: dict) -> None:
        """面板保存成功（已在 Tk 线程）：把能立刻生效的东西立刻生效。"""
        self.cfg = settings.load(self.root, self.config_path)
        self.scheduler.set_interval(int(self.cfg.get("interval_minutes") or 30))
        with contextlib.suppress(OSError):
            settings.sync_autostart(bool(self.cfg.get("autostart", True)))
        self._redraw_menu()

    def set_autostart(self, enabled: bool) -> None:
        """菜单里的开关：注册表与 config.json 一起改（配置是真相，注册表是投影）。"""
        self.cfg["autostart"] = enabled
        try:
            settings.sync_autostart(enabled)
            settings.save_config(self.config_path, {"autostart": enabled})
        except OSError as exc:
            self._set_note(f"改开机自启失败：{exc}")
            return
        self._set_note(f"开机自启：{'已开启' if enabled else '已关闭'}")
        self._redraw_menu()

    # -- 状态 ----------------------------------------------------------
    def status_line(self) -> str:
        parts: list[str] = []
        if self.scheduler.running:
            parts.append("运行中…")
        elif self.scheduler.paused:
            parts.append("已暂停")
        else:
            when = self.scheduler.next_run_at()
            parts.append(f"下次 {when:%H:%M}" if when else "未调度")
        if self._last_report is not None:
            parts.append(panel.format_report(self._last_report))
        if self._note:
            parts.append(self._note)
        return " · ".join(parts)

    def _set_note(self, text: str) -> None:
        self._note = text
        self._redraw_menu()
        if self.panel_visible() and self._panel is not None:
            self._panel.set_notice(text)

    def task_hint(self) -> str:
        if task_present():
            return (
                "检测到同名计划任务 refwatch：它和托盘会互相抢锁。\n"
                "建议在「任务计划程序」里移除它（或删掉后重启本程序会再问一次）。"
            )
        return "没有检测到同名计划任务，调度由本程序负责。"

    # -- 平台动作 ------------------------------------------------------
    def _open_path(self, path: Path) -> None:
        target = Path(path)
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(target))  # noqa: S606 - Windows 上打开资源管理器
        except OSError as exc:
            self._set_note(f"打开 {target} 失败：{exc}")

    def request_quit(self) -> None:
        self.scheduler.stop(timeout=5.0)
        with contextlib.suppress(Exception):
            self.icon.stop()
        self._tk_root.quit()

    # -- 启动/收尾 -----------------------------------------------------
    def prepare(self) -> None:
        """启动时的一次性工作：自启对账、旧计划任务询问。"""
        with contextlib.suppress(OSError):
            settings.sync_autostart(bool(self.cfg.get("autostart", True)))
        self._offer_task_removal()

    def _offer_task_removal(self) -> None:
        if self.cfg.get("task_prompt_done"):
            return
        if task_present():
            if ask_yes_no(
                "refwatch",
                "检测到旧的计划任务 refwatch（每 N 分钟跑一次 watch.py）。\n\n"
                "托盘应用已经接管调度，两者同时存在只会在 .lock 上互相跳过。\n"
                "现在移除该计划任务吗？",
            ):
                remove_task()
                self._set_note("已移除旧的计划任务")
        self._mark_task_prompt_done()

    def _mark_task_prompt_done(self) -> None:
        with contextlib.suppress(OSError):
            settings.save_config(self.config_path, {"task_prompt_done": True})
            self.cfg["task_prompt_done"] = True

    def run(self) -> None:
        self.icon.run_detached()
        self.scheduler.start()
        # 一次性启动工作放进事件循环：图标先出现，再问「要不要移除旧计划任务」——
        # 顺序反过来的话，用户在回答之前看不到任何东西（这比问题本身更让人困惑）。
        self._tk_root.after(300, self.prepare)
        try:
            self._tk_root.mainloop()
        finally:
            self.scheduler.stop(timeout=5.0)
            with contextlib.suppress(Exception):
                self.icon.stop()
            if self._panel is not None:
                with contextlib.suppress(Exception):
                    self._panel.destroy()
            with contextlib.suppress(Exception):
                self._tk_root.destroy()


def split_args(argv: list[str]) -> tuple[Path, Path | None, list[str]]:
    """把 --root/--config 摘出来，其余原样留给 watch.main（无头路径）。"""
    root = settings.default_root()
    config_path: Path | None = None
    rest: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item in ("--root", "--config") and index + 1 < len(argv):
            value = argv[index + 1]
            index += 2
            if item == "--root":
                root = Path(value).expanduser().resolve()
            else:
                config_path = Path(value).expanduser().resolve()
            continue
        if item.startswith("--root="):
            root = Path(item.split("=", 1)[1]).expanduser().resolve()
        elif item.startswith("--config="):
            config_path = Path(item.split("=", 1)[1]).expanduser().resolve()
        elif item != "--tray":
            rest.append(item)
        index += 1
    return root, config_path, rest


def is_headless(rest: list[str]) -> bool:
    """带这些参数就是「跑一轮就退出」，不启动托盘 —— 计划任务与窗口探针靠它。"""
    headless_flags = ("--dry-run", "--no-notify", "--repo")
    return any(item in headless_flags or item.startswith("--repo=") for item in rest) or "--one-round" in rest


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root, config_path, rest = split_args(args)
    headless = is_headless(rest)

    try:
        if headless:
            passthrough = [item for item in rest if item != "--one-round"]
            # 配置落点必须显式传：watch.main 默认在「脚本目录」找 config.json，
            # 而托盘的数据目录是 --root（%LOCALAPPDATA%\refwatch）。
            resolved = settings.resolve_config_path(root, config_path)
            return watch.main(["--root", str(root), "--config", str(resolved), *passthrough])

        if os.name != "nt":
            print("refwatch 托盘应用只支持 Windows；无头用法请直接跑 watch.py。", file=sys.stderr)
            return 2
        if panel.tk is None:  # pragma: no cover - 装了 Python 就有 tkinter
            show_message("refwatch", "这台机器的 Python 没有 tkinter，无法显示托盘与设置面板。", error=True)
            return 2

        instance = SingleInstance()
        if not instance.acquire():
            show_message(
                "refwatch",
                "refwatch 已经在运行。\n\n图标在通知区域 —— 看不到就点任务栏的「^」展开。",
            )
            return 0
        enable_dpi_awareness()  # 必须在创建 Tk 之前
        try:
            App(root, config_path).run()
        finally:
            instance.release()
    except KeyboardInterrupt:
        return 130
    except BaseException:
        text = traceback.format_exc()
        print(text, file=sys.stderr)
        path = write_crash_log(root, text)
        if not headless:
            # 无头模式（计划任务/CI）不弹框：没人点它，只会把进程/流水线卡住。
            detail = text.strip().splitlines()[-1]
            show_message(
                "refwatch 启动失败",
                f"{detail}\n\n完整堆栈：{path}" if path else detail,
                error=True,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
