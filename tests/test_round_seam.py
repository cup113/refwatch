"""一轮的接缝：run_round 是 CLI 与托盘应用共用的唯一实现。

两种测法：
  * 纯逻辑（mock check_repo）：退出码、发送计数、锁、dry-run 不落盘、事件顺序；
  * 真仓（helpers.Remote）：dry-run 之后再跑真的一轮，变化仍然要报 ——
    「dry-run 不推进 ref 基线」是行为，不是注释里的承诺。
"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from helpers import SLUG, Remote, git, rmtree, temp_root  # noqa: E402

import watch  # noqa: E402


def make_cfg(root: Path, **values: object) -> dict:
    """和 load_config 出来的形状一致：默认值 + 覆盖 + _root/_config_path。"""
    cfg = dict(watch.DEFAULT_CONFIG, repos=[SLUG], openid="oid", notify=True, git_ssl_backend="")
    cfg.update(values)
    cfg["_root"] = root
    cfg["_config_path"] = root / "config.json"
    return cfg


class RoundSeamTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = temp_root()
        self.addCleanup(rmtree, self.root)
        self.cfg = make_cfg(self.root)
        # 发送间隔 1.2s 是线上行为；测试里不要真的睡。
        sleeper = mock.patch.object(watch.time, "sleep", lambda _seconds: None)
        sleeper.start()
        self.addCleanup(sleeper.stop)

    # -- 退出码 --------------------------------------------------------
    def test_no_repos_is_exit_code_2(self) -> None:
        report = watch.run_round(self.root, make_cfg(self.root, repos=[]))
        self.assertEqual(report.status, "no-repos")
        self.assertEqual(report.exit_code, 2)

    def test_missing_openid_is_exit_code_1(self) -> None:
        with mock.patch.object(watch, "check_repo", lambda *a, **k: ["msg"]):
            report = watch.run_round(self.root, make_cfg(self.root, openid=""), [SLUG])
        self.assertEqual(report.status, "no-recipient")
        self.assertEqual(report.exit_code, 1)

    def test_channel_missing_webhook_is_exit_code_1(self) -> None:
        """wecom 缺 WECOM_WEBHOOK 与 qq 缺 openid 是同一种失败：no-recipient。"""
        quiet = {
            "WECOM_WEBHOOK": "",
            "QQBOT_ENV_FILE": str(self.root / "nope.env"),
            "DSH_HOME": str(self.root / "no-dsh"),
        }
        with mock.patch.object(watch, "check_repo", lambda *a, **k: ["msg"]), mock.patch.dict(
            "os.environ", quiet
        ):
            report = watch.run_round(self.root, make_cfg(self.root, channel="wecom"), [SLUG])
        self.assertEqual(report.status, "no-recipient")
        self.assertEqual(report.exit_code, 1)

    def test_setup_failure_is_exit_code_1(self) -> None:
        with mock.patch.object(watch, "check_repo", lambda *a, **k: ["msg"]), mock.patch.object(
            watch.channels, "prepare", side_effect=watch.channels.ChannelSetupError("boom")
        ):
            report = watch.run_round(self.root, self.cfg, [SLUG])
        self.assertEqual(report.status, "setup-failed")
        self.assertEqual(report.exit_code, 1)

    def test_clean_round_is_exit_code_0(self) -> None:
        with mock.patch.object(watch, "check_repo", lambda *a, **k: []):
            report = watch.run_round(self.root, self.cfg, [SLUG])
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.parts, [])

    # -- 发送 ----------------------------------------------------------
    def test_parts_are_delivered_in_order(self) -> None:
        sent: list[str] = []
        with mock.patch.object(watch, "check_repo", lambda *a, **k: ["a", "b"]), mock.patch.object(
            watch.channels, "prepare", lambda cfg, root=None: sent.append
        ):
            report = watch.run_round(self.root, self.cfg, [SLUG])
        self.assertEqual(sent, ["a", "b"])
        self.assertEqual(report.sent, 2)
        self.assertEqual(report.parts, ["a", "b"])

    def test_injected_deliver_fn_beats_the_channel(self) -> None:
        """deliver_fn 是接缝的注入点：给了它就不碰任何通道（预览/测试用）。"""
        sent: list[str] = []
        with mock.patch.object(watch, "check_repo", lambda *a, **k: ["msg"]), mock.patch.object(
            watch.channels, "prepare", lambda *a, **k: self.fail("不应走到通道准备")
        ):
            report = watch.run_round(self.root, self.cfg, [SLUG], deliver_fn=sent.append)
        self.assertEqual(sent, ["msg"])
        self.assertEqual(report.sent, 1)

    def test_no_notify_advances_state_but_sends_nothing(self) -> None:
        sent: list[str] = []
        with mock.patch.object(watch, "check_repo", lambda *a, **k: ["msg"]):
            report = watch.run_round(
                self.root, self.cfg, [SLUG], no_notify=True, deliver_fn=sent.append
            )
        self.assertEqual(sent, [])
        self.assertEqual(report.status, "not-sent")
        self.assertEqual(report.exit_code, 0)

    def test_dry_run_neither_sends_nor_saves_state(self) -> None:
        sent: list[str] = []
        printed: list[str] = []
        with mock.patch.object(watch, "check_repo", lambda *a, **k: ["正文"]), mock.patch.object(
            watch, "say", printed.append
        ):
            report = watch.run_round(
                self.root, self.cfg, [SLUG], dry_run=True, deliver_fn=sent.append
            )
        self.assertEqual(sent, [])
        self.assertFalse((self.root / "state.json").exists(), "dry-run 不能落盘 state.json")
        self.assertEqual(report.status, "dry-run")
        # 预览正文必须留在报告里：面板的「预览」就是靠它渲染的。
        self.assertEqual(report.parts, ["正文"])
        self.assertIn("DRY-RUN acme/widget [1/1]", "\n".join(printed))

    # -- 并发 ----------------------------------------------------------
    def test_lock_holder_makes_the_round_skip(self) -> None:
        (self.root / ".lock").write_text("1 0\n", encoding="utf-8")
        with mock.patch.object(watch, "check_repo", lambda *a, **k: ["msg"]):
            report = watch.run_round(self.root, self.cfg, [SLUG])
        self.assertEqual(report.skipped, "lock")
        self.assertEqual(report.parts, [])
        self.assertEqual(report.exit_code, 0, "跳过不是错误")

    # -- 事件流（面板靠它更新状态） -------------------------------------
    def test_events_are_emitted_in_order(self) -> None:
        events: list[dict] = []
        with mock.patch.object(watch, "check_repo", lambda *a, **k: []):
            watch.run_round(self.root, self.cfg, [SLUG], on_event=events.append)
        self.assertEqual(
            [event["kind"] for event in events],
            ["round-start", "repo-start", "repo-done", "round-done"],
        )
        self.assertEqual(events[0]["repos"], 1)

    def test_repo_failure_emits_an_error_event(self) -> None:
        events: list[dict] = []

        def boom(*_a: object, **_k: object) -> list[str]:
            raise watch.GitError("fetch failed")

        with mock.patch.object(watch, "check_repo", boom):
            report = watch.run_round(self.root, self.cfg, [SLUG], on_event=events.append)
        kinds = [event["kind"] for event in events]
        self.assertIn("repo-error", kinds)
        self.assertEqual(report.failures, 1)
        self.assertEqual(report.exit_code, 0, "抓取失败不是退出码错误，只有发不出去才是")

    def test_log_lines_follow_the_language_config(self) -> None:
        """language=en 时日志行整个换语言 —— 不只摘要，日志也在「全中文或全英文」里。"""
        watch.run_round(self.root, make_cfg(self.root, repos=[], language="en"))
        logdir = self.root / "logs"
        content = "\n".join(path.read_text(encoding="utf-8") for path in logdir.glob("watch-*.log"))
        self.assertIn("no repository list in config", content)
        self.assertNotIn("配置里没有仓库清单", content)


@unittest.skipUnless(shutil.which("git"), "需要 git")
class RoundSeamRealRepoTest(unittest.TestCase):
    """真仓版本：证明 dry-run 确实不推进基线，而 no_notify 会。"""

    def setUp(self) -> None:
        self.base = temp_root()
        self.addCleanup(rmtree, self.base)
        self.remote = Remote(self.base)
        self.root = self.base / "data"
        self.root.mkdir()
        self.cfg = make_cfg(self.root)
        self.remote.commit("first")
        self.remote.push("HEAD:refs/heads/main")
        self.mirror = watch.ensure_mirror(self.root, SLUG, self.cfg)[0]
        git(["remote", "set-url", "origin", str(self.remote.bare)], cwd=self.mirror)
        state = watch.load_state(self.root)
        self.assertEqual(watch.check_repo(self.root, SLUG, state, self.cfg), [], "首轮静默建基线")
        watch.save_state(self.root, state)

    def test_dry_run_does_not_advance_the_baseline(self) -> None:
        before = (self.root / "state.json").read_bytes()

        self.remote.commit("second")
        self.remote.push("HEAD:refs/heads/main")

        with mock.patch.object(watch, "say", lambda *a: None):
            preview = watch.run_round(self.root, self.cfg, [SLUG], dry_run=True)
        self.assertEqual((self.root / "state.json").read_bytes(), before, "dry-run 不能推进 ref 基线")
        self.assertIn("1 个新提交", "\n".join(preview.parts))

        real = watch.run_round(self.root, self.cfg, [SLUG], no_notify=True)  # 不发送，但推进
        self.assertIn("1 个新提交", "\n".join(real.parts), "dry-run 之后的真跑必须仍然报这次变化")
        self.assertNotEqual((self.root / "state.json").read_bytes(), before)

        again = watch.run_round(self.root, self.cfg, [SLUG], no_notify=True)
        self.assertEqual(again.parts, [], "基线推进后，第二轮不该再报同一件事")


if __name__ == "__main__":
    unittest.main()
