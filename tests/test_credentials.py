"""凭据来源顺序：环境变量 > <root>/.env > 旧 DSH 位置。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from helpers import rmtree, temp_root  # noqa: E402

import qq_notify  # noqa: E402


def write_env(path: Path, app_id: str, secret: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# test\nQQBOT_APPID={app_id}\nQQBOT_SECRET={secret}\n", encoding="utf-8")
    return path


class CredentialsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = temp_root()
        self.dsh = temp_root()
        self.addCleanup(rmtree, self.root)
        self.addCleanup(rmtree, self.dsh)

    def test_env_vars_win(self) -> None:
        write_env(self.root / ".env", "file-id", "file-secret")
        with mock.patch.dict(
            os.environ,
            {"QQBOT_APPID": "env-id", "QQBOT_SECRET": "env-secret", "DSH_HOME": str(self.dsh)},
        ):
            self.assertEqual(qq_notify.load_credentials(self.root), ("env-id", "env-secret"))

    def test_root_env_file_beats_legacy_home(self) -> None:
        write_env(self.root / ".env", "root-id", "root-secret")
        write_env(self.dsh / ".env", "legacy-id", "legacy-secret")
        with mock.patch.dict(
            os.environ,
            {"QQBOT_APPID": "", "QQBOT_SECRET": "", "DSH_HOME": str(self.dsh)},
        ):
            self.assertEqual(qq_notify.load_credentials(self.root), ("root-id", "root-secret"))

    def test_legacy_dsh_home_still_works(self) -> None:
        write_env(self.dsh / ".env", "legacy-id", "legacy-secret")
        with mock.patch.dict(
            os.environ,
            {"QQBOT_APPID": "", "QQBOT_SECRET": "", "DSH_HOME": str(self.dsh)},
        ):
            self.assertEqual(qq_notify.load_credentials(self.root), ("legacy-id", "legacy-secret"))

    def test_missing_credentials_raises(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"QQBOT_APPID": "", "QQBOT_SECRET": "", "DSH_HOME": str(self.dsh)},
        ):
            with self.assertRaises(SystemExit):
                qq_notify.load_credentials(self.root)


class EnvValuesTest(unittest.TestCase):
    """read_env_values：多通道凭据的批量读取器，优先级与 QQ 凭据一致。"""

    def setUp(self) -> None:
        self.root = temp_root()
        self.dsh = temp_root()
        self.addCleanup(rmtree, self.root)
        self.addCleanup(rmtree, self.dsh)

    def test_env_wins_then_file_then_empty(self) -> None:
        (self.root / ".env").write_text(
            "WECOM_WEBHOOK=file-url\nBARK_KEY=file-key\n", encoding="utf-8"
        )
        quiet = {
            "WECOM_WEBHOOK": "env-url",
            "BARK_KEY": "",
            "FEISHU_WEBHOOK": "",
            "DSH_HOME": str(self.dsh),
        }
        with mock.patch.dict(os.environ, quiet):
            values = qq_notify.read_env_values(
                ["WECOM_WEBHOOK", "BARK_KEY", "FEISHU_WEBHOOK"], self.root
            )
        self.assertEqual(values["WECOM_WEBHOOK"], "env-url", "环境变量优先")
        self.assertEqual(values["BARK_KEY"], "file-key", "缺的键回 .env 补")
        self.assertEqual(values["FEISHU_WEBHOOK"], "", "都没有就是空串，不抛")

    def test_keys_are_returned_as_given(self) -> None:
        with mock.patch.dict(os.environ, {"DSH_HOME": str(self.dsh)}):
            values = qq_notify.read_env_values(["BARK_SERVER"], self.root)
        self.assertEqual(list(values), ["BARK_SERVER"])


class OpenIdTest(unittest.TestCase):
    def test_no_default_openid_in_module(self) -> None:
        """openid 属于配置，代码里不该留任何默认值。"""
        self.assertFalse(hasattr(qq_notify, "DEFAULT_OPENID"))
        self.assertNotIn("DBEB", Path(qq_notify.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
