import pandas as pd

from quant.indicators import (
    add_ma,
    add_rsi,
    add_macd,
    add_volume_features,
    add_kdj,
    add_zhixing,
)


def _bars(closes, vols=None):
    n = len(closes)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01 09:30", periods=n, freq="min"),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": vols if vols is not None else [100] * n,
        }
    )


def test_add_ma_last_value():
    df = add_ma(_bars([1, 2, 3, 4, 5]), windows=(2,))
    assert df["ma2"].iloc[-1] == 4.5


def test_add_ma_multiple_windows():
    df = add_ma(_bars([1, 2, 3, 4, 5]), windows=(2, 3))
    assert "ma2" in df.columns and "ma3" in df.columns
    assert df["ma3"].iloc[-1] == 4.0


def test_add_rsi_all_up_is_100():
    df = add_rsi(_bars(list(range(1, 16))), period=14)
    assert df["rsi"].iloc[-1] == 100.0


def test_add_rsi_all_down_is_0():
    df = add_rsi(_bars(list(range(15, 0, -1))), period=14)
    assert df["rsi"].iloc[-1] == 0.0


def test_add_macd_columns_exist():
    df = add_macd(_bars(list(range(1, 40))))
    for col in ("dif", "dea", "macd"):
        assert col in df.columns


def test_volume_ratio():
    df = add_volume_features(
        _bars([1] * 6, vols=[100, 100, 100, 100, 100, 300]), window=5
    )
    assert df["vol_ratio"].iloc[-1] == 3.0


def _ohlc(closes):
    n = len(closes)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [100] * n,
        }
    )


def test_kdj_flat_market_is_zero():
    # 高低相等 -> RSV=0 -> K=D=J=0
    df = add_kdj(_bars([10.0] * 30))
    assert abs(df["j"].iloc[-1]) < 1e-9


def test_kdj_strong_uptrend_high_j():
    df = add_kdj(_ohlc(list(range(1, 40))))
    assert df["j"].iloc[-1] > 80  # 持续上涨,J 处高位


def test_zhixing_columns_and_bull_is_ma_average():
    df = add_zhixing(_ohlc([10.0] * 120), ema_period=10, ma_periods=(2, 4))
    # 恒定价格:双重EMA = 价格;多空线 = 各MA平均 = 价格
    assert abs(df["zx_short"].iloc[-1] - 10.0) < 1e-9
    assert abs(df["zx_bull"].iloc[-1] - 10.0) < 1e-9
