"""ref 过滤：`refs/pull/*` 永远不跟踪，`exclude_ref_patterns` 生效。"""

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


class RefNameTest(unittest.TestCase):
    def test_short_ref(self) -> None:
        self.assertEqual(watch.short_ref("refs/heads/main"), "main")
        self.assertEqual(watch.short_ref("refs/tags/v1.2.3"), "v1.2.3")
        self.assertEqual(watch.short_ref("refs/notes/x"), "refs/notes/x")

    def test_kind_of(self) -> None:
        self.assertEqual(watch.kind_of("refs/heads/main"), "branch")
        self.assertEqual(watch.kind_of("refs/tags/v1"), "tag")


@unittest.skipUnless(shutil.which("git"), "需要 git")
class ListRefsTest(unittest.TestCase):
    def test_platform_pr_refs_and_exclusions(self) -> None:
        base = temp_root()
        self.addCleanup(rmtree, base)
        remote = Remote(base)
        sha = remote.commit("first")
        remote.push("HEAD:refs/heads/main")
        remote.branch("dependabot/pip-bump")
        remote.push("dependabot/pip-bump")
        remote.tag("v1")
        remote.push("refs/tags/v1")

        root = base / "data"
        root.mkdir()
        cfg = dict(watch.DEFAULT_CONFIG, git_ssl_backend="")
        mirror = watch.ensure_mirror(root, SLUG, cfg)[0]
        git(["remote", "set-url", "origin", str(remote.bare)], cwd=mirror)
        watch.fetch_mirror(mirror, 60, cfg)
        # GitHub 会把每个 PR 广播成 refs/pull/*，GitLab 对应 merge-requests。
        git(["update-ref", "refs/pull/7/head", sha], cwd=mirror)
        git(["update-ref", "refs/merge-requests/9/head", sha], cwd=mirror)

        refs = watch.list_refs(mirror, ["refs/heads/dependabot/*"], cfg)
        self.assertIn("refs/heads/main", refs)
        self.assertIn("refs/tags/v1", refs)
        for absent in ("refs/heads/dependabot/pip-bump", "refs/pull/7/head", "refs/merge-requests/9/head"):
            self.assertNotIn(absent, refs)


if __name__ == "__main__":
    unittest.main()
