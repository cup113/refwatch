"""托盘应用的契约守卫：菜单回调参数个数、单实例互斥量、脱敏、隐藏子进程、参数分流。

这些 bug 的共同点是「只在装配期/无控制台时暴露」：菜单回调多一个参数 → 构造
MenuItem 时就抛 ValueError，而 pythonw 下没有 stderr，用户只看到「双击没反应」。
所以这里断言的是形状，而不是行为。

没有 pystray/PIL（或不在 Windows）时整个模块跳过 —— 零依赖 CI 矩阵照样绿。
"""

from __future__ import annotations

import ctypes
import importlib.util
import inspect
import os
import queue
import sys
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from helpers import rmtree, temp_root  # noqa: E402

import scheduler as scheduler_module  # noqa: E402
import watch  # noqa: E402

HAS_TRAY = os.name == "nt" and all(
    importlib.util.find_spec(name) is not None for name in ("pystray", "PIL", "tkinter")
)
if HAS_TRAY:
    import tray
else:  # pragma: no cover - 非 Windows 或没装 GUI 依赖
    tray = None


class FakeIcon:
    def __init__(self) -> None:
        self.stopped = False
        self.updates = 0

    def update_menu(self) -> None:
        self.updates += 1

    def stop(self) -> None:
        self.stopped = True


def make_app(root: Path):  # noqa: ANN201 - 返回 tray.App，本模块在没装依赖时不可用
    """不构造 Tk/Icon，只装配菜单回调真正用到的那几个属性。

    菜单契约的 bug 就发生在装配期，所以这里必须用真实的 App 方法（绑定方法），
    否则测的是复制品。
    """
    app = tray.App.__new__(tray.App)
    app.root = Path(root)
    app.config_path = Path(root) / "config.json"
    app.cfg = dict(watch.DEFAULT_CONFIG)
    app.scheduler = scheduler_module.Scheduler(lambda: None)
    app.icon = FakeIcon()
    app._dialogs = queue.Queue()
    app._round_guard = threading.Lock()
    app._last_report = None
    app._note = ""
    app._panel = None
    return app


@unittest.skipUnless(tray is not None, "需要 Windows + pystray + PIL + tkinter")
class MenuContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = temp_root()
        self.addCleanup(rmtree, self.root)
        self.app = make_app(self.root)
        self.menu = self.app.build_menu()

    def items(self) -> list:
        return [item for item in self.menu if item is not None and item.text not in ("", "- - - -")]

    def test_menu_constructs_and_has_entries(self) -> None:
        actions = [item for item in self.menu if item._action is not None]
        self.assertGreaterEqual(len(actions), 5, "菜单里应当有立即运行/预览/设置/日志/退出")

    def test_text_and_checked_callbacks_take_one_argument(self) -> None:
        for item in self.items():
            text = item.text
            self.assertIsInstance(text, str)
            self.assertEqual(text, item.text, f"{text} 的 text 回调两次求值结果不同")
            self.assertIn(item.checked, (True, False, None))

    def test_actions_take_at_most_two_arguments(self) -> None:
        for item in self.menu:
            action = item._action
            if action is None or not hasattr(action, "__code__"):
                continue
            count = action.__code__.co_argcount - (1 if inspect.ismethod(action) else 0)
            self.assertLessEqual(count, 2, f"{item.text} 的 action 有 {count} 个参数（pystray 只接受 0/1/2）")

    def test_checkable_items_report_a_real_bool(self) -> None:
        checked = {item.text: item.checked for item in self.items() if item.checked is not None}
        self.assertIn("开机自启", checked)
        self.assertIn("暂停调度", checked)
        self.assertIs(checked["开机自启"], True, "默认配置里自启是开的")
        self.assertIs(checked["暂停调度"], False)

    def test_pause_toggle_is_reflected_in_the_menu(self) -> None:
        self.app.scheduler.pause()
        paused = {item.text: item.checked for item in self.items()}
        self.assertIs(paused["暂停调度"], True)
        self.assertIn("已暂停", self.app._status_text(None))

    def test_next_run_line_is_human_readable(self) -> None:
        self.assertIn("下次运行", self.app._next_text(None))

    def test_menu_labels_follow_the_language_config(self) -> None:
        """保存语言后 update_menu 重画，标签 callable 按当前 cfg 重新求值。"""
        self.app.cfg["language"] = "en"
        labels = [item.text for item in self.items()]
        self.assertIn("Run a round now", labels)
        self.assertIn("Pause schedule", labels)
        self.assertIn("Quit", labels)
        self.assertIn("Status: OK", labels)
        self.assertNotIn("退出", labels, "换语言后不该残留上一语言的标签")
        self.app.scheduler._running = True
        try:
            self.assertEqual(self.app._status_text(None), "Status: running…")
        finally:
            self.app.scheduler._running = False


