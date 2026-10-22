"""消息构造：摘要 + 链接，且不超过 chunk 上限。"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import watch  # noqa: E402

CFG = dict(watch.DEFAULT_CONFIG)
WHEN = datetime(2026, 1, 2, 3, 4)


def commit(subject: str = "fix: one", **overrides: object) -> dict:
    base = {
        "sha": "b" * 40,
        "author": "ann lee",
        "when": WHEN,
        "subject": subject,
        "added": 3,
        "deleted": 1,
        "merge": False,
    }
    base.update(overrides)
    return base


class BuildMessageTest(unittest.TestCase):
    def test_update_has_compare_link_and_counts(self) -> None:
        event = {
            "kind": "update",
            "ref": "refs/heads/main",
            "old": "a" * 40,
            "new": "b" * 40,
            "commits": [commit()],
            "total": 3,
            "kind_label": "branch",
        }
        message = watch.build_repo_message("acme/widget", [event], CFG)
        self.assertIn("[acme/widget] 1 个 ref 有更新", message)
        self.assertIn("* main  3 个新提交", message)
        self.assertIn("+3/-1", message)
        self.assertIn("compare: https://github.com/acme/widget/compare/aaaaaaaaaaaa..bbbbbbbbbbbb", message)
        self.assertIn("另有 2 个较早提交未列出", message)

    def test_merge_commit_shows_merge_instead_of_stats(self) -> None:
        event = {
            "kind": "update",
            "ref": "refs/heads/main",
            "old": "a" * 40,
            "new": "b" * 40,
            "commits": [commit(merge=True)],
            "total": 1,
            "kind_label": "branch",
        }
        self.assertIn("ann lee merge", watch.build_repo_message("acme/widget", [event], CFG))

    def test_deleted_and_new_branch(self) -> None:
        events = [
            {"kind": "deleted", "ref": "refs/heads/gone"},
            {"kind": "new", "ref": "refs/heads/fresh", "new": "c" * 40, "kind_label": "branch", "commits": []},
        ]
        message = watch.build_repo_message("acme/widget", events, CFG)
        self.assertIn("分支已删除: gone", message)
        self.assertIn("新分支: fresh", message)

    def test_new_tag_links_to_release_page(self) -> None:
        event = {"kind": "update", "ref": "refs/tags/v1.2.0", "old": "a" * 40, "new": "b" * 40,
                 "commits": [], "total": 0, "kind_label": "tag"}
        message = watch.build_repo_message("acme/widget", [event], CFG)
        self.assertIn("tag: https://github.com/acme/widget/releases/tag/v1.2.0", message)

    def test_rewritten_branch_names_the_old_head(self) -> None:
        event = {"kind": "rewritten", "ref": "refs/heads/main", "old": "a" * 40, "new": "b" * 40,
                 "commits": [commit("reworked")]}
        message = watch.build_repo_message("acme/widget", [event], CFG)
        self.assertIn("分支历史被改写: main  aaaaaaa -> bbbbbbb", message)
        self.assertIn("https://github.com/acme/widget/commit/bbbbbbbbbbbb", message)


class ChunkTest(unittest.TestCase):
    def test_exact_limit_is_one_part(self) -> None:
        self.assertEqual(len(watch.chunk("x" * 4500, 4500)), 1)

    def test_over_limit_splits(self) -> None:
        parts = watch.chunk("x" * 4501, 4500)
        self.assertEqual([len(p) for p in parts], [4500, 1])


if __name__ == "__main__":
    unittest.main()
