"""设置面板的落盘层：合并写、校验、.env 手术、自启对账。

一切都不碰真实的注册表 / 用户目录：winreg 用假模块顶替，自启逻辑因此可以在任何平台测。
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from helpers import rmtree, temp_root  # noqa: E402

import settings  # noqa: E402
import watch  # noqa: E402


class FakeWinreg:
    """够用的 winreg 替身：一个内存字典当 HKCU 的 Run 键。"""

    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE = 1
    REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    class _Key:
        def __init__(self, fake: FakeWinreg) -> None:
            self._fake = fake

        def __enter__(self) -> FakeWinreg:
            return self._fake

        def __exit__(self, *_exc: object) -> None:
            return None

    def OpenKey(self, _root: object, _path: str) -> _Key:  # noqa: N802 - 模拟 winreg 的命名
        return FakeWinreg._Key(self)

    def CreateKeyEx(self, _root: object, _path: str, _reserved: int, _access: int) -> _Key:  # noqa: N802
        return FakeWinreg._Key(self)

    def QueryValueEx(self, _key: object, name: str) -> tuple[str, int]:  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, _key: object, name: str, _reserved: int, _kind: int, value: str) -> None:  # noqa: N802
        self.values[name] = value

    def DeleteValue(self, _key: object, name: str) -> None:  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


class SettingsBase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = temp_root()
        self.addCleanup(rmtree, self.root)
        # watch.default_config_path() 看的是 watch.HERE：指到临时目录，别捡到仓库里的文件。
        self._here = watch.HERE
        watch.HERE = self.root
        self.addCleanup(setattr, watch, "HERE", self._here)

    def quiet_env(self) -> dict[str, str]:
        return {
            "QQBOT_APPID": "",
            "QQBOT_SECRET": "",
            "QQBOT_ENV_FILE": str(self.root / "nope.env"),
            "DSH_HOME": str(self.root / "no-dsh"),
        }


class ConfigPathTest(SettingsBase):
    def test_root_config_json_wins(self) -> None:
        (self.root / "config.json").write_text("{}", encoding="utf-8")
        (self.root / "watch.json").write_text("{}", encoding="utf-8")
        self.assertEqual(settings.resolve_config_path(self.root).name, "config.json")

    def test_legacy_watch_json_is_found(self) -> None:
        (self.root / "watch.json").write_text("{}", encoding="utf-8")
        self.assertEqual(settings.resolve_config_path(self.root).name, "watch.json")

    def test_explicit_path_beats_everything(self) -> None:
        (self.root / "config.json").write_text("{}", encoding="utf-8")
        explicit = self.root / "other.json"
        self.assertEqual(settings.resolve_config_path(self.root, explicit), explicit)

    def test_missing_everything_defaults_to_root_config_json(self) -> None:
        self.assertEqual(settings.resolve_config_path(self.root), self.root / "config.json")

    def test_load_merges_defaults(self) -> None:
        (self.root / "config.json").write_text(json.dumps({"repos": ["a/b"]}), encoding="utf-8")
        cfg = settings.load(self.root)
        self.assertEqual(cfg["repos"], ["a/b"])
        self.assertEqual(cfg["interval_minutes"], watch.DEFAULT_CONFIG["interval_minutes"])
        self.assertEqual(cfg["_config_path"], self.root / "config.json")


class SaveConfigTest(SettingsBase):
    def test_unknown_keys_survive_a_merge_write(self) -> None:
        path = self.root / "config.json"
        path.write_text(json.dumps({"repos": ["old/one"], "my_custom_key": 42}), encoding="utf-8")
        settings.save_config(path, {"repos": ["new/two"]})
        written = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(written["repos"], ["new/two"])
        self.assertEqual(written["my_custom_key"], 42, "界面没展示的键不能丢")

    def test_runtime_private_keys_are_not_written(self) -> None:
        path = self.root / "config.json"
        settings.save_config(path, {"repos": [], "_config_path": "x", "_root": "y"})
        written = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("_config_path", written)
        self.assertNotIn("_root", written)

    def test_write_is_atomic_and_leaves_no_tmp(self) -> None:
        path = self.root / "config.json"
        settings.save_config(path, {"repos": ["a/b"]})
        self.assertEqual([p.name for p in self.root.iterdir()], ["config.json"])

    def test_broken_json_is_backed_up_before_overwrite(self) -> None:
        path = self.root / "config.json"
        path.write_text("{ this is not json", encoding="utf-8")
        settings.save_config(path, {"repos": ["a/b"]})
        backups = list(self.root.glob("config.json.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertIn("this is not json", backups[0].read_text(encoding="utf-8"))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["repos"], ["a/b"])

    def test_save_creates_missing_parent(self) -> None:
        path = self.root / "nested" / "deep" / "config.json"
        settings.save_config(path, {"repos": ["a/b"]})
        self.assertTrue(path.is_file())


class ValidateTest(SettingsBase):
    def test_repo_inputs_are_normalized(self) -> None:
        values = {
            "repos": [
                "  owner/name  ",
                "https://github.com/owner/name",
                "https://github.com/owner/name.git",
                "github.com/other/repo/",
                "git@github.com:third/repo.git",
                "owner/name",  # 与第一条重复（大小写/空白不同）
                "",
            ]
        }
        errors = settings.validate(values)
        self.assertEqual(errors, [])
        self.assertEqual(values["repos"], ["owner/name", "other/repo", "third/repo"])

    def test_bad_slug_is_reported(self) -> None:
        values = {"repos": ["nope", "a/b/c"]}
        errors = settings.validate(values)
        self.assertEqual(len(errors), 2, errors)

    def test_int_bounds(self) -> None:
        errors = settings.validate({"repos": [], "interval_minutes": 0, "chunk_limit": "abc"})
        self.assertEqual(len(errors), 2, errors)

    def test_bool_and_backend(self) -> None:
        errors = settings.validate({"repos": [], "notify": "yes", "git_ssl_backend": "gnutls"})
        self.assertEqual(len(errors), 2, errors)

    def test_secret_in_config_json_is_refused(self) -> None:
        errors = settings.validate({"repos": [], "QQBOT_SECRET": "s3cret"})
        self.assertTrue(any("QQBOT_SECRET" in message for message in errors), errors)

    def test_patterns_accept_a_multiline_string(self) -> None:
        values = {"repos": [], "exclude_ref_patterns": "refs/heads/ci/*\n\n refs/tags/nightly-* "}
        self.assertEqual(settings.validate(values), [])
        self.assertEqual(values["exclude_ref_patterns"], ["refs/heads/ci/*", "refs/tags/nightly-*"])

    def test_openid_is_trimmed(self) -> None:
        values = {"repos": [], "openid": "  ABC  "}
        settings.validate(values)
        self.assertEqual(values["openid"], "ABC")

    def test_language_must_be_zh_or_en(self) -> None:
        values = {"repos": [], "language": "fr"}
        errors = settings.validate(values)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("zh / en", errors[0])
        accepted = {"repos": [], "language": "en"}
        self.assertEqual(settings.validate(accepted), [])
        self.assertEqual(accepted["language"], "en")

    def test_error_messages_follow_the_lang_argument(self) -> None:
        errors = settings.validate({"interval_minutes": "abc"}, lang="en")
        self.assertTrue(any("must be an integer" in message for message in errors), errors)
        errors = settings.validate({"repos": [], "interval_minutes": "abc"})
        self.assertTrue(any("必须是整数" in message for message in errors), errors)


class SecretsTest(SettingsBase):
    def test_env_file_is_created_with_both_keys(self) -> None:
        settings.save_secrets(self.root, "12345", "sesame")
        text = (self.root / ".env").read_text(encoding="utf-8")
        self.assertEqual(text, "QQBOT_APPID=12345\nQQBOT_SECRET=sesame\n")

    def test_other_lines_comments_and_crlf_are_kept(self) -> None:
        path = self.root / ".env"
        path.write_bytes(b"# header\r\nFOO=bar\r\nQQBOT_APPID=old\r\n\r\nQQBOT_SECRET=old\r\nTAIL=x\r\n")
        settings.save_secrets(self.root, "new-id", "new-secret")
        data = path.read_bytes()
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""), "原来是 CRLF，写回还得是 CRLF")
        text = data.decode("utf-8")
        self.assertIn("# header", text)
        self.assertIn("FOO=bar", text)
        self.assertIn("TAIL=x", text)
        self.assertIn("QQBOT_APPID=new-id", text)
        self.assertIn("QQBOT_SECRET=new-secret", text)
        self.assertNotIn("old", text)

    def test_no_bom_is_introduced(self) -> None:
        settings.save_secrets(self.root, "id", "secret")
        self.assertFalse((self.root / ".env").read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_duplicate_keys_collapse_to_one(self) -> None:
        path = self.root / ".env"
        path.write_text("QQBOT_APPID=one\nQQBOT_APPID=two\n", encoding="utf-8")
        settings.save_secrets(self.root, "three", "s")
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count("QQBOT_APPID="), 1)
        self.assertIn("QQBOT_APPID=three", text)

    def test_saved_secrets_are_read_back(self) -> None:
        settings.save_secrets(self.root, "app-id", "app-secret")
        with mock.patch.dict(os.environ, self.quiet_env()):
            found = settings.read_secrets(self.root)
        self.assertEqual(found["appid"], "app-id")
        self.assertEqual(found["secret"], "app-secret")
        self.assertTrue(found["source"].endswith(".env"))

    def test_secrets_never_land_in_config_json(self) -> None:
        path = self.root / "config.json"
        settings.save_config(path, {"repos": ["a/b"], "openid": "oid"})
        settings.save_secrets(self.root, "app-id", "app-secret")
        self.assertNotIn("app-secret", path.read_text(encoding="utf-8"))


class AutostartTest(SettingsBase):
    def setUp(self) -> None:
        super().setUp()
        self.fake = FakeWinreg()
        patcher = mock.patch.dict(sys.modules, {"winreg": self.fake})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_absent_value_reads_as_none(self) -> None:
        self.assertIsNone(settings.registered_autostart())

    def test_enable_writes_the_value(self) -> None:
        self.assertTrue(settings.set_autostart(True, '"C:\\app\\refwatch.exe" --tray'))
        self.assertEqual(settings.registered_autostart(), '"C:\\app\\refwatch.exe" --tray')
        self.assertEqual(self.fake.values["refwatch"], '"C:\\app\\refwatch.exe" --tray')

    def test_enable_is_idempotent(self) -> None:
        command = '"C:\\app\\refwatch.exe" --tray'
        settings.set_autostart(True, command)
        self.assertFalse(settings.set_autostart(True, command), "值没变就不该再写注册表")

    def test_disable_removes_the_value(self) -> None:
        settings.set_autostart(True, "cmd")
        self.assertTrue(settings.set_autostart(False, "cmd"))
        self.assertIsNone(settings.registered_autostart())

    def test_disable_when_absent_is_a_noop(self) -> None:
        self.assertFalse(settings.set_autostart(False, "cmd"))

    def test_sync_follows_the_config_value(self) -> None:
        settings.sync_autostart(True, "cmd")
        self.assertEqual(settings.registered_autostart(), "cmd")
        settings.sync_autostart(False, "cmd")
        self.assertIsNone(settings.registered_autostart())


class SelfCommandTest(SettingsBase):
    def test_source_install_uses_pythonw_and_tray_py(self) -> None:
        command = settings.self_command()
        self.assertIn("tray.py", command)
        self.assertTrue(command.startswith('"'))

    def test_default_root_matches_install_ps1(self) -> None:
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\x\AppData\Local"}):
            if os.name == "nt":
                self.assertEqual(settings.default_root(), Path(r"C:\Users\x\AppData\Local") / "refwatch")


if __name__ == "__main__":
    unittest.main()
