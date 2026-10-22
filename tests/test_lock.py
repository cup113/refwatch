"""一轮锁：上一轮没跑完时下一轮跳过；过期锁可接管；失败方不删别人的锁。"""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from helpers import rmtree, temp_root  # noqa: E402

import watch  # noqa: E402


class LockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = temp_root()
        self.addCleanup(rmtree, self.root)

    def test_second_round_skips_and_does_not_steal(self) -> None:
        with watch.Lock(self.root, 1800) as first:
            self.assertTrue(first)
            with watch.Lock(self.root, 1800) as second:
                self.assertFalse(second)
                self.assertTrue((self.root / ".lock").exists(), "抢锁失败的一轮不能删掉别人的锁")
        self.assertFalse((self.root / ".lock").exists(), "正常退出要释放锁")

    def test_stale_lock_is_taken_over(self) -> None:
        stale = self.root / ".lock"
        stale.write_text("1 0\n", encoding="utf-8")
        old = time.time() - 9999
        os.utime(stale, (old, old))
        with watch.Lock(self.root, 60) as acquired:
            self.assertTrue(acquired)
        self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
