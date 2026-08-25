import pandas as pd
import pytest

from quant.config import Config
from quant.web.service import (
    DashboardService,
    _band_index_check,
    _no_new_low_streak,
    _temp_level,
)


def _cfg():
    return Config(watchlist=[{"code": "000001", "name": "平安银行"}])


def _daily(closes, lows=None, highs=None, start="2026-01-05"):
    n = len(closes)
    return pd.DataFrame(
        {
            "datetime": pd.date_range(start, periods=n, freq="D"),
            "open": closes,
            "high": highs if highs is not None else [c * 1.02 for c in closes],
            "low": lows if lows is not None else [c * 0.98 for c in closes],
            "close": closes,
            "volume": [1000] * n,
        }
    )


class BaseFakeSource:
    def __init__(self, daily_bars):
        self.daily_bars = daily_bars
        self.daily_calls = 0

    def get_minute_bars(self, code, period="1"):
        return self.daily_bars

    def get_daily_bars(self, code, start, end):
        self.daily_calls += 1
        return self.daily_bars

    def get_all_code_name(self):
        return pd.DataFrame([{"code": "000001", "name": "平安银行"}])


class BandFakeSource(BaseFakeSource):
    def __init__(self, daily_bars, activity=None, index_bars=None):
        super().__init__(daily_bars)
        self.activity = activity
        self.index_bars = index_bars
        self.activity_calls = 0
        self.index_calls = 0

    def get_market_activity(self):
        self.activity_calls += 1
        return self.activity

    def get_index_daily(self, symbol="sh000001", start="", end=""):
        self.index_calls += 1
        return self.index_bars


# ---- 温度分级 ----


def test_temp_level_boundaries():
    assert _temp_level(-5)["level"] == "冰点"
    assert _temp_level(0)["level"] == "不达标"
    assert _temp_level(64.9)["level"] == "不达标"
    assert _temp_level(65)["level"] == "及格"
    assert _temp_level(80)["level"] == "强势"
    assert _temp_level(130)["level"] == "较佳"
    assert _temp_level(150)["level"] == "较佳"
    assert _temp_level(151)["level"] == "冲顶"


# ---- 不创新低 ----


def test_no_new_low_streak():
    # 10,9,8,9,9,9:最后三根未跌破此前低点
    assert _no_new_low_streak([10, 9, 8, 9, 9, 9]) == 3
    # 最后一根创新低
    assert _no_new_low_streak([10, 9, 8, 7]) == 0
    # 一直上涨
    assert _no_new_low_streak([1, 2, 3, 4]) == 3


def test_band_index_check_stable_bottom():
    closes = [3000] * 30
    lows = [3000] * 30
    highs = [3100] * 30
    r = _band_index_check(_daily(closes, lows, highs))
    assert r["no_new_low_streak"] >= 3
    assert r["stable"] is True
    assert r["made_new_low"] is False
    assert r["position"] == "底部区域"


def test_band_index_check_new_low():
    closes = [3000 - i * 20 for i in range(12)]
    lows = [2990 - i * 20 for i in range(12)]
    r = _band_index_check(_daily(closes, lows))
    assert r["made_new_low"] is True
    assert r["no_new_low_streak"] == 0
    assert r["stable"] is False


# ---- 市场温度接口 ----


def test_get_band_market_temperature_and_cache():
    src = BandFakeSource(
        daily_bars=_daily([10] * 30),
        activity={"up": 700, "down": 300, "flat": 50, "limit_up": 20, "limit_down": 2, "stat_time": "2026-08-25 15:00"},
        index_bars=_daily([3000] * 30, [3000] * 30, [3100] * 30),
    )
    svc = DashboardService(src, _cfg())
    r = svc.get_band_market()
    t = r["temperature"]
    assert t["value"] == pytest.approx(133.3, abs=0.1)
    assert t["level"] == "较佳"
    assert t["up"] == 700 and t["down"] == 300
    assert r["index"]["stable"] is True
    svc.get_band_market()
    assert src.activity_calls == 1  # 5分钟缓存,不重复拉取
    assert src.index_calls == 1


def test_get_band_market_degrades_gracefully():
    src = BaseFakeSource(_daily([10] * 30))
    svc = DashboardService(src, _cfg())
    r = svc.get_band_market()
    assert r == {"temperature": None, "index": None}


def test_get_band_market_survives_source_error():
    class ErrSource(BandFakeSource):
        def get_market_activity(self):
            raise RuntimeError("限流")

        def get_index_daily(self, symbol="sh000001", start="", end=""):
            raise RuntimeError("限流")

    src = ErrSource(_daily([10] * 30))
    svc = DashboardService(src, _cfg())
    r = svc.get_band_market()
    assert r == {"temperature": None, "index": None}


# ---- 个股体检 ----


def test_band_stock_low_align_entry():
    # 超跌40%+ 震荡3日不创新低 + 现价贴近5日最低
    closes = [10] * 30 + [6.2, 5.9, 5.6, 5.6, 5.6, 5.7]
    lows = [c - 0.05 for c in closes]
    svc = DashboardService(BandFakeSource(_daily(closes, lows)), _cfg())
    r = svc.get_band_stock("000001")
    checks = {c["key"]: c for c in r["checks"]}
    assert checks["left_align"]["ok"] is True
    assert checks["drawdown"]["ok"] is True
    assert checks["no_new_low"]["ok"] is True
    assert r["verdict"]["title"] == "向左看齐·进场区"
    assert r["verdict"]["tone"] == "good"


def test_band_stock_new_low_reject():
    # 单边下跌(跌幅未达超跌线),今日创新低
    closes = [10 - i * 0.1 for i in range(20)]
    svc = DashboardService(BandFakeSource(_daily(closes)), _cfg())
    r = svc.get_band_stock("000001")
    assert r["verdict"]["title"] == "今日创新低"
    assert r["verdict"]["tone"] == "bad"


def test_band_stock_first_pullback_ma10():
    # 上升趋势中第一次回踩10日线:连涨后回落,盘中触及均线,收盘收回上方
    closes = [10 + i * 0.3 for i in range(18)] + [15.0, 14.5]
    lows = [c - 0.1 for c in closes]
    lows[-1] = 14.1  # 盘中回踩到10日线附近
    svc = DashboardService(BandFakeSource(_daily(closes, lows)), _cfg())
    r = svc.get_band_stock("000001")
    checks = {c["key"]: c for c in r["checks"]}
    assert checks["ma10"]["ok"] is True
    assert r["verdict"]["title"] == "回踩10日线·进场信号"


def test_band_stock_short_data():
    svc = DashboardService(BandFakeSource(_daily([10] * 10)), _cfg())
    r = svc.get_band_stock("000001")
    assert r["checks"] == []
    assert r["verdict"]["title"] == "数据不足"
