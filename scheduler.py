#!/usr/bin/env python3
"""按 interval_minutes 跑一轮的调度器 —— 托盘应用的心跳。

刻意不 import 任何 GUI 库：runner 和时钟都是注入的，所以这段逻辑在没有显示环境
（CI 的 ubuntu runner、纯命令行）也能测。三条不变式：

  * 启动后先等 first_delay 跑第一轮（给界面留出出现的时间），之后按 interval 走；
  * **暂停只停调度**，不影响手动一轮；手动一轮**不挪动**下一次的时间点；
  * 同一时刻只有一轮在跑（上一轮没结束就忽略新的触发，锁仍由 watch.Lock 兜底）。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Callable

FIRST_RUN_DELAY = 5.0  # 秒：登录后先让图标出现，再开始抓取


class Scheduler:
    def __init__(
        self,
        runner: Callable[[], object],
        interval_minutes: int = 30,
        on_change: Callable[[], None] | None = None,
        first_delay: float = FIRST_RUN_DELAY,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runner = runner
        self._on_change = on_change or (lambda: None)
        self._interval = max(1, int(interval_minutes))
        self._first_delay = max(0.0, float(first_delay))
        self._clock = clock
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._round_guard = threading.Lock()
        self._thread: threading.Thread | None = None
        self._paused = False
        self._running = False
        self._last_run: float | None = None

    # -- 只读状态（菜单渲染时会被频繁调用） ------------------------------
    @property
    def interval_minutes(self) -> int:
        return self._interval

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def running(self) -> bool:
        return self._running

    def next_run_at(self) -> datetime | None:
        delay = self._next_delay()
        if delay is None:
            return None
        return datetime.now() + timedelta(seconds=delay)

    # -- 生命周期 ------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._loop, name="refwatch-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """打断等待并等线程收敛；一轮正在进行时最多等 timeout 秒。"""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout)

    # -- 控制 ----------------------------------------------------------
    def set_interval(self, minutes: int) -> None:
        interval = max(1, int(minutes))
        if interval == self._interval:
            return
        self._interval = interval
        self._wake.set()  # 让等待中的循环按新间隔重算
        self._on_change()

    def pause(self) -> None:
        if self._paused:
            return
        self._paused = True
        self._wake.set()
        self._on_change()

    def resume(self) -> None:
        if not self._paused:
            return
        self._paused = False
        # 与「刚启动」同样处理：first_delay 之后跑一轮 —— 用户刚解除暂停，
        # 想知道现在有没有变化，而不是再等一整个 interval。
        self._last_run = None
        self._wake.set()
        self._on_change()

    def trigger_now(self) -> bool:
        """手动一轮：另起线程跑，立刻返回；已经在跑则拒绝。"""
        if self._running or self._round_guard.locked():
            return False
        threading.Thread(target=self._execute, kwargs={"advance": False}, name="refwatch-manual", daemon=True).start()
        return True

    # -- 内部 ----------------------------------------------------------
    def _next_delay(self) -> float | None:
        if self._paused:
            return None
        if self._last_run is None:
            return self._first_delay
        return max(0.0, self._last_run + self._interval * 60 - self._clock())

    def _loop(self) -> None:
        while not self._stop.is_set():
            delay = self._next_delay()
            if delay is None:  # 暂停：等唤醒（resume / stop / set_interval）
                self._wake.wait()
                self._wake.clear()
                continue
            if delay > 0 and self._wake.wait(delay):
                self._wake.clear()
                continue
            if self._stop.is_set():
                return
            if self._paused:
                continue
            self._wake.clear()
            self._execute(advance=True)

    def _execute(self, *, advance: bool) -> None:
        # 非阻塞抢锁：定时线程与手动一轮不会叠加，抢不到的这一次直接放弃。
        if not self._round_guard.acquire(blocking=False):
            return
        try:
            self._running = True
            if advance:
                self._last_run = self._clock()
            self._on_change()
            self._runner()
        finally:
            self._running = False
            self._round_guard.release()
            self._on_change()
