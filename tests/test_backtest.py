import pandas as pd
import pytest

from quant.backtest import backtest
from quant.signals.types import Signal


def _bars(closes, opens=None):
    n = len(closes)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01 09:30", periods=n, freq="min"),
            "open": opens if opens is not None else closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [100] * n,
        }
    )


def _rule_at_third_bar(direction):
    def rule(d, cfg):
        if len(d) == 3:
            return Signal("t", direction, d["datetime"].iloc[-1], float(d["close"].iloc[-1]), {})
        return None

    return rule


def test_forward_return_long_no_cost():
    df = _bars([10, 10, 10, 10, 12])  # i=2 触发,下一根入场@10, i=4 @12
    stats = backtest(df, _rule_at_third_bar("long"), {}, forward=2, cost=0.0)
    assert stats.trades == 1
    assert abs(stats.avg_return - 0.2) < 1e-9
    assert stats.win_rate == 1.0


def test_entry_uses_next_bar_open_after_signal():
    df = _bars([10, 10, 10, 11, 12], opens=[10, 10, 10, 11, 12])
    stats = backtest(df, _rule_at_third_bar("long"), {}, forward=2, cost=0.0)
    assert abs(stats.avg_return - ((12 - 11) / 11)) < 1e-9
    trade = stats.trade_records[0]
    assert str(trade.signal_time) == "2024-01-01 09:32:00"
    assert str(trade.entry_time) == "2024-01-01 09:33:00"
    assert str(trade.exit_time) == "2024-01-01 09:34:00"


def test_signal_close_entry_uses_signal_bar_close():
    df = _bars([10, 10, 10, 11, 12], opens=[10, 10, 99, 11, 12])
    stats = backtest(
        df,
        _rule_at_third_bar("long"),
        {},
        forward=2,
        cost=0.0,
        entry_timing="signal_close",
    )

    assert abs(stats.avg_return - ((12 - 10) / 10)) < 1e-9
    trade = stats.trade_records[0]
    assert str(trade.signal_time) == "2024-01-01 09:32:00"
    assert str(trade.entry_time) == "2024-01-01 09:32:00"
    assert str(trade.exit_time) == "2024-01-01 09:34:00"
    assert trade.signal_price == 10
    assert trade.entry_price == 10


def test_rejects_unknown_entry_timing():
    with pytest.raises(ValueError, match="entry_timing"):
        backtest(_bars([10, 10, 10, 11]), _rule_at_third_bar("long"), {}, entry_timing="bad")


def test_cost_is_subtracted():
    df = _bars([10, 10, 10, 10, 12])
    stats = backtest(df, _rule_at_third_bar("long"), {}, forward=2, cost=0.05)
    assert abs(stats.avg_return - 0.15) < 1e-9


def test_short_direction_profits_on_drop():
    df = _bars([10, 10, 10, 10, 8])  # 跌到 8,short 盈利 0.2
    stats = backtest(df, _rule_at_third_bar("short"), {}, forward=2, cost=0.0)
    assert abs(stats.avg_return - 0.2) < 1e-9


def test_no_lookahead_rule_sees_only_past():
    df = _bars([1, 2, 3, 4, 5])
    seen_lengths = []

    def spy_rule(d, cfg):
        seen_lengths.append(len(d))
        # 规则看到的最后一根收盘必须等于其切片长度对应的值(=按顺序的历史)
        assert d["close"].iloc[-1] == len(d)
        return None

    backtest(df, spy_rule, {}, forward=1, cost=0.0)
    # forward=1 时,i 从 0..3,共 4 次,切片长度 1..4
    assert seen_lengths == [1, 2, 3, 4]


def test_no_trades_returns_zeroed_stats():
    df = _bars([10, 10, 10, 10, 10])
    stats = backtest(df, lambda d, c: None, {}, forward=2)
    assert stats.trades == 0
    assert stats.win_rate == 0.0
    assert stats.avg_return == 0.0


def test_trade_records_and_equity_curve_are_returned():
    df = _bars([10, 10, 10, 11, 12])
    stats = backtest(df, _rule_at_third_bar("long"), {}, forward=2, cost=0.0)
    assert abs(stats.total_return - ((12 - 11) / 11)) < 1e-9
    assert stats.max_drawdown == 0.0
    assert abs(stats.best_return - ((12 - 11) / 11)) < 1e-9
    assert abs(stats.worst_return - ((12 - 11) / 11)) < 1e-9
    assert len(stats.trade_records) == 1
    assert stats.trade_records[0].signal_price == 10
    assert stats.trade_records[0].entry_price == 11
    assert stats.trade_records[0].exit_price == 12
    assert stats.equity_curve[0]["time"] == "2024-01-01 09:34:00"
    assert abs(stats.equity_curve[0]["equity"] - (1 + (12 - 11) / 11)) < 1e-9


def test_default_backtest_does_not_open_overlapping_trades():
    df = _bars([10, 11, 12, 13, 14, 15])

    def always_long(d, cfg):
        return Signal("always", "long", d["datetime"].iloc[-1], float(d["close"].iloc[-1]), {})

    no_overlap = backtest(df, always_long, {}, forward=2, cost=0.0)
    overlap = backtest(df, always_long, {}, forward=2, cost=0.0, allow_overlap=True)
    assert no_overlap.trades == 2
    assert overlap.trades == 4
