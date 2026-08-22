"""后台选股任务器:面板"立即选股"按钮触发,异步运行,前端轮询状态。

选股要跑几分钟,不能阻塞请求,所以在后台线程跑;同一时刻只允许一个任务。
start(strategy) 指定本次用哪套选股方案。
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable


class ScreenRunner:
    def __init__(self, run_fn: Callable[[str], dict]):
        self._run_fn = run_fn  # run_fn(strategy) -> dict
        sig = inspect.signature(run_fn)
        self._accepts_progress = (
            len(sig.parameters) >= 2
            or any(p.kind == p.VAR_POSITIONAL for p in sig.parameters.values())
            or any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
        )
        self._accepts_scope = (
            len(sig.parameters) >= 3
            or any(p.kind == p.VAR_POSITIONAL for p in sig.parameters.values())
            or any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
        )
        self._lock = threading.Lock()
        self._running = False
        self._strategy: str | None = None
        self._scope: str | None = None
        self._last: dict | None = None
        self._progress: dict | None = None

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
        threading.Thread(target=self._worker, args=(strategy, scope), daemon=True).start()
        return True

    def _set_progress(self, progress: dict) -> None:
        with self._lock:
            self._progress = progress

    def _worker(self, strategy: str, scope: str | None = None) -> None:
        try:
            if self._accepts_scope:
                result = self._run_fn(strategy, scope, self._set_progress)
            elif self._accepts_progress:
                result = self._run_fn(strategy, self._set_progress)
            else:
                result = self._run_fn(strategy)
            with self._lock:
                self._last = {"ok": True, **result}
        except Exception as e:  # 选股失败不应让任务器卡在 running
            with self._lock:
                self._last = {"ok": False, "strategy": strategy, "error": str(e)}
        finally:
            with self._lock:
                self._running = False
