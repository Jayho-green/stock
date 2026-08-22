import pandas as pd

from quant.signals.screen_rules import (
    above_ma,
    volume_surge,
    zhixing_pick,
    screen,
    SCREEN_RULES,
)


def _daily(closes, vols=None):
    n = len(closes)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": vols if vols else [1000] * n,
        }
    )


def test_above_ma_true():
    closes = list(range(1, 25))  # 持续上涨,收盘在均线之上
    assert above_ma(_daily(closes), {"ma_window": 20}) is True


def test_above_ma_false():
    closes = list(range(25, 1, -1))  # 持续下跌,收盘在均线之下
    assert above_ma(_daily(closes), {"ma_window": 20}) is False


def test_volume_surge_true():
    vols = [1000] * 5 + [2000]  # 末日放量 2 倍
    assert volume_surge(_daily([10] * 6, vols), {"vol_lookback": 5, "vol_surge_mult": 1.5}) is True


def test_volume_surge_false():
    vols = [1000] * 6  # 无放量
    assert volume_surge(_daily([10] * 6, vols), {"vol_lookback": 5, "vol_surge_mult": 1.5}) is False


class FakeSource:
    def __init__(self, data):
        self._data = data

    def get_daily_bars(self, code, start, end):
        return self._data[code]


def test_screen_combines_rules():
    hit = _daily(list(range(1, 25)), [1000] * 23 + [3000])  # 站上均线 + 放量
    miss = _daily(list(range(25, 1, -1)), [1000] * 24)       # 跌破均线
    src = FakeSource({"AAA": hit, "BBB": miss})
    cfg = {"ma_window": 20, "vol_lookback": 5, "vol_surge_mult": 1.5}
    rules = [above_ma, volume_surge]  # 显式规则,不依赖注册表内容
    result = screen(["AAA", "BBB"], src, rules, cfg, "20240101", "20240201")
    assert result == ["AAA"]


def test_screen_rules_registry_is_zhixing():
    assert SCREEN_RULES == [zhixing_pick]


def _ohlc(closes):
    n = len(closes)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2023-01-01", periods=n, freq="D"),
            "open": closes,
            "high": [c + 0.3 for c in closes],
            "low": [c - 0.3 for c in closes],
            "close": closes,
            "volume": [1000] * n,
        }
    )


_ZX_CFG = {
    "zx_ema": 10,
    "zx_ma_periods": [14, 28, 57, 114],
    "kdj_n": 9, "kdj_k": 3, "kdj_d": 3,
    "j_low_window": 20, "j_low_tol": 1.05,
}


def test_zhixing_false_insufficient_data():
    assert zhixing_pick(_ohlc(list(range(1, 50))), _ZX_CFG) is False  # < 114


def test_zhixing_false_downtrend():
    # 持续下跌:价格在短期趋势线之下 -> False
    closes = [200 - i for i in range(140)]
    assert zhixing_pick(_ohlc(closes), _ZX_CFG) is False


def test_zhixing_false_when_j_recovered_above_recent_low():
    # 上涨中途有一次回调(J 在窗口内更低),随后反弹使当前 J 抬高 -> 不在最低附近 -> False
    closes = [10 + i for i in range(135)] + [125.0, 130.0, 135.0, 140.0, 145.0]
    assert zhixing_pick(_ohlc(closes), _ZX_CFG) is False


def test_zhixing_uptrend_with_final_dip_true():
    # 长期上涨 + 最后一根小幅回调:价格仍在趋势线上方、趋势线在多空线上方、J 跌到近20日最低
    closes = [10 + i for i in range(139)] + [140.0]  # 139 根升到 148,末根回调到 140
    assert zhixing_pick(_ohlc(closes), _ZX_CFG) is True
