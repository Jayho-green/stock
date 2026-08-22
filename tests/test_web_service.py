from datetime import datetime

import pandas as pd

from quant.config import Config
from quant.web.service import DashboardService, TTLCache, _change_pct


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FixedDateTimeClock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self):
        return self.value


def _bars(closes, vols=None, start="2024-01-02 09:30"):
    n = len(closes)
    return pd.DataFrame(
        {
            "datetime": pd.date_range(start, periods=n, freq="min"),
            "open": closes,
            "high": [c + 0.1 for c in closes],
            "low": [c - 0.1 for c in closes],
            "close": closes,
            "volume": vols if vols else [100] * n,
        }
    )


class FakeSource:
    def __init__(self, bars, daily_bars=None):
        self.bars = bars
        self.daily_bars = daily_bars if daily_bars is not None else bars
        self.calls = 0
        self.daily_calls = 0

    def get_minute_bars(self, code, period="1"):
        self.calls += 1
        return self.bars

    def get_daily_bars(self, code, start, end):
        self.daily_calls += 1
        return self.daily_bars

    def get_all_code_name(self):
        return pd.DataFrame([{"code": "000001", "name": "平安银行"}, {"code": "600000", "name": "浦发银行"}])


def _cfg():
    return Config(watchlist=[{"code": "000001", "name": "平安银行"}], rules={"rsi_period": 14})


def test_ttl_cache_expiry():
    clk = FakeClock()
    c = TTLCache(ttl=30, now=clk)
    c.set("k", 1)
    clk.t = 20
    assert c.get("k") == 1
    clk.t = 31
    assert c.get("k") is None


def test_change_pct_vs_prev_day():
    # 前一日收盘 10,当日最新 11 -> +10%
    day1 = _bars([10, 10, 10], start="2024-01-02 14:57")
    day2 = _bars([10.5, 11], start="2024-01-03 09:30")
    bars = pd.concat([day1, day2], ignore_index=True)
    assert round(_change_pct(bars), 2) == 10.0


def test_get_quotes_fields():
    bars = _bars(list(range(1, 40)))
    svc = DashboardService(FakeSource(bars), _cfg())
    q = svc.get_quotes()
    assert len(q) == 1
    row = q[0]
    assert row["code"] == "000001"
    assert "price" in row and "change_pct" in row and "rsi" in row
    assert isinstance(row["signals"], list)


def test_get_kline_arrays_aligned():
    bars = _bars(list(range(1, 30)))
    svc = DashboardService(FakeSource(bars), _cfg())
    k = svc.get_kline("000001")
    n = len(bars)
    assert len(k["datetime"]) == n
    assert len(k["ohlc"]) == n
    assert len(k["volume"]) == n
    assert len(k["ma5"]) == n
    assert k["ohlc"][0] == [1.0, 1.0, 0.9, 1.1]  # open,close,low,high
    assert k["period"] == "minute"


def test_get_kline_returns_only_latest_day():
    # 两天数据:K线只返回最近一天,但均线在跨天历史上计算(首根即有值)
    day1 = _bars(list(range(1, 26)), start="2024-01-02 09:30")
    day2 = _bars([30, 31, 32, 33], start="2024-01-03 09:30")
    bars = pd.concat([day1, day2], ignore_index=True)
    svc = DashboardService(FakeSource(bars), _cfg())
    k = svc.get_kline("000001")
    assert len(k["ohlc"]) == 4  # 只剩最近一天的 4 根
    assert all(dt.startswith("2024-01-03") for dt in k["datetime"])
    assert k["ma5"][0] is not None  # 跨天历史 -> 当天首根均线有值


def test_cache_limits_source_calls():
    bars = _bars(list(range(1, 30)))
    src = FakeSource(bars)
    clk = FakeClock()
    svc = DashboardService(src, _cfg(), ttl=30, now=clk)
    svc.get_quotes()
    svc.get_kline("000001")  # 同一 TTL 窗口,复用缓存
    assert src.calls == 1
    clk.t = 31
    svc.get_kline("000001")  # 过期后重新取
    assert src.calls == 2


def test_get_daily_kline_includes_zhixing_lines():
    daily = _bars(list(range(1, 260)), start="2024-01-02 00:00")
    svc = DashboardService(FakeSource(_bars(list(range(1, 30))), daily_bars=daily), _cfg())
    k = svc.get_kline("000001", "daily")
    n = 240
    assert k["period"] == "daily"
    assert len(k["ohlc"]) == n
    assert len(k["zx_short"]) == n
    assert len(k["zx_bull"]) == n
    assert k["zx_short"][-1] is not None
    assert k["zx_bull"][-1] is not None
    assert len(k["kdj_k"]) == n
    assert len(k["kdj_d"]) == n
    assert len(k["kdj_j"]) == n
    assert k["kdj_k"][-1] is not None


