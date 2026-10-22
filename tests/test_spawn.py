"""子进程不再闪黑窗口：git() 必须带上 CREATE_NO_WINDOW（Windows）。

这个断言是「形状」级别的守卫；真正证明窗口消失的是 tools/window_probe.py
（窗口不会出现在日志里，单测抓不到）—— 见 docs/adr/0003。
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import watch  # noqa: E402


class SpawnFlagsTest(unittest.TestCase):
    def test_git_child_is_started_without_a_console(self) -> None:
        captured: dict = {}

        class FakeProc:
            returncode = 0
            stdout = b""
            stderr = b""

        def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN202
            captured["cmd"] = cmd
            captured.update(kwargs)
            return FakeProc()

        with mock.patch.object(watch.subprocess, "run", fake_run):
            watch.git(["status"], cfg={"git_ssl_backend": ""})

        if os.name == "nt":
            self.assertEqual(captured.get("creationflags"), subprocess.CREATE_NO_WINDOW)
        else:
            self.assertNotIn("creationflags", captured)

    @unittest.skipUnless(os.name == "nt", "只有 Windows 需要 flag")
    def test_windows_flag_constant_is_present(self) -> None:
        self.assertEqual(watch._WINDOWS_SPAWN_KW, {"creationflags": subprocess.CREATE_NO_WINDOW})

    def test_ssl_backend_flag_precedes_git_arguments(self) -> None:
        captured: dict = {}

        class FakeProc:
            returncode = 0
            stdout = b""
            stderr = b""

        def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN202
            captured["cmd"] = cmd
            return FakeProc()

        with mock.patch.object(watch.subprocess, "run", fake_run):
            watch.git(["status"], cfg={"git_ssl_backend": "openssl"})
        if os.name == "nt":
            self.assertEqual(captured["cmd"][:3], ["git", "-c", "http.sslBackend=openssl"])
            self.assertEqual(captured["cmd"][3], "status")


class TraySpawnStructureTest(unittest.TestCase):
    """托盘里的子进程（schtasks 等）同样属于 ADR-0003：必须经过带 flag 的那一个入口。

    源码级结构守卫，不 import pystray/tkinter —— 零依赖 CI 矩阵里也要跑。
    新增一个绕过 run_hidden() 的 subprocess 调用 = 每轮闪窗那个 bug 回来了。
    """

    def setUp(self) -> None:
        self.source = (HERE.parent / "tray.py").read_text(encoding="utf-8")

    def test_all_subprocess_calls_go_through_the_hidden_helper(self) -> None:
        self.assertEqual(self.source.count("subprocess.run("), 1, "子进程调用必须收敛到 run_hidden()")
        self.assertEqual(self.source.count("subprocess.Popen("), 0)
        self.assertEqual(self.source.count("os.system("), 0)

    def test_the_helper_uses_the_shared_no_window_flag(self) -> None:
        self.assertIn("_SPAWN_KW: dict = dict(watch._WINDOWS_SPAWN_KW)", self.source)
        self.assertIn("**_SPAWN_KW", self.source)


if __name__ == "__main__":
    unittest.main()
