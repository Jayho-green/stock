"""冷却去重:同一 (code, rule) 在冷却期内只放行一次,防止刷屏。"""

from __future__ import annotations

import time
from collections.abc import Callable


class Deduper:
    def __init__(self, cooldown_seconds: float = 900, now: Callable[[], float] = time.time):
        self.cooldown = cooldown_seconds
        self._now = now
        self._last: dict[tuple[str, str], float] = {}

    def should_notify(self, code: str, rule: str) -> bool:
        """首次或超过冷却时间则放行(并刷新时间戳),否则拦截。"""
        t = self._now()
        key = (code, rule)
        last = self._last.get(key)
        if last is None or (t - last) >= self.cooldown:
            self._last[key] = t
            return True
        return False
