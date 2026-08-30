"""0AMV(无穷成本均线)面板服务。

盘后数据稳定,因此走本地缓存:
- 15:30 之后到次日 08:00 之前,当日收盘数据视为稳定,缓存命中即返回。
- 缓存过期时后台线程异步刷新,请求先拿到旧数据并带 ``stale`` 标记,不阻塞页面。
- 每个交易日 15:35 由 launchd 触发 ``scripts/run_amv0_update.py`` 主动刷新。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from ..amv0 import (
    DEEP_DISCOUNT,
    WATCH_DISCOUNT,
    DataUnavailable,
    Instrument,
    compute_cyc,
    fetch_capital,
    fetch_qfq_bars,
    fetch_raw_bars,
    universe_list,
    zone_of,
)

POST_CLOSE_TIME = time(15, 30)
MORNING_REFRESH_TIME = time(8, 0)
_SERIES_COLUMNS = [
    "date", "open", "high", "low", "close", "volume",
    "amv0", "cyc5", "cyc13", "cyc34", "cys0", "alpha",
    "buy_signal", "align_signal", "deep_discount",
]


def effective_session_date(clock: Callable[[], datetime] = datetime.now) -> date:
    """当前应展示的交易日:08:00 前仍按上一自然日处理,并回退到最近的工作日。

    (只处理周末;法定休市日最多导致一次多余刷新,数据不会出错。)
    """

    now = clock()
    day = now.date() - timedelta(days=1) if now.time() < MORNING_REFRESH_TIME else now.date()
    while day.weekday() >= 5:  # 5=周六 6=周日
        day -= timedelta(days=1)
    return day


def _clean(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else round(number, 6)


class Amv0Service:
    def __init__(
        self,
        cache_dir: Path | str,
        clock: Callable[[], datetime] = datetime.now,
        instruments: list[Instrument] | None = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.clock = clock
        self.instruments = instruments if instruments is not None else universe_list()
        self._lock = threading.Lock()
        self._refreshing = False
        self._last_result: dict | None = None

    # ---------- 缓存读写 ----------

    @property
    def _meta_path(self) -> Path:
        return self.cache_dir / "_meta.json"

    def _series_path(self, code: str) -> Path:
        return self.cache_dir / f"{code}.json"

    def read_meta(self) -> dict:
        try:
            return json.loads(self._meta_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def read_series(self, code: str) -> dict | None:
        try:
            return json.loads(self._series_path(code).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    # ---------- 刷新 ----------

    def is_stale(self) -> bool:
        """缓存是否需要刷新。收盘前只要当天已刷新过就不再重复拉取。"""

        meta = self.read_meta()
        cached = meta.get("session_date")
        if not cached:
            return True
        now = self.clock()
        target = effective_session_date(self.clock)
        if now.time() < POST_CLOSE_TIME and cached == target.isoformat():
            return False
        return cached != target.isoformat()

    def refresh(self, force: bool = False) -> dict:
        """同步刷新全部标的。返回本次刷新摘要。"""

        if not force and not self.is_stale():
            meta = self.read_meta()
            return {"skipped": True, "session_date": meta.get("session_date"), "ok": len(meta.get("ok", []))}

        codes = [item.code for item in self.instruments]
        try:
            capitals = fetch_capital(codes)
        except DataUnavailable:
            capitals = {}

        ok: list[str] = []
        failed: list[dict] = []
        session = effective_session_date(self.clock).isoformat()
        for item in self.instruments:
            try:
                capital = capitals.get(item.code)
                if not capital:
                    raise DataUnavailable("未取得流通份额")
                frame = compute_cyc(fetch_raw_bars(item.code), fetch_qfq_bars(item.code), capital)
                self._write_json(
                    self._series_path(item.code),
                    {
                        "session_date": session,
                        "code": item.code,
                        "name": item.name,
                        "sector": item.sector,
                        "capital": capital,
                        "rows": [
                            {col: (row[col] if col == "date" else _clean(row[col])) for col in _SERIES_COLUMNS}
                            for _, row in frame.tail(600).iterrows()
                        ],
                    },
                )
                ok.append(item.code)
            except Exception as exc:  # 单只失败不影响整体
                failed.append({"code": item.code, "name": item.name, "error": str(exc)[:160]})

        summary = {
            "session_date": session,
            "updated_at": self.clock().strftime("%Y-%m-%d %H:%M:%S"),
            "ok": ok,
            "failed": failed,
        }
        if ok:
            self._write_json(self._meta_path, summary)
        return summary

    def refresh_async(self) -> bool:
        """后台刷新;已有任务在跑则返回 False。"""

        with self._lock:
            if self._refreshing:
                return False
            self._refreshing = True

        def worker() -> None:
            try:
                result = self.refresh()
                with self._lock:
                    self._last_result = {"ok": True, **result}
            except Exception as exc:
                with self._lock:
                    self._last_result = {"ok": False, "error": str(exc)[:200]}
            finally:
                with self._lock:
                    self._refreshing = False

        threading.Thread(target=worker, daemon=True).start()
        return True

    def status(self) -> dict:
        meta = self.read_meta()
        with self._lock:
            refreshing = self._refreshing
            last = self._last_result
        return {
            "session_date": meta.get("session_date"),
            "updated_at": meta.get("updated_at"),
            "instruments": len(meta.get("ok", [])),
            "failed": meta.get("failed", []),
            "stale": self.is_stale(),
            "refreshing": refreshing,
            "last_refresh": last,
        }

    # ---------- 对外数据 ----------

    def get_overview(self, auto_refresh: bool = True) -> dict:
        """总览:每只标的最新 0AMV 状态 + 市场宽度序列。"""

        if auto_refresh and self.is_stale():
            self.refresh_async()

        rows: list[dict] = []
        breadth_frames: list[pd.Series] = []
        for item in self.instruments:
            payload = self.read_series(item.code)
            if not payload or not payload.get("rows"):
                continue
            frame = pd.DataFrame(payload["rows"])
            last = frame.iloc[-1]
            alpha_mean = frame["alpha"].tail(120).mean()
            cys0 = _clean(last["cys0"])
            # 最近一次超跌反转信号距今多少个交易日
            fired = frame.index[frame.get("buy_signal", pd.Series(0, index=frame.index)) == 1]
            rows.append(
                {
                    "code": item.code,
                    "name": item.name,
                    "sector": item.sector,
                    "date": last["date"],
                    "close": _clean(last["close"]),
                    "amv0": _clean(last["amv0"]),
                    "cyc13": _clean(last["cyc13"]),
                    "cys0": cys0,
                    "zone": zone_of(cys0),
                    "above0": bool(last["close"] > last["amv0"]) if last["amv0"] else None,
                    "alpha_pct": _clean(alpha_mean * 100) if alpha_mean else None,
                    "half_life": _clean(0.6931 / alpha_mean) if alpha_mean else None,
                    "buy_signal": int(last.get("buy_signal", 0) or 0),
                    "days_since_signal": int(len(frame) - 1 - fired[-1]) if len(fired) else None,
                }
            )
            series = frame.set_index("date")["close"] > frame.set_index("date")["amv0"]
            breadth_frames.append(series.rename(item.code))

        breadth: list[dict] = []
        if breadth_frames:
            wide = pd.concat(breadth_frames, axis=1).sort_index().tail(250)
            pct = wide.mean(axis=1) * 100
            breadth = [{"date": d, "value": round(float(v), 1)} for d, v in pct.items() if pd.notna(v)]

        # 超跌观察区:当前处于折价/深度折价的标的,按乖离由深到浅
        watch = sorted(
            [r for r in rows if r["cys0"] is not None and r["cys0"] < WATCH_DISCOUNT],
            key=lambda r: r["cys0"],
        )
        return {
            "status": self.status(),
            "rows": rows,
            "breadth": breadth,
            "sectors": self._sector_summary(rows),
            "watch": watch,
            "thresholds": {"deep": DEEP_DISCOUNT, "watch": WATCH_DISCOUNT},
        }

    @staticmethod
    def _sector_summary(rows: list[dict]) -> list[dict]:
        if not rows:
            return []
        frame = pd.DataFrame(rows)
        grouped = frame.groupby("sector", sort=False).agg(
            count=("code", "size"), cys0=("cys0", "mean"), above=("above0", "mean")
        )
        return [
            {
                "sector": sector,
                "count": int(row["count"]),
                "cys0": round(float(row["cys0"]), 2) if pd.notna(row["cys0"]) else None,
                "above_pct": round(float(row["above"]) * 100, 1) if pd.notna(row["above"]) else None,
            }
            for sector, row in grouped.iterrows()
        ]

    def get_series(self, code: str, days: int = 250) -> dict:
        """单只标的的 K 线 + 成本均线序列。"""

        payload = self.read_series(code)
        if not payload:
            raise KeyError(code)
        rows = payload["rows"][-max(days, 30) :]
        return {
            "code": payload["code"],
            "name": payload["name"],
            "sector": payload["sector"],
            "session_date": payload.get("session_date"),
            "dates": [r["date"] for r in rows],
            # ECharts candlestick 顺序: [open, close, low, high]
            "ohlc": [[r["open"], r["close"], r["low"], r["high"]] for r in rows],
            "volume": [r["volume"] for r in rows],
            "amv0": [r["amv0"] for r in rows],
            "cyc5": [r["cyc5"] for r in rows],
            "cyc13": [r["cyc13"] for r in rows],
            "cyc34": [r["cyc34"] for r in rows],
            "cys0": [r["cys0"] for r in rows],
            # 买点标记:[索引, 当日最低价] —— 画在 K 线下方
            "buy_marks": [
                [i, r["low"]] for i, r in enumerate(rows) if r.get("buy_signal")
            ],
            "align_marks": [
                [i, r["high"]] for i, r in enumerate(rows) if r.get("align_signal")
            ],
        }
