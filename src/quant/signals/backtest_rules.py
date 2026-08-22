"""Backtest-only signal rules.

These rules are not used by intraday monitoring. They can depend on daily
screening conditions and custom execution assumptions.
"""

from __future__ import annotations

import pandas as pd

from .screen_rules import zhixing_pick
from .types import Signal


def zhixing_pick_close(df: pd.DataFrame, cfg: dict) -> Signal | None:
    """知行多空方案:日线满足选股条件时,按当日收盘价买入做多。"""

    if not zhixing_pick(df, cfg):
        return None
    last = df.iloc[-1]
    return Signal(
        "zhixing_pick_close",
        "long",
        last["datetime"],
        float(last["close"]),
        {"entry_timing": "signal_close"},
    )


BACKTEST_ONLY_RULES = [zhixing_pick_close]

BACKTEST_ENTRY_TIMING = {
    "zhixing_pick_close": "signal_close",
}
