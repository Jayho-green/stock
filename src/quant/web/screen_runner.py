"""后台选股任务器:面板"立即选股"按钮触发,异步运行,前端轮询状态。

选股要跑几分钟,不能阻塞请求,所以在后台线程跑;同一时刻只允许一个任务。
start(strategy) 指定本次用哪套选股方案。
cancel() 请求停止:选股循环尽快退出,重试等待立即中断,已完成的结果保留。
"""

from __future__ import annotations

import inspect
import threading
import time
from collections.abc import Callable

MAX_SCREEN_RETRIES = 30
SCREEN_RETRY_DELAY = 60  # 秒


class ScreenRunner:
    def __init__(self, run_fn: Callable[..., dict]):
        self._run_fn = run_fn  # run_fn(strategy, scope, progress[, stop_check]) -> dict
        sig = inspect.signature(run_fn)
        params = list(sig.parameters.values())
        has_var = any(p.kind == p.VAR_POSITIONAL or p.kind == p.VAR_KEYWORD for p in params)
        self._accepts_stop = has_var or len(params) >= 4
        self._accepts_scope = has_var or len(params) >= 3
        self._accepts_progress = has_var or len(params) >= 2
        self._lock = threading.Lock()
        self._running = False
        self._strategy: str | None = None
        self._scope: str | None = None
        self._last: dict | None = None
        self._progress: dict | None = None
        self._cancel = threading.Event()

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "strategy": self._strategy,
                "scope": self._scope,
                "last": self._last,
                "progress": self._progress,
            }

    def start(self, strategy: str, scope: str | None = None) -> bool:
        """启动一次后台选股(用指定方案);已在运行则返回 False。"""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._strategy = strategy
            self._scope = scope
            self._progress = None
        self._cancel = threading.Event()
        threading.Thread(target=self._worker, args=(strategy, scope), daemon=True).start()
        return True

    def cancel(self) -> bool:
        """请求停止当前选股;没有在跑的任务则返回 False。"""
        with self._lock:
            if not self._running:
                return False
        self._cancel.set()
        return True

    def _set_progress(self, progress: dict) -> None:
        with self._lock:
            self._progress = progress

    def _invoke_run(self, strategy: str, scope: str | None) -> dict:
        stop_check = self._cancel.is_set
        if self._accepts_stop:
            return self._run_fn(strategy, scope, self._set_progress, stop_check)
        if self._accepts_scope:
            return self._run_fn(strategy, scope, self._set_progress)
        if self._accepts_progress:
            return self._run_fn(strategy, self._set_progress)
        return self._run_fn(strategy)

    def _worker(self, strategy: str, scope: str | None = None) -> None:
        retry = 0
        try:
            while True:
                try:
                    result = self._invoke_run(strategy, scope)
                except Exception as e:
                    # 拉全A表等早期步骤被限流打断:等待后重试(可随时手动停止)
                    if self._cancel.is_set() or retry >= MAX_SCREEN_RETRIES:
                        with self._lock:
                            self._last = {"ok": False, "strategy": strategy, "error": str(e)}
                        return
                    retry += 1
                    if self._wait_retry(retry, error=str(e)):
                        with self._lock:
                            self._last = {"ok": False, "cancelled": True, "strategy": strategy, "error": str(e)}
                        return
                    continue
                if self._cancel.is_set() or result.get("stopped"):
                    # 用户停止:保留已完成的结果
                    with self._lock:
                        self._last = {"ok": True, "cancelled": True, **result}
                    return
                if result.get("complete", True):
                    with self._lock:
                        self._last = {"ok": True, **result}
                    return
                # 未完成(限流中断),等待后自动重试,续跑剩余股票
                if retry >= MAX_SCREEN_RETRIES:
                    with self._lock:
                        self._last = {"ok": True, **result, "retries_exhausted": True}
                    return
                retry += 1
                stopped = self._wait_retry(retry)
                if stopped:
                    with self._lock:
                        self._last = {"ok": True, "cancelled": True, **result}
                    return
        finally:
            with self._lock:
                self._running = False

    def _wait_retry(self, attempt: int, error: str | None = None) -> bool:
        """等待后重试;期间被取消则返回 True。"""
        with self._lock:
            self._progress = {
                **(self._progress or {}),
                "retrying": True,
                "retry_attempt": attempt,
                "max_retries": MAX_SCREEN_RETRIES,
                "retry_delay_seconds": SCREEN_RETRY_DELAY,
                **({"retry_error": error} if error else {}),
            }
        return self._cancel.wait(SCREEN_RETRY_DELAY)
