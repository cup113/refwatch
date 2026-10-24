"""channels.py：注册表一致性、签名公式、各适配器的 payload 与失败面。

不碰网络：所有出口都 mock 在 qq_notify.post_json / get_access_token / send_text 上。
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
import os
import sys
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from helpers import rmtree, temp_root  # noqa: E402

import channels  # noqa: E402
import i18n  # noqa: E402
import qq_notify  # noqa: E402


def quiet_env(root: Path, **overrides: str) -> dict[str, str]:
    """所有通道的 .env 键清空、候选文件指到不存在的地方，再叠加 overrides。"""
    values = {key: "" for code in channels.CHANNELS for key in channels.env_keys_of(code)}
    values["DSH_HOME"] = str(root / "no-dsh")
    values["QQBOT_ENV_FILE"] = str(root / "nope.env")
    values.update(overrides)
    return values


class ChannelBase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = temp_root()
        self.addCleanup(rmtree, self.root)

    def posts(self, reply: dict, record: list | None = None) -> mock.MagicMock:
        """替身 post_json：记录 (url, payload, form, kwargs) 并返回 reply。"""
        def fake_post(url, payload=None, headers=None, *, form=None, **kwargs):  # noqa: ARG001
            if record is not None:
                record.append({"url": url, "payload": payload, "form": form, "kwargs": kwargs})
            return reply

        patcher = mock.patch.object(qq_notify, "post_json", fake_post)
        patcher.start()
        self.addCleanup(patcher.stop)
        return patcher


class RegistryTest(ChannelBase):
    def test_every_channel_has_credentials_and_a_limit(self) -> None:
        self.assertEqual(set(channels.CREDENTIALS), set(channels.CHANNELS))
        self.assertEqual(set(channels.TEXT_LIMITS), set(channels.CHANNELS))

    def test_every_label_key_exists_in_both_language_tables(self) -> None:
        """面板字段标签由注册表驱动：缺键会在运行时 AssertionError，这里提前拦住。"""
        for specs in channels.CREDENTIALS.values():
            for spec in specs:
                for lang in i18n.LANGUAGES:
                    self.assertIn(str(spec["label"]), i18n.STRINGS[lang])

    def test_channel_display_keys_exist(self) -> None:
        for code in channels.CHANNELS:
            for lang in i18n.LANGUAGES:
                self.assertIn(f"channel.{code}", i18n.STRINGS[lang])

    def test_normalize_channel_falls_back_to_qq(self) -> None:
        self.assertEqual(channels.normalize_channel("wecom"), "wecom")
        self.assertEqual(channels.normalize_channel(" WeCom "), "wecom")
        self.assertEqual(channels.normalize_channel(""), "qq")
        self.assertEqual(channels.normalize_channel(None), "qq")
        self.assertEqual(channels.normalize_channel("telegram"), "qq")


class SplitByBytesTest(ChannelBase):
    def test_cjk_text_is_split_by_bytes_not_chars(self) -> None:
        """守护：wecom 上限是 2048 **字节**。按字符切会看 1000 ≤ 2000 就整条放行，
        3000 字节的消息会被官方拒收 —— 字节感知的切分必须把它切开。"""
        text = "汉" * 1000  # 3000 字节
        parts = channels.split_by_bytes(text, 2000)
        self.assertGreater(len(parts), 1, "按字符切（len(text) ≤ limit 直通）过不了这条断言")
        for part in parts:
            self.assertLessEqual(len(part.encode("utf-8")), 2000)
        self.assertEqual("".join(parts), text, "切分不能丢字")

    def test_short_text_is_returned_as_is(self) -> None:
        self.assertEqual(channels.split_by_bytes("abc", 2000), ["abc"])

    def test_mixed_width_never_loses_characters(self) -> None:
        text = "a汉b\n" * 333
        parts = channels.split_by_bytes(text, 100)
        self.assertEqual("".join(parts), text)
        for part in parts:
            self.assertLessEqual(len(part.encode("utf-8")), 100)


class WecomTest(ChannelBase):
    def test_missing_webhook_is_config_error(self) -> None:
        with mock.patch.dict(os.environ, quiet_env(self.root)):
            with self.assertRaises(channels.ChannelConfigError):
                channels.prepare({"channel": "wecom"}, self.root)

    def test_send_posts_text_and_hides_query_in_errors(self) -> None:
        record: list[dict] = []
        self.posts({"errcode": 0}, record)
        env = quiet_env(self.root, WECOM_WEBHOOK="https://qyapi.example/send?key=k1")
        with mock.patch.dict(os.environ, env):
            send = channels.prepare({"channel": "wecom"}, self.root)
            send("hello")
        self.assertEqual(record[0]["url"], "https://qyapi.example/send?key=k1")
        self.assertEqual(record[0]["payload"], {"msgtype": "text", "text": {"content": "hello"}})
        self.assertTrue(record[0]["kwargs"].get("hide_query_in_errors"))

    def test_long_cjk_digest_is_resplit_by_bytes(self) -> None:
        record: list[dict] = []
        self.posts({"errcode": 0}, record)
        env = quiet_env(self.root, WECOM_WEBHOOK="https://qyapi.example/send?key=k1")
        with mock.patch.dict(os.environ, env):
            send = channels.prepare({"channel": "wecom"}, self.root)
            send("汉" * 1000)
        self.assertEqual(len(record), 2, "3000 字节要切成 2000 + 1000 两条")

    def test_errcode_failure_raises_runtime_error(self) -> None:
        self.posts({"errcode": 40001, "errmsg": "invalid webhook"})
        env = quiet_env(self.root, WECOM_WEBHOOK="https://qyapi.example/send?key=k1")
        with mock.patch.dict(os.environ, env):
            send = channels.prepare({"channel": "wecom"}, self.root)
            with self.assertRaises(RuntimeError):
                send("hello")


class DingTalkTest(ChannelBase):
    def test_signature_matches_the_official_recipe(self) -> None:
        """官方公式：`{ts}\\n{secret}` 同时当 HMAC key 与消息，base64 后 urlencode。"""
        url = channels._dingtalk_signed_url(
            "https://oapi.dingtalk.com/robot/send?access_token=tok",
            "SECxyz",
            now_ms=1700000000000,
        )
        string_to_sign = "1700000000000\nSECxyz"
        digest = hmac_mod.new(
            string_to_sign.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
        ).digest()
        expected = urllib.parse.quote_plus(base64.b64encode(digest).decode("utf-8"))
        self.assertIn("timestamp=1700000000000", url)
        self.assertIn(f"sign={expected}", url)

    def test_no_secret_leaves_webhook_unchanged(self) -> None:
        raw = "https://oapi.dingtalk.com/robot/send?access_token=tok"
        self.assertEqual(channels._dingtalk_signed_url(raw, ""), raw)

    def test_send_uses_signed_url_and_text_payload(self) -> None:
        record: list[dict] = []
        self.posts({"errcode": 0}, record)
        env = quiet_env(
            self.root,
            DINGTALK_WEBHOOK="https://oapi.dingtalk.com/robot/send?access_token=tok",
            DINGTALK_SECRET="SECxyz",
        )
        with mock.patch.dict(os.environ, env):
            send = channels.prepare({"channel": "dingtalk"}, self.root)
            send("hello")
        url = record[0]["url"]
        self.assertIn("timestamp=", url)
        self.assertIn("sign=", url)
        self.assertEqual(record[0]["payload"], {"msgtype": "text", "text": {"content": "hello"}})


class FeishuTest(ChannelBase):
    def test_signature_matches_the_official_recipe(self) -> None:
        """官方公式：`{ts}\\n{secret}` 整串当 HMAC key，对空消息签名后 base64。"""
        key = "1700000000\nSECxyz".encode("utf-8")
        expected = base64.b64encode(hmac_mod.new(key, b"", hashlib.sha256).digest()).decode("utf-8")
        self.assertEqual(channels._feishu_sign("SECxyz", 1700000000), expected)

    def test_send_without_secret_has_no_sign_fields(self) -> None:
        record: list[dict] = []
        self.posts({"code": 0, "msg": "success"}, record)
        env = quiet_env(self.root, FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/h1")
        with mock.patch.dict(os.environ, env):
            send = channels.prepare({"channel": "feishu"}, self.root)
            send("hello")
        self.assertEqual(record[0]["payload"]["msg_type"], "text")
        self.assertEqual(record[0]["payload"]["content"], {"text": "hello"})
        self.assertNotIn("sign", record[0]["payload"])

    def test_send_with_secret_carries_timestamp_and_sign(self) -> None:
        record: list[dict] = []
        self.posts({"code": 0}, record)
        env = quiet_env(
            self.root,
            FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/h1",
            FEISHU_SECRET="SECxyz",
        )
        with mock.patch.dict(os.environ, env):
            send = channels.prepare({"channel": "feishu"}, self.root)
            send("hello")
        self.assertIn("timestamp", record[0]["payload"])
        self.assertIn("sign", record[0]["payload"])

    def test_both_response_generations_are_accepted(self) -> None:
        for reply in ({"code": 0}, {"StatusCode": 0}):
            self.posts(reply)
            env = quiet_env(self.root, FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/h1")
            with mock.patch.dict(os.environ, env):
                send = channels.prepare({"channel": "feishu"}, self.root)
                send("hello")  # 不抛即通过

    def test_sign_failure_error_raises(self) -> None:
        self.posts({"code": 19021, "msg": "sign match fail"})
        env = quiet_env(
            self.root,
            FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/h1",
            FEISHU_SECRET="SECxyz",
        )
        with mock.patch.dict(os.environ, env):
            send = channels.prepare({"channel": "feishu"}, self.root)
            with self.assertRaises(RuntimeError):
                send("hello")


class ServerChanTest(ChannelBase):
    def test_send_posts_title_and_full_desp(self) -> None:
        record: list[dict] = []
        self.posts({"code": 0}, record)
        env = quiet_env(self.root, SERVERCHAN_SENDKEY="SCTkey123")
        with mock.patch.dict(os.environ, env):
            send = channels.prepare({"channel": "serverchan"}, self.root)
            send("[acme/widget] 2 个 ref 有更新\n正文一行")
        self.assertEqual(record[0]["url"], "https://sctapi.ftqq.com/SCTkey123.send")
        self.assertEqual(record[0]["form"]["title"], "[acme/widget] 2 个 ref 有更新")
        self.assertIn("正文一行", record[0]["form"]["desp"])

    def test_long_first_line_title_is_truncated(self) -> None:
        record: list[dict] = []
        self.posts({"code": 0}, record)
        env = quiet_env(self.root, SERVERCHAN_SENDKEY="SCTkey123")
        with mock.patch.dict(os.environ, env):
            send = channels.prepare({"channel": "serverchan"}, self.root)
            send("标" * 100)
        self.assertLessEqual(len(record[0]["form"]["title"]), 32)

    def test_nonzero_code_raises(self) -> None:
        self.posts({"code": 40001, "message": "bad sendkey"})
        env = quiet_env(self.root, SERVERCHAN_SENDKEY="SCTkey123")
        with mock.patch.dict(os.environ, env):
            send = channels.prepare({"channel": "serverchan"}, self.root)
            with self.assertRaises(RuntimeError):
                send("hello")


class BarkTest(ChannelBase):
    def test_default_server_is_used_and_payload_split(self) -> None:
        record: list[dict] = []
        self.posts({"code": 200}, record)
        env = quiet_env(self.root, BARK_KEY="bark789")
        with mock.patch.dict(os.environ, env):
            send = channels.prepare({"channel": "bark"}, self.root)
            send("标题行\n正文")
        self.assertEqual(record[0]["url"], "https://api.day.app/push")
        self.assertEqual(record[0]["payload"]["title"], "标题行")
        self.assertEqual(record[0]["payload"]["body"], "标题行\n正文")
        self.assertEqual(record[0]["payload"]["device_key"], "bark789")

    def test_custom_server_overrides_the_default(self) -> None:
        record: list[dict] = []
        self.posts({"code": 200}, record)
        env = quiet_env(self.root, BARK_KEY="bark789", BARK_SERVER="https://bark.self/")
        with mock.patch.dict(os.environ, env):
            send = channels.prepare({"channel": "bark"}, self.root)
            send("hi")
        self.assertEqual(record[0]["url"], "https://bark.self/push", "尾斜杠要被收掉")

    def test_non_200_code_raises(self) -> None:
        self.posts({"code": 400, "message": "bad key"})
        env = quiet_env(self.root, BARK_KEY="bark789")
        with mock.patch.dict(os.environ, env):
            send = channels.prepare({"channel": "bark"}, self.root)
            with self.assertRaises(RuntimeError):
                send("hello")


class QqPrepareTest(ChannelBase):
    def test_missing_openid_is_config_error(self) -> None:
        with mock.patch.dict(os.environ, quiet_env(self.root)):
            with self.assertRaises(channels.ChannelConfigError):
                channels.prepare({"channel": "qq", "openid": ""}, self.root)

    def test_missing_credentials_is_config_error(self) -> None:
        env = quiet_env(self.root)
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(channels.ChannelConfigError):
                channels.prepare({"channel": "qq", "openid": "oid"}, self.root)

    def test_token_failure_is_setup_error(self) -> None:
        env = quiet_env(self.root, QQBOT_APPID="id", QQBOT_SECRET="secret")
        with mock.patch.dict(os.environ, env), mock.patch.object(
            qq_notify, "get_access_token", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(channels.ChannelSetupError):
                channels.prepare({"channel": "qq", "openid": "oid"}, self.root)

    def test_sender_uses_openid_and_token(self) -> None:
        sent: list[tuple[str, str, str]] = []
        env = quiet_env(self.root, QQBOT_APPID="id", QQBOT_SECRET="secret")
        with mock.patch.dict(os.environ, env), mock.patch.object(
            qq_notify, "get_access_token", lambda app_id, secret: "tok"
        ), mock.patch.object(qq_notify, "send_text", lambda openid, text, token: sent.append((openid, text, token))):
            send = channels.prepare({"channel": "qq", "openid": "oid"}, self.root)
            send("hi")
        self.assertEqual(sent, [("oid", "hi", "tok")])


class CliTest(ChannelBase):
    def test_unknown_channel_is_exit_2(self) -> None:
        self.assertEqual(channels.main(["--channel", "icq", "hi"]), 2)

    def test_empty_text_is_exit_2(self) -> None:
        self.assertEqual(channels.main(["--channel", "wecom"]), 2)

    def test_missing_config_is_exit_2(self) -> None:
        with mock.patch.dict(os.environ, quiet_env(self.root)):
            self.assertEqual(
                channels.main(["--channel", "wecom", "--root", str(self.root), "hi"]), 2
            )

    def test_happy_path_sends_through_the_channel(self) -> None:
        env = quiet_env(self.root, WECOM_WEBHOOK="https://qyapi.example/send?key=k1")
        with mock.patch.dict(os.environ, env), mock.patch.object(
            qq_notify, "post_json", lambda *a, **k: {"errcode": 0}
        ):
            self.assertEqual(
                channels.main(["--channel", "wecom", "--root", str(self.root), "hi"]), 0
            )


if __name__ == "__main__":
    unittest.main()
