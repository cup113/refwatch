"""配置加载：config.json 优先、watch.json 兼容、openid 来源、git 后端参数。"""

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

import watch  # noqa: E402


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = temp_root()
        self.addCleanup(rmtree, self.dir)
        self.root = self.dir / "data"
        self.root.mkdir()
        self._here = watch.HERE
        watch.HERE = self.dir
        self.addCleanup(setattr, watch, "HERE", self._here)

    def write(self, name: str, **values: object) -> Path:
        path = self.dir / name
        path.write_text(json.dumps(values), encoding="utf-8")
        return path

    def test_config_json_wins_over_watch_json(self) -> None:
        self.write("config.json", repos=["new/one"])
        self.write("watch.json", repos=["old/one"])
        cfg = watch.load_config(self.root, None)
        self.assertEqual(cfg["repos"], ["new/one"])
        self.assertEqual(cfg["_config_path"].name, "config.json")

    def test_legacy_watch_json_still_loads(self) -> None:
        self.write("watch.json", repos=["old/one"])
        cfg = watch.load_config(self.root, None)
        self.assertEqual(cfg["repos"], ["old/one"])
        self.assertEqual(cfg["_config_path"].name, "watch.json")

    def test_explicit_config_beats_both(self) -> None:
        self.write("config.json", repos=["disk/one"])
        explicit = self.write("elsewhere.json", repos=["explicit/one"])
        cfg = watch.load_config(self.root, explicit)
        self.assertEqual(cfg["repos"], ["explicit/one"])

    def test_missing_config_falls_back_to_defaults(self) -> None:
        cfg = watch.load_config(self.root, None)
        self.assertEqual(cfg["repos"], [])
        self.assertEqual(cfg["chunk_limit"], watch.DEFAULT_CONFIG["chunk_limit"])

    def test_openid_from_env_when_config_blank(self) -> None:
        self.write("config.json", openid="")
        with mock.patch.dict(os.environ, {"QQBOT_OPENID": "env-openid"}):
            cfg = watch.load_config(self.root, None)
        self.assertEqual(cfg["openid"], "env-openid")

    def test_git_backend_injected_only_on_windows(self) -> None:
        want = ["-c", "http.sslBackend=openssl"]
        cfg = {"git_ssl_backend": "openssl"}
        self.assertEqual(watch._git_opts(cfg), want if os.name == "nt" else [])
        self.assertEqual(watch._git_opts({"git_ssl_backend": ""}), [])

    def test_language_defaults_to_zh(self) -> None:
        cfg = watch.load_config(self.root, None)
        self.assertEqual(cfg["language"], "zh")

    def test_invalid_language_falls_back_with_a_warning(self) -> None:
        self.write("config.json", repos=["a/b"], language="fr")
        cfg = watch.load_config(self.root, None)
        self.assertEqual(cfg["language"], "zh", "非法值回退 zh，不炸")
        logs = list((self.root / "logs").glob("watch-*.log"))
        content = "\n".join(path.read_text(encoding="utf-8") for path in logs)
        self.assertIn("[WARN]", content)
        self.assertIn("fr", content)

    def test_valid_language_passes_through(self) -> None:
        self.write("config.json", repos=["a/b"], language="en")
        cfg = watch.load_config(self.root, None)
        self.assertEqual(cfg["language"], "en")

    def test_channel_defaults_to_qq(self) -> None:
        cfg = watch.load_config(self.root, None)
        self.assertEqual(cfg["channel"], "qq")

    def test_invalid_channel_falls_back_with_a_warning(self) -> None:
        self.write("config.json", repos=["a/b"], channel="msn")
        cfg = watch.load_config(self.root, None)
        self.assertEqual(cfg["channel"], "qq", "非法值回退 qq，不炸")
        logs = list((self.root / "logs").glob("watch-*.log"))
        content = "\n".join(path.read_text(encoding="utf-8") for path in logs)
        self.assertIn("[WARN]", content)
        self.assertIn("channel", content)
        self.assertIn("msn", content)

    def test_valid_channel_passes_through(self) -> None:
        self.write("config.json", repos=["a/b"], channel="feishu")
        cfg = watch.load_config(self.root, None)
        self.assertEqual(cfg["channel"], "feishu")


if __name__ == "__main__":
    unittest.main()
