import pandas as pd

from quant.signals.monitor_rules import (
    ma_cross,
    rsi_extreme,
    volume_spike,
    break_intraday_high_low,
)


def _bars(opens, highs, lows, closes, vols):
    n = len(closes)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01 09:30", periods=n, freq="min"),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": vols,
        }
    )


def _flat(closes, vols=None):
    n = len(closes)
    return _bars(closes, closes, closes, closes, vols if vols else [100] * n)


def test_ma_cross_golden():
    # 下跌后急涨,短均线(2)上穿长均线(4)
    closes = [10, 9, 8, 7, 6, 12]
    sig = ma_cross(_flat(closes), {"ma_short": 2, "ma_long": 4})
    assert sig is not None
    assert sig.direction == "long"
    assert sig.rule == "ma_cross"


def test_ma_cross_none_when_no_cross():
    closes = [1, 2, 3, 4, 5, 6]  # 持续上行,无穿越
    assert ma_cross(_flat(closes), {"ma_short": 2, "ma_long": 4}) is None


def test_rsi_oversold_long():
    closes = list(range(15, 0, -1))  # 持续下跌 -> rsi=0 -> 超卖
    sig = rsi_extreme(_flat(closes), {"rsi_period": 14, "rsi_oversold": 30})
    assert sig is not None and sig.direction == "long"


def test_volume_spike_up_is_long():
    opens = [10] * 5 + [10]
    closes = [10] * 5 + [11]  # 最后一根上涨
    highs = [c + 0.1 for c in closes]
    lows = [c - 0.1 for c in closes]
    vols = [100, 100, 100, 100, 100, 300]  # 量比=3
    sig = volume_spike(
        _bars(opens, highs, lows, closes, vols),
        {"vol_window": 5, "vol_spike_mult": 2.0},
    )
    assert sig is not None and sig.direction == "long"
    assert sig.detail["vol_ratio"] == 3.0


def test_break_intraday_high():
    opens = [10, 10, 10]
    highs = [10.5, 10.6, 11.0]  # 最后一根突破此前最高 10.6
    lows = [9.5, 9.6, 10.0]
    closes = [10, 10, 10.9]
    sig = break_intraday_high_low(
        _bars(opens, highs, lows, closes, [100, 100, 100]), {}
    )
    assert sig is not None and sig.direction == "long"
