"""面板服务层:带 TTL 缓存地从数据源取分钟K,派生行情/K线/信号。

缓存的意义:前端可频繁轮询,但对 akshare 的真实请求按 TTL 限频,避免触发东财限流。
"""

from __future__ import annotations

import math
import json
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from ..backtest import DEFAULT_COST, backtest
from ..config import Config
from ..indicators import add_kdj, add_ma, add_rsi, add_volume_features, add_zhixing
from ..signals.backtest_rules import BACKTEST_ENTRY_TIMING, BACKTEST_ONLY_RULES
from ..signals.engine import run_rules
from ..signals.monitor_rules import MONITOR_RULES
from ..watchlist import add_watchlist_item
from ..kline_cache import PostCloseKlineCache, effective_market_date


class TTLCache:
    def __init__(self, ttl: float, now: Callable[[], float] = time.time):
        self.ttl = ttl
        self._now = now
        self._store: dict[Any, tuple[float, Any]] = {}

    def get(self, key):
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, val = entry
        if self._now() - ts > self.ttl:
            del self._store[key]
            return None
        return val

    def set(self, key, val) -> None:
        self._store[key] = (self._now(), val)


def _clean(series: pd.Series) -> list[float | None]:
    """把 NaN 转成 None,便于 JSON 序列化。"""
    return [None if (v is None or (isinstance(v, float) and math.isnan(v))) else float(v) for v in series]


def _change_pct(bars: pd.DataFrame) -> float:
    """相对上一交易日收盘的涨跌幅(%);只有当日数据时回退到当日开盘。"""
    b = bars.copy()
    b["date"] = b["datetime"].dt.date
    last_date = b["date"].iloc[-1]
    prior = b[b["date"] < last_date]
    base = prior["close"].iloc[-1] if len(prior) else b["open"].iloc[0]
    last = b["close"].iloc[-1]
    return float((last - base) / base * 100) if base else 0.0


RULE_LABELS = {
    "ma_cross": "均线交叉",
    "macd_cross": "MACD交叉",
    "rsi_extreme": "RSI极值",
    "volume_spike": "放量异动",
    "break_intraday_high_low": "突破高低点",
    "zhixing_pick_close": "知行多空方案",
}

GLOBAL_QUOTE_TARGETS = [
    {
        "id": "sk_hynix",
        "secid": "177.000660",
        "name": "SK海力士",
        "company": "SK hynix Inc.",
        "market": "KRX",
        "currency": "KRW",
        "accent": "teal",
    },
    {
        "id": "samsung",
        "secid": "177.005930",
        "name": "三星电子",
        "company": "Samsung Electronics Co., Ltd.",
        "market": "KRX",
        "currency": "KRW",
        "accent": "violet",
    },
]


