#!/usr/bin/env python3
"""窗口探针：在目标命令运行期间枚举「新出现的可见窗口」，用来证明定时任务不再闪黑窗口。

用法（Windows）：
    python tools/window_probe.py -- pythonw.exe -X utf8 watch.py --root . --dry-run

  -- 之后的整段是目标命令；探针只负责观察，不改目标的行为。
  退出码：0 = 没有新出现的控制台窗口且目标成功；1 = 出现控制台窗口，或目标命令失败；2 = 用法错误。
  非 Windows 平台打印 skip 并以 0 退出。

为什么需要它：`pythonw.exe` 自己没有控制台，但它 spawn 的 `git.exe` 是控制台子系统
程序，Windows 会给子进程新分配一个控制台窗口 —— 这正是每轮刷屏的那个黑窗口。
窗口不会出现在任何日志里，单测也抓不到，只能靠枚举窗口来证明。

判定规则：只有「控制台窗口」算回归；其它新窗口（输入法状态窗、任务切换器之类）打印出来
但不算失败 —— 否则探针会整天被别人的窗口误伤。
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time

POLL_SEC = 0.03
SETTLE_SEC = 0.4

# conhost、Windows Terminal 的宿主窗口，以及 pseudo-console 的宿主类名。
CONSOLE_WINDOW_CLASSES = frozenset(
    {
        "ConsoleWindowClass",
        "CASCADIA_HOSTING_WINDOW_CLASS",
        "PseudoConsoleWindow",
    }
)


def visible_windows() -> dict[int, tuple[str, str]]:
    """返回 {hwnd: (class, title)}，只含可见的顶层窗口。"""
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    found: dict[int, tuple[str, str]] = {}

    def on_window(hwnd, _lparam):  # noqa: ANN001 - ctypes 回调签名固定
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, 256)
            found[int(hwnd)] = (class_name.value, title.value)
        return True

    user32.EnumWindows(enum_proc(on_window), 0)
    return found


def watch_command(cmd: list[str]) -> tuple[int, list[tuple[str, str]], list[tuple[str, str]]]:
    """跑目标命令，返回 (退出码, 新出现的控制台窗口, 其它新窗口)。"""
    before = visible_windows()
    started = time.monotonic()
    proc = subprocess.Popen(cmd)
    appeared: dict[int, tuple[str, str]] = {}
    while proc.poll() is None:
        for hwnd, info in visible_windows().items():
            if hwnd not in before:
                appeared[hwnd] = info
        time.sleep(POLL_SEC)
    time.sleep(SETTLE_SEC)
    for hwnd, info in visible_windows().items():
        if hwnd not in before:
            appeared[hwnd] = info
    elapsed = time.monotonic() - started

    consoles = [(cls, title) for cls, title in appeared.values() if cls in CONSOLE_WINDOW_CLASSES]
    others = [(cls, title) for cls, title in appeared.values() if cls not in CONSOLE_WINDOW_CLASSES]
    print(
        f"[probe] 目标退出码 {proc.returncode}，观察 {elapsed:.1f}s，"
        f"新控制台窗口 {len(consoles)} 个，其它新窗口 {len(others)} 个"
    )
    for cls, title in consoles:
        print(f"  [console] class={cls!r} title={title[:80]!r}")
    for cls, title in others:
        print(f"  [noise]   class={cls!r} title={title[:80]!r}（别人的窗口，不算失败）")
    return proc.returncode, consoles, others


def main(argv: list[str]) -> int:
    if "--" not in argv:
        print(__doc__)
        return 2
    cmd = argv[argv.index("--") + 1 :]
    if not cmd:
        print("-- 之后没有命令", file=sys.stderr)
        return 2
    if sys.platform != "win32":
        print("[probe] 非 Windows 平台：没有控制台窗口问题，跳过")
        return 0
    returncode, consoles, _others = watch_command(cmd)
    if consoles:
        print("[probe] 结论：有控制台窗口闪出（回归）", file=sys.stderr)
        return 1
    print("[probe] 结论：没有新控制台窗口")
    return 0 if returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
