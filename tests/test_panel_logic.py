"""设置面板的纯逻辑：表单 ↔ 配置转换、读盘容错、日志尾部、预览渲染。

不构造任何 Tk 窗口 —— 面板的 widget 层在 CI 里既没有稳定显示环境，也没有必要。
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

import channels  # noqa: E402
import panel  # noqa: E402
import settings  # noqa: E402
import watch  # noqa: E402


class FormRoundTripTest(unittest.TestCase):
    def cfg(self, **values: object) -> dict:
        return dict(watch.DEFAULT_CONFIG, repos=["a/b"], openid="oid", **values)

    def test_form_to_config_keeps_types(self) -> None:
        form = {
            "repos": [" https://github.com/A/B.git ", "a/b"],
            "openid": " oid ",
            "notify": True,
            "autostart": False,
            "language": "en",
            "exclude_ref_patterns": ["refs/heads/ci/*", "  "],
            "git_ssl_backend": "openssl",
            "interval_minutes": "15",
            "max_commits_per_ref": "10",
            "max_chunks_per_repo": "1",
            "chunk_limit": "4000",
            "fetch_timeout_sec": "120",
            "fail_alert_threshold": "5",
            "lock_stale_sec": "600",
        }
        values = panel.config_from_form(form)
        self.assertEqual(values["repos"], ["A/B"])
        self.assertEqual(values["openid"], "oid")
        self.assertEqual(values["interval_minutes"], 15)
        self.assertEqual(values["max_commits_per_ref"], 10)
        self.assertIs(values["autostart"], False)
        self.assertEqual(values["language"], "en")
        self.assertEqual(values["exclude_ref_patterns"], ["refs/heads/ci/*", "  "], "空行交给 validate 过滤")
        self.assertEqual(values["git_ssl_backend"], "openssl")

    def test_language_round_trips_and_invalid_falls_back(self) -> None:
        form = panel.form_from_config(self.cfg(language="en"))
        self.assertEqual(form["language"], "en")
        self.assertEqual(panel.config_from_form(form)["language"], "en")
        self.assertEqual(
            panel.config_from_form({"language": "klingon"})["language"], "zh", "非法语言回退 zh"
        )

    def test_channel_round_trips_and_invalid_falls_back(self) -> None:
        form = panel.form_from_config(self.cfg(channel="wecom"))
        self.assertEqual(form["channel"], "wecom")
        self.assertEqual(panel.config_from_form(form)["channel"], "wecom")
        self.assertEqual(
            panel.config_from_form({"channel": "icq"})["channel"], "qq", "非法通道回退 qq"
        )

    def test_config_to_form_then_back_is_stable(self) -> None:
        cfg = self.cfg(interval_minutes=7, notify=False, autostart=True)
        form = panel.form_from_config(cfg)
        self.assertEqual(form["interval_minutes"], "7", "数字在 Entry 里应该是字符串")
        self.assertEqual(panel.config_from_form(form)["repos"], ["a/b"])

    def test_credential_fields_use_the_env_key_names(self) -> None:
        """凭据只有一套命名（env 键小写）—— 显示与保存都按它查表，不可能再错位。"""
        form = panel.form_from_config(self.cfg())
        self.assertIn("qqbot_appid", form)
        self.assertIn("qqbot_secret", form)
        self.assertNotIn("appid", form)
        self.assertNotIn("secret", form)

    def test_garbage_number_survives_until_validate(self) -> None:
        form = panel.form_from_config(self.cfg())
        form["interval_minutes"] = "abc"
        values = panel.config_from_form(form)
        self.assertEqual(values["interval_minutes"], "abc", "转换不抛异常，交给 validate 报错")
        errors = settings.validate(values)
        self.assertTrue(any("interval_minutes" in message for message in errors), errors)

    def test_missing_numbers_fall_back_to_defaults(self) -> None:
        form = panel.form_from_config({"repos": ["a/b"]})
        self.assertEqual(form["interval_minutes"], str(watch.DEFAULT_CONFIG["interval_minutes"]))
        self.assertEqual(form["chunk_limit"], str(watch.DEFAULT_CONFIG["chunk_limit"]))


class LoadFormTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = temp_root()
        self.addCleanup(rmtree, self.root)
        self._here = watch.HERE
        watch.HERE = self.root
        self.addCleanup(setattr, watch, "HERE", self._here)

    def _quiet_env(self) -> dict[str, str]:
        """把真实环境里的凭据清掉：读盘测试只能看见本用例写的那份 .env。"""
        env = {key: "" for code in channels.CHANNELS for key in channels.env_keys_of(code)}
        env["DSH_HOME"] = str(self.root / "legacy-home")
        env["QQBOT_ENV_FILE"] = ""
        return env

    def test_reads_disk_values(self) -> None:
        (self.root / "config.json").write_text(json.dumps({"repos": ["x/y"], "openid": "o"}), encoding="utf-8")
        with mock.patch.dict(os.environ, self._quiet_env()):
            form, notice = panel.load_form(self.root)
        self.assertIsNone(notice)
        self.assertEqual(form["repos"], ["x/y"])
        self.assertEqual(form["openid"], "o")

    def test_broken_config_shows_defaults_and_a_notice(self) -> None:
        (self.root / "config.json").write_text("{ nope", encoding="utf-8")
        form, notice = panel.load_form(self.root)
        self.assertIsNotNone(notice)
        self.assertIn("读取配置失败", notice)
        self.assertEqual(form["repos"], watch.DEFAULT_CONFIG["repos"])

    def test_missing_config_is_not_a_notice(self) -> None:
        form, notice = panel.load_form(self.root)
        self.assertIsNone(notice)
        self.assertEqual(form["repos"], [])

    def test_qq_credentials_are_loaded_into_the_env_key_form(self) -> None:
        """回归：表单曾用 appid/secret、控件却绑 qqbot_* —— 两个框恒空，编辑还被丢弃。"""
        (self.root / "config.json").write_text(json.dumps({"repos": ["a/b"]}), encoding="utf-8")
        (self.root / ".env").write_text("QQBOT_APPID=file-id\nQQBOT_SECRET=file-secret\n", encoding="utf-8")
        with mock.patch.dict(os.environ, self._quiet_env()):
            form, notice = panel.load_form(self.root)
        self.assertIsNone(notice)
        self.assertEqual(form["channel"], "qq")
        self.assertEqual(form["qqbot_appid"], "file-id")
        self.assertEqual(form["qqbot_secret"], "file-secret")

    def test_all_channels_are_preloaded_not_just_the_selected_one(self) -> None:
        """切通道时应当看到该通道已配置的值，而不是空白。"""
        (self.root / "config.json").write_text(json.dumps({"channel": "wecom"}), encoding="utf-8")
        (self.root / ".env").write_text("WECOM_WEBHOOK=https://example.invalid/hook\n", encoding="utf-8")
        with mock.patch.dict(os.environ, self._quiet_env()):
            form, _notice = panel.load_form(self.root)
        self.assertEqual(form["channel"], "wecom")
        self.assertEqual(form["wecom_webhook"], "https://example.invalid/hook")
        self.assertEqual(form["qqbot_appid"], "", "未配置的通道保持空，不是别的通道的值")


class ChannelSecretsTest(unittest.TestCase):
    """表单 → .env 的唯一映射：保存与脏状态比较都走这里。"""

    def test_qq_maps_to_uppercase_env_keys(self) -> None:
        form = {"qqbot_appid": " id ", "qqbot_secret": "s3cret"}
        self.assertEqual(
            panel.channel_secrets(form, "qq"),
            {"QQBOT_APPID": "id", "QQBOT_SECRET": "s3cret"},
        )

    def test_channel_spec_order_and_missing_values(self) -> None:
        self.assertEqual(
            panel.channel_secrets({"wecom_webhook": "https://hook"}, "wecom"),
            {"WECOM_WEBHOOK": "https://hook"},
        )
        self.assertEqual(panel.channel_secrets({}, "bark"), {"BARK_KEY": "", "BARK_SERVER": ""})

    def test_invalid_channel_falls_back_to_qq(self) -> None:
        self.assertEqual(
            panel.channel_secrets({"qqbot_appid": "id"}, "icq"),
            {"QQBOT_APPID": "id", "QQBOT_SECRET": ""},
        )


class LogTailTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = temp_root()
        self.addCleanup(rmtree, self.root)

    def test_missing_log_says_so_instead_of_raising(self) -> None:
        self.assertIn("还没有日志", panel.tail_log(self.root))

    def test_tail_returns_the_newest_file_last_lines(self) -> None:
        logdir = self.root / "logs"
        logdir.mkdir()
        (logdir / "watch-20240101.log").write_text("old\n", encoding="utf-8")
        (logdir / "watch-20240202.log").write_text("\n".join(f"line {i}" for i in range(50)), encoding="utf-8")
        text = panel.tail_log(self.root, lines=5)
        self.assertEqual(text.splitlines(), [f"line {i}" for i in range(45, 50)])

    def test_empty_log_file_is_handled(self) -> None:
        logdir = self.root / "logs"
        logdir.mkdir()
        (logdir / "watch-20240101.log").write_text("", encoding="utf-8")
        self.assertIn("空", panel.tail_log(self.root))


class ReportTextTest(unittest.TestCase):
    def report(self, **kwargs: object) -> watch.RoundReport:
        return watch.RoundReport(**kwargs)  # type: ignore[arg-type]

    def test_ok_round_mentions_counts(self) -> None:
        text = panel.format_report(self.report(repos=2, sent=1))
        self.assertIn("完成", text)
        self.assertIn("2 个仓库", text)
        self.assertIn("已推送 1 条", text)

    def test_lock_skip_is_reported_as_skip(self) -> None:
        self.assertIn("跳过", panel.format_report(self.report(skipped="lock")))

    def test_dry_run_says_it_did_not_advance(self) -> None:
        text = panel.format_report(self.report(status="dry-run", parts=["a"]))
        self.assertIn("预览", text)
        self.assertIn("1 条待发", text)

    def test_preview_text_numbers_the_parts(self) -> None:
        text = panel.preview_text(self.report(parts=["甲", "乙"]))
        self.assertIn("第 1 条", text)
        self.assertIn("第 2 条", text)
        self.assertIn("甲", text)

    def test_preview_of_empty_round_is_explicit(self) -> None:
        self.assertIn("没有要发送", panel.preview_text(self.report()))

    def test_report_and_preview_follow_the_language(self) -> None:
        report = self.report(repos=2, sent=1, parts=["甲"])
        self.assertIn("done", panel.format_report(report, lang="en"))
        self.assertIn("2 repos", panel.format_report(report, lang="en"))
        self.assertIn("sent 1", panel.format_report(report, lang="en"))
        self.assertIn("part 1", panel.preview_text(report, lang="en"))
        self.assertIn("nothing to send", panel.preview_text(self.report(), lang="en"))


if __name__ == "__main__":
    unittest.main()
