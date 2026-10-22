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


if __name__ == "__main__":
    unittest.main()