@unittest.skipUnless(tray is not None, "需要 Windows + pystray + PIL + tkinter")
class EventAndLogTest(unittest.TestCase):
    def test_every_event_kind_becomes_one_line(self) -> None:
        events = [
            {"kind": "round-start", "repos": 2, "dry_run": True},
            {"kind": "repo-start", "repo": "a/b"},
            {"kind": "repo-done", "repo": "a/b", "parts": 1},
            {"kind": "repo-error", "repo": "a/b", "error": "boom"},
            {"kind": "sent", "index": 1, "total": 2, "chars": 10},
            {"kind": "round-done", "status": "ok"},
        ]
        lines = [tray.format_event(event) for event in events]
        self.assertTrue(all(line and "\n" not in line for line in lines), lines)
        self.assertIn("预览", lines[0])
        self.assertIn("boom", lines[3])

    def test_event_lines_follow_the_language(self) -> None:
        events = [
            {"kind": "round-start", "repos": 2, "dry_run": True},
            {"kind": "sent", "index": 1, "total": 2, "chars": 10},
            {"kind": "round-done", "status": "ok"},
        ]
        lines = [tray.format_event(event, lang="en") for event in events]
        self.assertIn("round started (2 repos, preview)", lines[0])
        self.assertIn("sent 1/2 (10 chars)", lines[1])
        self.assertIn("round done: ok", lines[2])

    def test_unknown_event_does_not_crash(self) -> None:
        self.assertIn("mystery", tray.format_event({"kind": "mystery"}))

    def test_secrets_are_redacted(self) -> None:
        text = "QQBOT_APPID=12345\nQQBOT_SECRET=hunter2\nAuthorization: QQBot abc.def"
        cleaned = tray.redact(text)
        self.assertNotIn("12345", cleaned)
        self.assertNotIn("hunter2", cleaned)
        self.assertNotIn("abc.def", cleaned)
        self.assertIn("QQBOT_SECRET=***", cleaned)

    def test_channel_credentials_are_redacted(self) -> None:
        """多通道凭据与 QQ 同级：webhook/SendKey 持有者就能骚扰你，崩溃日志必须打码。"""
        text = (
            "WECOM_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=wh-secret\n"
            "DINGTALK_SECRET=SEC123\nSERVERCHAN_SENDKEY=SCTkey456\nBARK_KEY=bark789\n"
            "feishu_webhook: https://open.feishu.cn/open-apis/bot/v2/hook/hk1\n"
        )
        cleaned = tray.redact(text)
        for secret in ("wh-secret", "SEC123", "SCTkey456", "bark789", "hk1"):
            self.assertNotIn(secret, cleaned, secret)
        self.assertIn("WECOM_WEBHOOK=***", cleaned)

    def test_crash_log_is_written_redacted(self) -> None:
        root = temp_root()
        self.addCleanup(rmtree, root)
        path = tray.write_crash_log(root, "Traceback\nQQBOT_SECRET=topsecret\n")
        assert path is not None
        content = path.read_text(encoding="utf-8")
        self.assertIn("未捕获异常", content)
        self.assertNotIn("topsecret", content)

    def test_crash_log_header_follows_the_language(self) -> None:
        root = temp_root()
        self.addCleanup(rmtree, root)
        path = tray.write_crash_log(root, "boom", lang="en")
        assert path is not None
        self.assertIn("uncaught exception", path.read_text(encoding="utf-8"))

    def test_tail_log_style_event_line_has_a_timestamp(self) -> None:
        self.assertRegex(tray.format_event({"kind": "round-done", "status": "ok"}), r"^\d\d:\d\d:\d\d ")