def test_backtest_warms_daily_kline_cache():
    daily = _bars(list(range(1, 260)), start="2024-01-02 00:00")
    src = FakeSource(_bars(list(range(1, 30))), daily_bars=daily)
    svc = DashboardService(src, _cfg(), ttl=0)

    svc.run_backtest("000001", "rsi_extreme", days=365, forward=5, cost=0)
    calls_after_backtest = src.daily_calls
    k = svc.get_kline("000001", "daily")

    assert src.daily_calls == calls_after_backtest
    assert len(k["ohlc"]) == 240


def test_backtest_daily_bars_use_post_close_file_cache(tmp_path):
    cache_path = tmp_path / "kline_cache"
    daily = _bars(list(range(1, 260)), start="2024-01-02 00:00")
    src1 = FakeSource(_bars(list(range(1, 30))), daily_bars=daily)
    svc1 = DashboardService(
        src1,
        _cfg(),
        ttl=0,
        kline_cache_path=cache_path,
        clock=FixedDateTimeClock(datetime(2026, 7, 1, 16, 0)),
    )
    svc1.run_backtest("000001", "rsi_extreme", days=365, forward=5, cost=0)

    src2 = FakeSource(_bars(list(range(1, 30))), daily_bars=_bars(list(range(100, 359))))
    svc2 = DashboardService(
        src2,
        _cfg(),
        ttl=0,
        kline_cache_path=cache_path,
        clock=FixedDateTimeClock(datetime(2026, 7, 1, 16, 30)),
    )
    svc2.run_backtest("000001", "rsi_extreme", days=365, forward=5, cost=0)

    assert src2.daily_calls == 0


def test_post_close_daily_kline_uses_file_cache_after_ttl_expiry(tmp_path):
    daily = _bars(list(range(1, 260)), start="2024-01-02 00:00")
    src = FakeSource(_bars(list(range(1, 30))), daily_bars=daily)
    ttl_clock = FakeClock()
    svc = DashboardService(
        src,
        _cfg(),
        ttl=0,
        now=ttl_clock,
        kline_cache_path=tmp_path / "kline_cache",
        clock=FixedDateTimeClock(datetime(2026, 7, 1, 16, 0)),
    )

    first = svc.get_kline("000001", "daily")
    ttl_clock.t = 1
    src.daily_bars = _bars(list(range(100, 359)), start="2024-01-02 00:00")
    second = svc.get_kline("000001", "daily")

    assert src.daily_calls == 1
    assert second["ohlc"] == first["ohlc"]


def test_before_8_next_day_reuses_previous_post_close_cache(tmp_path):
    cache_path = tmp_path / "kline_cache"
    daily = _bars(list(range(1, 260)), start="2024-01-02 00:00")
    src1 = FakeSource(_bars(list(range(1, 30))), daily_bars=daily)
    svc1 = DashboardService(
        src1,
        _cfg(),
        kline_cache_path=cache_path,
        clock=FixedDateTimeClock(datetime(2026, 7, 1, 16, 0)),
    )
    expected = svc1.get_kline("000001", "daily")

    src2 = FakeSource(_bars(list(range(1, 30))), daily_bars=_bars(list(range(100, 359))))
    svc2 = DashboardService(
        src2,
        _cfg(),
        kline_cache_path=cache_path,
        clock=FixedDateTimeClock(datetime(2026, 7, 2, 7, 59)),
    )
    actual = svc2.get_kline("000001", "daily")

    assert src2.daily_calls == 0
    assert actual["ohlc"] == expected["ohlc"]


def test_after_8_bypasses_post_close_file_cache(tmp_path):
    cache_path = tmp_path / "kline_cache"
    src1 = FakeSource(_bars(list(range(1, 30))), daily_bars=_bars(list(range(1, 260))))
    svc1 = DashboardService(
        src1,
        _cfg(),
        kline_cache_path=cache_path,
        clock=FixedDateTimeClock(datetime(2026, 7, 1, 16, 0)),
    )
    cached = svc1.get_kline("000001", "daily")

    src2 = FakeSource(_bars(list(range(1, 30))), daily_bars=_bars(list(range(100, 359))))
    svc2 = DashboardService(
        src2,
        _cfg(),
        kline_cache_path=cache_path,
        clock=FixedDateTimeClock(datetime(2026, 7, 2, 8, 0)),
    )
    fresh = svc2.get_kline("000001", "daily")

    assert src2.daily_calls == 1
    assert fresh["ohlc"] != cached["ohlc"]


