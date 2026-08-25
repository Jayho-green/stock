"""盘后 K 线本地缓存。

规则:
- 15:30 之后到次日 08:00 之前,日 K 和当日分时视为稳定数据,优先复用本地缓存。
- 08:00 到 15:30 之间仍实时拉取,不读写这个文件缓存。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

POST_CLOSE_TIME = time(15, 30)
MORNING_REFRESH_TIME = time(8, 0)


def effective_market_date(clock: Callable[[], datetime] = datetime.now) -> date:
    """当前请求应使用的数据日期:08:00 前仍按上一日收盘数据处理。"""

    now = clock()
    if now.time() < MORNING_REFRESH_TIME:
        return now.date() - timedelta(days=1)
    return now.date()


class PostCloseKlineCache:
    """把盘后稳定的 K 线写入本地文件,降低重复请求数据源的次数。"""

    def __init__(self, root: Path | str, clock: Callable[[], datetime] = datetime.now):
        self.root = Path(root)
        self.clock = clock

    def active_session_date(self) -> date | None:
        now = self.clock()
        current_time = now.time()
        if current_time >= POST_CLOSE_TIME:
            return now.date()
        if current_time < MORNING_REFRESH_TIME:
            return now.date() - timedelta(days=1)
        return None

    def get_or_fetch(
        self,
        period: str,
        code: str,
        fetcher: Callable[[], pd.DataFrame],
    ) -> pd.DataFrame:
        session_date = self.active_session_date()
        if session_date is None:
            return fetcher()

        cached = self.read(period, code, session_date)
        if cached is not None:
            return cached

        bars = fetcher()
        self.write(period, code, session_date, bars)
        return bars.copy()

    def read(self, period: str, code: str, session_date: date) -> pd.DataFrame | None:
        path = self._path(period, code)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

        if payload.get("session_date") != session_date.isoformat():
            return None
        if payload.get("period") != period:
            return None
        if str(payload.get("code", "")).zfill(6) != str(code).zfill(6):
            return None

        rows = payload.get("rows")
        if not isinstance(rows, list):
            return None
        columns = payload.get("columns")
        if not isinstance(columns, list):
            columns = None
        frame = pd.DataFrame(rows, columns=columns)
        return self._normalize(frame)

    def write(self, period: str, code: str, session_date: date, bars: pd.DataFrame) -> None:
        path = self._path(period, code)
        path.parent.mkdir(parents=True, exist_ok=True)
        frame = bars.copy()
        rows = json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False))
        payload = {
            "session_date": session_date.isoformat(),
            "period": period,
            "code": str(code).zfill(6),
            "columns": list(frame.columns),
            "rows": rows,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _path(self, period: str, code: str) -> Path:
        safe_period = "".join(ch for ch in period if ch.isalnum() or ch in {"_", "-"})
        return self.root / safe_period / f"{str(code).zfill(6)}.json"

    @staticmethod
    def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
        frame = frame.copy()
        if "datetime" in frame.columns:
            frame["datetime"] = pd.to_datetime(frame["datetime"])
        for col in ["open", "high", "low", "close", "volume"]:
            if col in frame.columns:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
        return frame
