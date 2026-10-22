"""调度器：暂停只停调度、手动一轮不挪动时间点、同一时刻只有一轮。

用注入的 runner + 很小的间隔来测，全程不睡 30 分钟也不碰网络。
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import scheduler  # noqa: E402


class Counting:
    """可等待的 runner：跑一次就 set 一个 Event，测试不必 sleep 猜。"""

    def __init__(self) -> None:
        self.calls: list[float] = []
        self.ran = threading.Event()
        self.gate: threading.Event | None = None

    def __call__(self) -> None:
        self.calls.append(time.monotonic())
        if self.gate is not None:
            self.gate.wait(5.0)
        self.ran.set()


class SchedulerTest(unittest.TestCase):
    def make(self, runner, **kwargs) -> scheduler.Scheduler:  # noqa: ANN001
        kwargs.setdefault("first_delay", 0.01)
        scheduler_obj = scheduler.Scheduler(runner, **kwargs)
        self.addCleanup(scheduler_obj.stop)
        return scheduler_obj

    def test_first_round_runs_after_the_first_delay(self) -> None:
        runner = Counting()
        sched = self.make(runner)
        sched.start()
        self.assertTrue(runner.ran.wait(2.0), "启动后应当跑第一轮")

    def test_pause_stops_the_clock_and_resume_restarts_it(self) -> None:
        runner = Counting()
        sched = self.make(runner)
        sched.pause()
        self.assertIsNone(sched.next_run_at(), "暂停时没有下一次")
        sched.start()
        self.assertFalse(runner.ran.wait(0.3), "暂停期间不该跑")
        sched.resume()
        self.assertTrue(runner.ran.wait(2.0), "恢复后应当按 first_delay 跑一轮")
        self.assertIsNotNone(sched.next_run_at())

    def test_stop_interrupts_a_long_wait(self) -> None:
        sched = self.make(Counting(), interval_minutes=600)
        sched.start()
        started = time.monotonic()
        sched.stop()
        self.assertLess(time.monotonic() - started, 2.0, "stop 必须打断等待，而不是等满 10 小时")

    def test_manual_round_does_not_move_the_schedule(self) -> None:
        runner = Counting()
        sched = self.make(runner, interval_minutes=30)
        sched.start()
        self.assertTrue(runner.ran.wait(2.0))
        before = sched.next_run_at()
        self.assertIsNotNone(before)
        runner.ran.clear()
        self.assertTrue(sched.trigger_now())
        self.assertTrue(runner.ran.wait(2.0), "手动一轮应当真的跑")
        after = sched.next_run_at()
        assert before is not None and after is not None
        self.assertLess(abs((after - before).total_seconds()), 2.0, "手动一轮不能挪动下一次的时间点")

    def test_manual_round_is_refused_while_one_is_running(self) -> None:
        runner = Counting()
        runner.gate = threading.Event()
        sched = self.make(runner)
        self.assertTrue(sched.trigger_now())
        deadline = time.monotonic() + 2.0
        while not sched.running and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(sched.running, "手动一轮应当处于运行中")
        self.assertFalse(sched.trigger_now(), "已经在跑时不能再起一轮")
        runner.gate.set()

    def test_set_interval_reschedules(self) -> None:
        runner = Counting()
        sched = self.make(runner, interval_minutes=30)
        sched.start()
        self.assertTrue(runner.ran.wait(2.0))
        before = sched.next_run_at()
        assert before is not None
        sched.set_interval(1)
        after = sched.next_run_at()
        assert after is not None
        self.assertLess(after - datetime.now(), timedelta(minutes=2), "改成 1 分钟后应当很快再跑")
        self.assertEqual(sched.interval_minutes, 1)

    def test_interval_floor_is_one_minute(self) -> None:
        sched = self.make(Counting(), interval_minutes=0)
        self.assertEqual(sched.interval_minutes, 1)

    def test_pause_and_resume_idempotent(self) -> None:
        sched = self.make(Counting())
        sched.pause()
        sched.pause()
        self.assertTrue(sched.paused)
        sched.resume()
        sched.resume()
        self.assertFalse(sched.paused)

    def test_on_change_fires_for_control_and_rounds(self) -> None:
        changes: list[int] = []
        runner = Counting()
        sched = self.make(runner, on_change=lambda: changes.append(1))
        sched.start()
        self.assertTrue(runner.ran.wait(2.0))
        self.assertGreaterEqual(len(changes), 2, "一轮开始/结束各通知一次")
        sched.pause()
        self.assertGreaterEqual(len(changes), 3, "暂停也要通知（菜单得重画）")


if __name__ == "__main__":
    unittest.main()