def test_post_close_minute_kline_uses_file_cache(tmp_path):
    cache_path = tmp_path / "kline_cache"
    minute = _bars(list(range(1, 40)))
    src1 = FakeSource(minute)
    svc1 = DashboardService(
        src1,
        _cfg(),
        kline_cache_path=cache_path,
        clock=FixedDateTimeClock(datetime(2026, 7, 1, 16, 0)),
    )
    expected = svc1.get_kline("000001")

    src2 = FakeSource(_bars(list(range(100, 139))))
    svc2 = DashboardService(
        src2,
        _cfg(),
        kline_cache_path=cache_path,
        clock=FixedDateTimeClock(datetime(2026, 7, 1, 16, 30)),
    )
    actual = svc2.get_kline("000001")

    assert src2.calls == 0
    assert actual["ohlc"] == expected["ohlc"]


def test_add_watchlist_uses_resolved_name(tmp_path):
    manual = tmp_path / "watchlist.manual.toml"
    svc = DashboardService(FakeSource(_bars(list(range(1, 30)))), _cfg(), manual_path=manual)
    ret = svc.add_watchlist("600000")
    assert ret["added"] is True
    assert ret["item"] == {"code": "600000", "name": "浦发银行"}


def test_get_news_uses_full_market_with_watchlist_markers(monkeypatch):
    captured = {}

    def fake_news(limit=80, today_only=True, stock_universe=None):
        captured["stock_universe"] = stock_universe
        return {"items": [], "sources": [], "errors": []}

    monkeypatch.setattr("quant.news.get_market_news", fake_news)
    src = FakeSource(_bars(list(range(1, 30))))
    cfg = Config(watchlist=[{"code": "000001", "name": "平安银行"}], rules={"rsi_period": 14})
    svc = DashboardService(src, cfg)

    svc.get_news()

    assert captured["stock_universe"] == [
        {"code": "000001", "name": "平安银行", "in_watchlist": True},
        {"code": "000001", "name": "平安银行"},
        {"code": "600000", "name": "浦发银行"},
    ]
    assert src.calls == 0


def test_get_news_writes_and_reuses_disk_cache(tmp_path):
    cache_path = tmp_path / "news_cache.json"

    def news_fetcher(limit=80, today_only=True):
        return {
            "date": "2026-07-14",
            "updated_at": "2026-07-14 10:00:00",
            "today_only": today_only,
            "fallback_latest": False,
            "sources": [{"id": "test", "label": "测试源", "ok": True, "count": 1}],
            "errors": [],
            "items": [{"title": "已缓存资讯", "summary": "摘要", "published_at": "2026-07-14 09:30:00"}],
        }

    svc = DashboardService(
        FakeSource(_bars(list(range(1, 30)))),
        _cfg(),
        news_fetcher=news_fetcher,
        news_cache_path=cache_path,
    )
    data = svc.get_news()
    assert data["items"][0]["title"] == "已缓存资讯"
    assert cache_path.exists()

    def failing_news_fetcher(limit=80, today_only=True):
        raise RuntimeError("news source down")

    cached_svc = DashboardService(
        FakeSource(_bars(list(range(1, 30)))),
        _cfg(),
        news_fetcher=failing_news_fetcher,
        news_cache_path=cache_path,
    )
    cached = cached_svc.get_news()
    assert cached["from_disk_cache"] is True
    assert cached["items"][0]["title"] == "已缓存资讯"


def test_get_news_archives_events_and_adds_impact_grade(tmp_path):
    def news_fetcher(limit=80, today_only=True):
        return {
            "date": datetime.now().date().isoformat(),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "items": [
                {
                    **{
                        "id": "event-1",
                        "title": "平安银行发布重要经营信息",
                        "summary": "经营数据改善",
                        "published_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "source": "测试源",
                        "source_id": "test",
                        "sentiment": "利好",
                        "sentiment_direction": "positive",
                        "sentiment_score": 3,
                        "analysis_source": "glm",
                    },
                    "related_stocks": [
                        {
                            "code": "000001",
                            "name": "平安银行",
                            "score": 110,
                            "confidence": 0.9,
                            "impact_score": 3,
                            "sentiment": "利好",
                            "sentiment_direction": "positive",
                            "reason": "标题提及公司名称",
                        }
                    ],
                }
            ],
            "sources": [],
            "errors": [],
        }

    svc = DashboardService(
        FakeSource(_bars(list(range(1, 30)))),
        _cfg(),
        news_fetcher=news_fetcher,
        news_cache_path=tmp_path / "news-cache.json",
        news_events_path=tmp_path / "news-events.sqlite3",
    )
    data = svc.get_news()

    assert data["items"][0]["impact_level"] in {"A", "B", "C", "D"}
    report = svc.get_news_backtest_report()
    assert report["overview"]["archived_events"] == 1
    assert report["overview"]["directional_relations"] == 1