class DashboardService:
    def __init__(
        self,
        source,
        cfg: Config,
        ttl: float = 30,
        now: Callable[[], float] = time.time,
        generated_path=None,
        manual_path=None,
        news_fetcher: Callable[..., dict] | None = None,
        kline_cache_path=None,
        clock: Callable[[], datetime] = datetime.now,
        global_archive_path=None,
        news_cache_path=None,
        news_events_path=None,
    ):
        self.source = source
        self.cfg = cfg
        self.generated_path = generated_path
        self.manual_path = manual_path
        self.news_fetcher = news_fetcher
        self.clock = clock
        self.global_archive_path = Path(global_archive_path) if global_archive_path is not None else None
        self.news_cache_path = Path(news_cache_path) if news_cache_path is not None else None
        self.news_event_store = None
        self.news_backtest_runner = None
        if news_events_path is not None:
            from ..news_backtest import NewsBacktestRunner, NewsEventStore

            self.news_event_store = NewsEventStore(news_events_path)
            self.news_backtest_runner = NewsBacktestRunner(self.news_event_store, self._minute_bars)
        self.kline_cache = (
            PostCloseKlineCache(kline_cache_path, clock=clock) if kline_cache_path is not None else None
        )
        self._cache = TTLCache(ttl, now)
        self._global_cache = TTLCache(7, now)
        self._long_cache = TTLCache(6 * 3600, now)
        if self.news_event_store is not None:
            cached_news = self._read_news_cache()
            if cached_news and cached_news.get("items"):
                self.news_event_store.apply_impact_levels(cached_news["items"])
                self.news_event_store.archive_payload(cached_news)

    def _active_watchlist(self) -> list[dict]:
        """实时名单:有 generated_path 时合并手填+选股名单,否则用 cfg 名单。"""
        if self.generated_path is not None or self.manual_path is not None:
            from ..watchlist import load_active_watchlist

            generated_path = self.generated_path or "__missing_watchlist_generated__.toml"
            return load_active_watchlist(self.cfg.watchlist, generated_path, self.manual_path)
        return self.cfg.watchlist

    def get_watchlist(self) -> list[dict]:
        """轻量返回自选名单,不拉行情。"""
        return self._active_watchlist()

    def search_stocks(self, q: str, limit: int = 10) -> list[dict]:
        """按代码前缀或名称子串搜索全A股票,返回 [{code,name}],表缓存6小时。"""
        query = (q or "").strip()
        if not query or not hasattr(self.source, "get_all_code_name"):
            return []
        stock_universe = self._long_cache.get("stock_universe")
        if stock_universe is None:
            try:
                stock_universe = self.source.get_all_code_name().to_dict("records")
            except Exception:
                stock_universe = []
            self._long_cache.set("stock_universe", stock_universe)
        qlower = query.lower()
        code_hits: list[dict] = []
        name_hits: list[dict] = []
        for row in stock_universe:
            code = str(row.get("code", "")).zfill(6)
            name = str(row.get("name", ""))
            if code.startswith(query):
                code_hits.append({"code": code, "name": name})
            elif qlower in name.lower():
                name_hits.append({"code": code, "name": name})
            if len(code_hits) >= limit and len(name_hits) >= limit:
                break
        return (code_hits + name_hits)[:limit]

    def _minute_bars(self, code: str) -> pd.DataFrame:
        key = ("minute_bars", code)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if self.kline_cache is not None:
            bars = self.kline_cache.get_or_fetch("minute", code, lambda: self.source.get_minute_bars(code))
        else:
            bars = self.source.get_minute_bars(code)
        self._cache.set(key, bars)
        return bars

    def _daily_bars(self, code: str) -> pd.DataFrame:
        key = ("daily_bars", code)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        warmed = self._long_cache.get(("daily_bars_chart", str(code).zfill(6)))
        if warmed is not None:
            self._cache.set(key, warmed)
            return warmed
        end = effective_market_date(self.clock)
        start = end - timedelta(days=540)
        fetch = lambda: self.source.get_daily_bars(code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        if self.kline_cache is not None:
            bars = self.kline_cache.get_or_fetch("daily", code, fetch)
        else:
            bars = fetch()
        self._cache.set(key, bars)
        self._remember_chart_daily_bars(code, bars)
        return bars

    def _daily_bars_for_days(self, code: str, days: int) -> pd.DataFrame:
        key = ("daily_bars", code, days)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        end = effective_market_date(self.clock)
        start = end - timedelta(days=days)
        start_s = start.strftime("%Y%m%d")
        end_s = end.strftime("%Y%m%d")
        fetch = lambda: self.source.get_daily_bars(code, start_s, end_s)
        if self.kline_cache is not None:
            bars = self.kline_cache.get_or_fetch(f"backtest_daily_{start_s}_{end_s}", code, fetch)
        else:
            bars = fetch()
        self._cache.set(key, bars)
        self._remember_chart_daily_bars(code, bars)
        return bars

    def _remember_chart_daily_bars(self, code: str, bars: pd.DataFrame) -> None:
        if bars is None or len(bars) == 0:
            return
        self._long_cache.set(("daily_bars_chart", str(code).zfill(6)), bars.copy())

    def _stock_name(self, code: str) -> str:
        for item in self._active_watchlist():
            if str(item["code"]).zfill(6) == str(code).zfill(6):
                return item.get("name") or str(code).zfill(6)
        if hasattr(self.source, "get_all_code_name"):
            try:
                names = self.source.get_all_code_name()
                match = names[names["code"].astype(str).str.zfill(6) == str(code).zfill(6)]
                if len(match):
                    return str(match.iloc[0]["name"])
            except Exception:
                pass
        return str(code).zfill(6)

    def get_quotes(self) -> list[dict]:
        out: list[dict] = []
        for item in self._active_watchlist():
            code = item["code"]
            name = item.get("name", code)
            try:
                bars = self._minute_bars(code)
            except Exception:
                out.append({"code": code, "name": name, "error": True})
                continue
            vr = add_volume_features(bars, self.cfg.rules.get("vol_window", 5))["vol_ratio"].iloc[-1]
            rsi = add_rsi(bars, self.cfg.rules.get("rsi_period", 14))["rsi"].iloc[-1]
            sigs = run_rules(bars, MONITOR_RULES, code, name, self.cfg.rules)
            out.append(
                {
                    "code": code,
                    "name": name,
                    "price": float(bars["close"].iloc[-1]),
                    "change_pct": round(_change_pct(bars), 2),
                    "vol_ratio": None if math.isnan(vr) else round(float(vr), 2),
                    "rsi": None if math.isnan(rsi) else round(float(rsi), 1),
                    "signals": [s.rule for s in sigs],
                }
            )
        return out

    def get_global_quotes(self) -> dict:
        key = ("global_quotes", tuple((t["id"], t["secid"]) for t in GLOBAL_QUOTE_TARGETS))
        cached = self._global_cache.get(key)
        if cached is not None:
            return cached
        if not hasattr(self.source, "get_global_quotes"):
            data = {
                "updated_at": self.clock().strftime("%Y-%m-%d %H:%M:%S"),
                "quotes": [
                    {**target, "price": None, "change_pct": None, "change": None, "error": True}
                    for target in GLOBAL_QUOTE_TARGETS
                ],
            }
            self._global_cache.set(key, data)
            return data
        try:
            rows = self.source.get_global_quotes(GLOBAL_QUOTE_TARGETS)
        except Exception:
            rows = []
        by_id = {str(row.get("id")): row for row in rows if row.get("id")}
        fallback_trends = None
        quotes = []
        for target in GLOBAL_QUOTE_TARGETS:
            row = by_id.get(target["id"])
            if row:
                quotes.append({**target, **row, "error": False})
            else:
                if fallback_trends is None and hasattr(self.source, "get_global_intraday"):
                    try:
                        fallback_trends = self.source.get_global_intraday(GLOBAL_QUOTE_TARGETS)
                    except Exception:
                        fallback_trends = {}
                trend = (fallback_trends or {}).get(target["id"]) or {}
                prev = trend.get("prev_close")
                price = trend.get("price")
                quotes.append(
                    {
                        **target,
                        "code": trend.get("code") or str(target["secid"]).split(".")[-1],
                        "price": price,
                        "change_pct": round((price - prev) / prev * 100, 2) if price and prev else None,
                        "change": price - prev if price and prev else None,
                        "prev_close": prev,
                        "day_high": trend.get("day_high"),
                        "day_low": trend.get("day_low"),
                        "open": trend.get("open"),
                        "volume": trend.get("volume"),
                        "source_time": None,
                        "error": not bool(price),
                    }
                )
        data = {"updated_at": self.clock().strftime("%Y-%m-%d %H:%M:%S"), "quotes": quotes}
        self._global_cache.set(key, data)
        return data

    def get_global_semiconductor_watch(self) -> dict:
        """韩国半导体双股看盘页 payload:快照、日内曲线、收盘归档。"""
        key = "global_semiconductor_watch"
        cached = self._global_cache.get(key)
        if cached is not None:
            return cached
        quote_payload = self.get_global_quotes()
        quotes = quote_payload.get("quotes") or []
        trends = {}
        if hasattr(self.source, "get_global_intraday"):
            try:
                trends = self.source.get_global_intraday(GLOBAL_QUOTE_TARGETS)
            except Exception:
                trends = {}
        for quote in quotes:
            trend = trends.get(quote["id"])
            if not trend or not trend.get("points"):
                trend = self._fallback_global_trend(quote)
            self._complete_global_quote_from_trend(quote, trend)
            quote["trend"] = trend
        market = self._korea_market_state()
        if not market["is_open"]:
            self._archive_global_close(quotes, market["date"])
        data = {
            "updated_at": quote_payload.get("updated_at") or self.clock().strftime("%Y-%m-%d %H:%M:%S"),
            "market": market,
            "quotes": quotes,
            "archive": self._read_global_archive(limit=8) or self._archive_fallback(quotes, market["date"]),
        }
        self._global_cache.set(key, data)
        return data

    def _complete_global_quote_from_trend(self, quote: dict, trend: dict) -> None:
        price = quote.get("price") or trend.get("price")
        prev = quote.get("prev_close") or trend.get("prev_close")
        if quote.get("price") is None and price is not None:
            quote["price"] = price
        if quote.get("prev_close") is None and prev is not None:
            quote["prev_close"] = prev
        if quote.get("change") is None and price is not None and prev:
            quote["change"] = price - prev
        if quote.get("change_pct") is None and price is not None and prev:
            quote["change_pct"] = round((price - prev) / prev * 100, 2)
        for key in ("open", "day_high", "day_low", "volume", "code"):
            if quote.get(key) is None and trend.get(key) is not None:
                quote[key] = trend.get(key)
        quote["error"] = not bool(quote.get("price"))

    def _korea_market_state(self) -> dict:
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        open_at = now.replace(hour=9, minute=0, second=0, microsecond=0)
        close_at = now.replace(hour=15, minute=30, second=0, microsecond=0)
        is_open = now.weekday() < 5 and open_at <= now <= close_at
        return {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "is_open": is_open,
            "label": "市场交易中 · 约 8 秒刷新" if is_open else "市场已收盘 · 收盘价已监控",
        }

    def _fallback_global_trend(self, quote: dict) -> dict:
        prev = quote.get("prev_close")
        price = quote.get("price")
        points = []
        if prev and price:
            for i, value in enumerate((prev, price)):
                points.append(
                    {
                        "time": f"参考点 {i + 1}",
                        "price": float(value),
                        "pct": round((float(value) - float(prev)) / float(prev) * 100, 4),
                    }
                )
        return {
            "id": quote.get("id"),
            "name": quote.get("name"),
            "prev_close": prev,
            "points": points,
            "synthetic": True,
        }

    def _read_global_archive(self, limit: int = 8) -> list[dict]:
        path = self.global_archive_path
        if path is None or not path.exists():
            return []
        rows = []
        try:
            for line in path.read_text("utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    row.setdefault("archived", True)
                    rows.append(row)
        except Exception:
            return []
        return list(reversed(rows))[:limit]

    def _archive_global_close(self, quotes: list[dict], archive_date: str) -> None:
        path = self.global_archive_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = {(row.get("date"), row.get("id")) for row in self._read_global_archive(limit=1000)}
        rows = []
        for quote in quotes:
            if not quote.get("price") or (archive_date, quote.get("id")) in existing:
                continue
            rows.append(
                {
                    "date": archive_date,
                    "id": quote.get("id"),
                    "name": quote.get("name"),
                    "price": quote.get("price"),
                    "change_pct": quote.get("change_pct"),
                    "currency": quote.get("currency") or "KRW",
                    "source_time": quote.get("source_time"),
                    "archived": True,
                    "archived_at": self.clock().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        if not rows:
            return
        with path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _archive_fallback(self, quotes: list[dict], archive_date: str) -> list[dict]:
        return [
            {
                "date": archive_date,
                "id": q.get("id"),
                "name": q.get("name"),
                "price": q.get("price"),
                "change_pct": q.get("change_pct"),
                "currency": q.get("currency") or "KRW",
                "source_time": q.get("source_time"),
                "archived": False,
            }
            for q in quotes
            if q.get("price")
        ]

    def _format_kline(self, code: str, b: pd.DataFrame, period: str) -> dict:
        # ECharts candlestick 顺序:[open, close, low, high]
        ohlc = [[float(r.open), float(r.close), float(r.low), float(r.high)] for r in b.itertuples()]
        out = {
            "code": code,
            "period": period,
            "datetime": [str(x) for x in b["datetime"]],
            "ohlc": ohlc,
            "volume": _clean(b["volume"]),
            "ma5": _clean(b["ma5"]),
            "ma20": _clean(b["ma20"]),
            "rsi": _clean(b["rsi"]),
        }
        if "zx_short" in b.columns:
            out["zx_short"] = _clean(b["zx_short"])
        if "zx_bull" in b.columns:
            out["zx_bull"] = _clean(b["zx_bull"])
        if {"k", "d", "j"} <= set(b.columns):
            out["kdj_k"] = _clean(b["k"])
            out["kdj_d"] = _clean(b["d"])
            out["kdj_j"] = _clean(b["j"])
        return out

    def get_kline(self, code: str, period: str = "minute") -> dict:
        if period == "daily":
            bars = self._daily_bars(code)
            b = add_ma(bars, (5, 20))
            b = add_rsi(b, self.cfg.rules.get("rsi_period", 14))
            b = add_zhixing(b)
            b = add_kdj(
                b,
                self.cfg.screen.get("kdj_n", 9),
                self.cfg.screen.get("kdj_k", 3),
                self.cfg.screen.get("kdj_d", 3),
            )
            b = b.tail(240).reset_index(drop=True)
            return self._format_kline(code, b, period)

        bars = self._minute_bars(code)
        # 指标在全历史上计算(保证均线连续),只展示最近一个交易日的分钟K
        b = add_ma(bars, (5, 20))
        b = add_rsi(b, self.cfg.rules.get("rsi_period", 14))
        last_day = b["datetime"].dt.date.max()
        b = b[b["datetime"].dt.date == last_day].reset_index(drop=True)
        return self._format_kline(code, b, "minute")

    def get_backtest_rules(self) -> list[dict]:
        return [
            {"id": rule.__name__, "label": RULE_LABELS.get(rule.__name__, rule.__name__)}
            for rule in [*MONITOR_RULES, *BACKTEST_ONLY_RULES]
        ]

    def get_news(self, limit: int = 80, today_only: bool = True) -> dict:
        key = ("market_news", max(1, min(int(limit), 120)), bool(today_only))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            if self.news_fetcher is not None:
                data = self.news_fetcher(limit=key[1], today_only=key[2])
            else:
                from ..news import get_market_news

                watchlist_universe = [
                    {**item, "in_watchlist": True}
                    for item in self._active_watchlist()
                    if item.get("code") and item.get("name")
                ]
                stock_universe = self._long_cache.get("stock_universe")
                if stock_universe is None and hasattr(self.source, "get_all_code_name"):
                    try:
                        stock_universe = self.source.get_all_code_name().to_dict("records")
                        self._long_cache.set("stock_universe", stock_universe)
                    except Exception:
                        stock_universe = None
                if stock_universe:
                    stock_universe = watchlist_universe + list(stock_universe)
                else:
                    stock_universe = watchlist_universe
                data = get_market_news(limit=max(key[1], 400), today_only=key[2], stock_universe=stock_universe)
        except Exception:
            cached_news = self.get_news_cache(limit=key[1])
            if cached_news.get("items"):
                return cached_news
            raise
        if data.get("items"):
            data = dict(data)
            data["items"] = list(data.get("items") or [])
            if self.news_event_store is not None:
                self.news_event_store.apply_impact_levels(data["items"])
                self.news_event_store.archive_payload(data)
            data["items"] = data["items"][: key[1]]
            self._write_news_cache(data)
        elif cached_news := self.get_news_cache(limit=key[1]):
            if cached_news.get("items"):
                data = cached_news
        self._cache.set(key, data)
        return data

    def get_news_cache(self, limit: int = 80) -> dict:
        data = self._read_news_cache()
        if not data:
            return {
                "date": self.clock().date().isoformat(),
                "updated_at": None,
                "today_only": True,
                "fallback_latest": False,
                "items": [],
                "sources": [],
                "errors": [],
                "from_disk_cache": True,
            }
        data = dict(data)
        data["items"] = list(data.get("items") or [])[: max(1, min(int(limit), 120))]
        if self.news_event_store is not None:
            self.news_event_store.apply_impact_levels(data["items"])
        data["from_disk_cache"] = True
        return data

    def get_news_backtest_report(self, days: int = 30) -> dict:
        if self.news_event_store is None:
            return {
                "generated_at": self.clock().strftime("%Y-%m-%d %H:%M:%S"),
                "days": days,
                "overview": {
                    "archived_events": 0,
                    "archived_publications": 0,
                    "duplicate_publications": 0,
                    "directional_relations": 0,
                    "evaluated_samples": 0,
                    "pending_samples": 0,
                    "hit_rate": None,
                    "avg_signed_return_10m": None,
                    "median_abs_return_10m": None,
                },
                "horizons": [],
                "levels": [],
                "directions": [],
                "weights": [],
                "recent": [],
            }
        return self.news_event_store.report(days)

    def start_news_backtest(self, days: int = 30, limit: int = 500) -> dict:
        if self.news_backtest_runner is None:
            return {"started": False, "running": False, "error": "未配置资讯事件库"}
        return self.news_backtest_runner.start(days=days, limit=limit)

    def get_news_backtest_status(self) -> dict:
        if self.news_backtest_runner is None:
            return {"running": False, "error": "未配置资讯事件库", "report": self.get_news_backtest_report()}
        return self.news_backtest_runner.status()

    def export_news_backtest_dataset(self, days: int = 120) -> Path | None:
        if self.news_event_store is None:
            return None
        return self.news_event_store.write_export(days=days)

    def _read_news_cache(self) -> dict | None:
        path = self.news_cache_path
        if path is None or not path.exists():
            return None
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _write_news_cache(self, data: dict) -> None:
        path = self.news_cache_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(data)
        payload["cached_at"] = self.clock().strftime("%Y-%m-%d %H:%M:%S")
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)

    def run_backtest(
        self,
        code: str,
        rule_name: str,
        days: int = 365,
        forward: int = 5,
        cost: float = DEFAULT_COST,
        name: str | None = None,
    ) -> dict:
        rules = {rule.__name__: rule for rule in [*MONITOR_RULES, *BACKTEST_ONLY_RULES]}
        if rule_name not in rules:
            raise ValueError(f"未知回测规则: {rule_name}")
        bars = self._daily_bars_for_days(str(code).zfill(6), days)
        entry_timing = BACKTEST_ENTRY_TIMING.get(rule_name, "next_open")
        stats = backtest(
            bars,
            rules[rule_name],
            self.cfg.rules,
            forward=forward,
            cost=cost,
            entry_timing=entry_timing,
        )
        entry_text = "信号日收盘价" if entry_timing == "signal_close" else "下一交易日开盘价"
        signal_text = "日线收盘后确认信号"
        if entry_timing == "signal_close":
            signal_text = "日线收盘时满足知行多空方案"
        return {
            "code": str(code).zfill(6),
            "name": name or self._stock_name(str(code).zfill(6)),
            "rule": rule_name,
            "rule_label": RULE_LABELS.get(rule_name, rule_name),
            "days": days,
            "forward": forward,
            "cost": cost,
            "execution": {
                "signal": signal_text,
                "entry": entry_text,
                "exit": f"入场后第 {forward} 个交易日收盘价",
                "allow_overlap": False,
            },
            "bars": len(bars),
            "stats": {
                "trades": stats.trades,
                "win_rate": stats.win_rate,
                "avg_return": stats.avg_return,
                "total_return": stats.total_return,
                "max_drawdown": stats.max_drawdown,
                "best_return": stats.best_return,
                "worst_return": stats.worst_return,
                "profit_factor": stats.profit_factor,
            },
            "trades": [t.to_record() for t in stats.trade_records[-30:]],
            "equity_curve": stats.equity_curve,
        }

    def run_backtest_batch(
        self,
        stocks: list[dict | str],
        rule_name: str,
        days: int = 365,
        forward: int = 5,
        cost: float = DEFAULT_COST,
    ) -> dict:
        rules = {rule.__name__: rule for rule in [*MONITOR_RULES, *BACKTEST_ONLY_RULES]}
        if rule_name not in rules:
            raise ValueError(f"未知回测规则: {rule_name}")

        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in stocks:
            if isinstance(item, str):
                raw_code = item
                raw_name = item
            elif isinstance(item, dict):
                raw_code = item.get("code", "")
                raw_name = item.get("name") or raw_code
            else:
                continue
            code = str(raw_code).strip().zfill(6)
            if not (code.isdigit() and len(code) == 6) or code in seen:
                continue
            seen.add(code)
            normalized.append({"code": code, "name": str(raw_name or code)})
        if not normalized:
            raise ValueError("批量回测至少需要 1 只股票")
        if len(normalized) > 100:
            raise ValueError("批量回测最多支持 100 只股票")

        results: list[dict] = []
        errors: list[dict] = []
        for item in normalized:
            try:
                ret = self.run_backtest(
                    item["code"],
                    rule_name,
                    days=days,
                    forward=forward,
                    cost=cost,
                    name=item["name"],
                )
                results.append(
                    {
                        "code": ret["code"],
                        "name": ret["name"],
                        "stats": ret["stats"],
                        "bars": ret["bars"],
                        "trades": ret["trades"],
                    }
                )
            except Exception as e:
                errors.append({"code": item["code"], "name": item["name"], "error": str(e)})

        results.sort(key=lambda row: row["stats"].get("total_return", 0), reverse=True)
        total_trades = sum(int(row["stats"].get("trades", 0)) for row in results)
        weighted_win = sum(row["stats"].get("win_rate", 0) * row["stats"].get("trades", 0) for row in results)
        weighted_avg = sum(row["stats"].get("avg_return", 0) * row["stats"].get("trades", 0) for row in results)
        avg_total = sum(row["stats"].get("total_return", 0) for row in results) / len(results) if results else 0.0
        max_drawdown = min((row["stats"].get("max_drawdown", 0) for row in results), default=0.0)
        entry_timing = BACKTEST_ENTRY_TIMING.get(rule_name, "next_open")
        return {
            "mode": "batch",
            "rule": rule_name,
            "rule_label": RULE_LABELS.get(rule_name, rule_name),
            "days": days,
            "forward": forward,
            "cost": cost,
            "execution": {
                "signal": "日线收盘时满足知行多空方案" if entry_timing == "signal_close" else "日线收盘后确认信号",
                "entry": "信号日收盘价" if entry_timing == "signal_close" else "下一交易日开盘价",
                "exit": f"入场后第 {forward} 个交易日收盘价",
                "allow_overlap": False,
            },
            "summary": {
                "stocks": len(normalized),
                "ok": len(results),
                "failed": len(errors),
                "trades": total_trades,
                "win_rate": weighted_win / total_trades if total_trades else 0.0,
                "avg_return": weighted_avg / total_trades if total_trades else 0.0,
                "avg_total_return": avg_total,
                "max_drawdown": max_drawdown,
            },
            "results": results,
            "errors": errors,
        }

    def add_watchlist(self, code: str, name: str | None = None) -> dict:
        if self.manual_path is None:
            raise ValueError("未配置手动自选股文件")
        final_name = (name or "").strip()
        if not final_name and hasattr(self.source, "get_all_code_name"):
            try:
                names = self.source.get_all_code_name()
                match = names[names["code"].astype(str).str.zfill(6) == str(code).zfill(6)]
                if len(match):
                    final_name = str(match.iloc[0]["name"])
            except Exception:
                final_name = ""
        item, added = add_watchlist_item(self.manual_path, code, final_name or None)
        return {"ok": True, "added": added, "item": item}
