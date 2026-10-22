"""ref -> SHA 状态机：首次静默、update、rewritten、new、deleted 五种情形。"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from helpers import SLUG, Remote, git, rmtree, temp_root  # noqa: E402

import watch  # noqa: E402


@unittest.skipUnless(shutil.which("git"), "需要 git")
class StateMachineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = temp_root()
        self.addCleanup(rmtree, self.base)
        self.remote = Remote(self.base)
        self.root = self.base / "data"
        self.root.mkdir()
        self.cfg = dict(watch.DEFAULT_CONFIG, repos=[SLUG], openid="oid", notify=True, git_ssl_backend="")
        self.state: dict = {"version": 1, "repos": {}}
        self.mirror: Path | None = None

    # -- 脚手架 --------------------------------------------------------
    def first_commit(self) -> str:
        sha = self.remote.commit("first")
        self.remote.push("HEAD:refs/heads/main")
        return sha

    def baseline(self, *refspecs: str) -> dict[str, str]:
        """建镜像仓 -> 指向本地 bare -> 跑一次（应当静默建基线）。"""
        if refspecs:
            self.remote.push(*refspecs)
        self.mirror = watch.ensure_mirror(self.root, SLUG, self.cfg)[0]
        git(["remote", "set-url", "origin", str(self.remote.bare)], cwd=self.mirror)
        self.assertEqual(watch.check_repo(self.root, SLUG, self.state, self.cfg, False), [])
        refs = self.state["repos"][SLUG]["refs"]
        self.assertTrue(refs, "基线应当记录 refs")
        return refs  # type: ignore[no-any-return]

    def cycle(self) -> list[str]:
        return watch.check_repo(self.root, SLUG, self.state, self.cfg, False)

    # -- 用例 ----------------------------------------------------------
    def test_baseline_is_silent_and_records_every_ref(self) -> None:
        self.first_commit()
        self.remote.tag("v1")
        refs = self.baseline("refs/tags/v1")
        self.assertIn("refs/heads/main", refs)
        self.assertIn("refs/tags/v1", refs)
        self.assertEqual(self.cycle(), [], "没有变化的一轮不该产生消息")

    def test_new_commit_on_branch(self) -> None:
        self.first_commit()
        self.baseline()
        self.remote.commit("second")
        self.remote.push("HEAD:refs/heads/main")
        parts = self.cycle()
        self.assertEqual(len(parts), 1)
        self.assertIn("1 个新提交", parts[0])
        self.assertIn("compare: https://github.com/acme/widget/compare/", parts[0])
        self.assertEqual(self.state["repos"][SLUG]["refs"]["refs/heads/main"], git(["rev-parse", "HEAD"], cwd=self.remote.work).strip())

    def test_rewritten_history_detected_by_ancestry(self) -> None:
        first = self.first_commit()
        self.remote.commit("second")
        self.remote.push("HEAD:refs/heads/main")
        self.baseline()
        self.remote.reset_to(first)
        self.remote.commit("rewritten")
        self.remote.push("HEAD:refs/heads/main", force=True)
        parts = self.cycle()
        self.assertEqual(len(parts), 1)
        self.assertIn("分支历史被改写", parts[0])

    def test_new_branch_reports_tip_commit(self) -> None:
        self.first_commit()
        self.baseline()
        self.remote.commit("feature work")
        self.remote.push("HEAD:refs/heads/feature")
        parts = self.cycle()
        self.assertEqual(len(parts), 1)
        self.assertIn("新分支: feature", parts[0])

    def test_new_branch_at_root_commit_does_not_crash(self) -> None:
        """只有 1 个提交的仓库 `<sha>~1` 不存在，不能因此把整轮搞失败。"""
        self.first_commit()
        self.baseline()
        self.remote.branch("solo")
        self.remote.push("solo")
        parts = self.cycle()
        self.assertEqual(len(parts), 1)
        self.assertIn("新分支: solo", parts[0])

    def test_deleted_branch(self) -> None:
        self.first_commit()
        self.remote.branch("feature")
        self.baseline("feature")
        self.remote.delete_remote_branch("feature")
        parts = self.cycle()
        self.assertEqual(len(parts), 1)
        self.assertIn("分支已删除: feature", parts[0])

    def test_moved_tag(self) -> None:
        self.first_commit()
        self.remote.tag("v1")
        self.baseline("refs/tags/v1")
        self.remote.commit("move tag")
        self.remote.tag("v1", force=True)
        self.remote.push("refs/tags/v1", force=True)
        parts = self.cycle()
        self.assertEqual(len(parts), 1)
        self.assertIn("标签被移动: v1", parts[0])

    def test_fetch_failure_raises_git_error(self) -> None:
        self.first_commit()
        self.baseline()
        rmtree(self.remote.bare)
        with self.assertRaises(watch.GitError):
            self.cycle()


if __name__ == "__main__":
    unittest.main()
