"""交易日历与交易时段判断(纯函数,trade_dates 由调用方注入)。"""

from __future__ import annotations

from datetime import date, datetime, time

MORNING = (time(9, 30), time(11, 30))
AFTERNOON = (time(13, 0), time(15, 0))


def is_in_session(dt: datetime) -> bool:
    """是否处于 A股交易时段(09:30-11:30 或 13:00-15:00)。"""
    t = dt.time()
    return (MORNING[0] <= t <= MORNING[1]) or (AFTERNOON[0] <= t <= AFTERNOON[1])


def is_trading_day(d: date, trade_dates: set[date]) -> bool:
    """该日是否为交易日(trade_dates 来自数据源的交易日历)。"""
    return d in trade_dates