@unittest.skipUnless(tray is not None, "需要 Windows + pystray + PIL + tkinter")
class SpawnDisciplineTest(unittest.TestCase):
    """GUI 进程 spawn 控制台程序必须带 CREATE_NO_WINDOW，否则每轮闪窗（ADR-0003）。"""

    def test_run_hidden_uses_the_no_window_flag(self) -> None:
        captured: dict = {}

        class FakeProc:
            returncode = 0
            stdout = b""

        def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN202
            captured["cmd"] = cmd
            captured.update(kwargs)
            return FakeProc()

        with mock.patch.object(tray.subprocess, "run", fake_run):
            code, out = tray.run_hidden(["schtasks", "/query"])
        self.assertEqual((code, out), (0, ""))
        if os.name == "nt":
            self.assertEqual(captured["creationflags"], watch._WINDOWS_SPAWN_KW["creationflags"])
        self.assertEqual(captured["cmd"], ["schtasks", "/query"])

    def test_run_hidden_reports_failures_instead_of_raising(self) -> None:
        with mock.patch.object(tray.subprocess, "run", side_effect=OSError("nope")):
            code, out = tray.run_hidden(["schtasks"])
        self.assertEqual(code, 1)
        self.assertIn("nope", out)

    def test_task_detection_uses_schtasks(self) -> None:
        with mock.patch.object(tray, "run_hidden", lambda args, timeout=10.0: (0, "")) as _:
            self.assertTrue(tray.task_present())
        with mock.patch.object(tray, "run_hidden", lambda args, timeout=10.0: (1, "")):
            self.assertFalse(tray.task_present())


@unittest.skipUnless(tray is not None, "需要 Windows + pystray + PIL + tkinter")
class SingleInstanceTest(unittest.TestCase):
    def test_second_instance_is_refused_and_release_frees_it(self) -> None:
        name = f"Local\\refwatch-test-{uuid.uuid4().hex}"
        first = tray.SingleInstance(name)
        second = tray.SingleInstance(name)
        try:
            self.assertTrue(first.acquire(), "第一次应当拿到互斥量")
            self.assertFalse(second.acquire(), "第二次必须被拒绝（这就是「已经在运行」）")
            first.release()
            third = tray.SingleInstance(name)
            self.assertTrue(third.acquire(), "释放后应当能重新拿到")
            third.release()
        finally:
            first.release()
            second.release()

    def test_stale_last_error_does_not_fake_a_second_instance(self) -> None:
        """线程上残留的 ERROR_ALREADY_EXISTS(183) 不能让全新实例误判「已经在运行」。

        这个 bug 的表现是：明明没有第二个托盘，程序却弹「已经在运行」然后退出 ——
        因为 ctypes 读的是线程的 last-error，而成功的 API 调用会把它**原样留着**。
        """
        ctypes.windll.kernel32.SetLastError(183)
        instance = tray.SingleInstance(f"Local\\refwatch-test-{uuid.uuid4().hex}")
        try:
            self.assertTrue(instance.acquire())
        finally:
            instance.release()


@unittest.skipUnless(tray is not None, "需要 Windows + pystray + PIL + tkinter")
class IconAndArgsTest(unittest.TestCase):
    def test_icon_image_comes_from_the_committed_asset(self) -> None:
        image = tray.load_icon_image(HERE.parent / "assets" / "refwatch.ico")
        self.assertEqual(image.size, (256, 256))
        self.assertEqual(image.mode, "RGBA")

    def test_missing_icon_falls_back_instead_of_crashing(self) -> None:
        image = tray.load_icon_image(Path("does-not-exist.ico"))
        self.assertEqual(image.mode, "RGBA")

    def test_split_args_takes_root_and_config_aside(self) -> None:
        root, config, rest = tray.split_args(
            ["--root", r"D:\data", "--config", r"D:\data\c.json", "--one-round", "--dry-run", "--tray"]
        )
        self.assertEqual(root, Path(r"D:\data"))
        self.assertEqual(config, Path(r"D:\data\c.json"))
        self.assertEqual(rest, ["--one-round", "--dry-run"], "--tray 不该被转发给 watch.main")

    def test_split_args_accepts_equals_form(self) -> None:
        root, config, rest = tray.split_args(["--root=D:\\x", "--config=D:\\x\\y.json"])
        self.assertEqual(root, Path(r"D:\x"))
        self.assertEqual(config, Path(r"D:\x\y.json"))
        self.assertEqual(rest, [])

    def test_headless_modes(self) -> None:
        self.assertTrue(tray.is_headless(["--one-round"]))
        self.assertTrue(tray.is_headless(["--dry-run"]))
        self.assertTrue(tray.is_headless(["--repo", "a/b"]))
        self.assertTrue(tray.is_headless(["--repo=a/b"]))
        self.assertFalse(tray.is_headless([]))


if __name__ == "__main__":
    unittest.main()
