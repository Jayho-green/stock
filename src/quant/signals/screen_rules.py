"""选股规则:盘前/盘后在日线上筛选候选股。

每条规则签名: ``(daily_df, cfg) -> bool``(命中为 True)。
加新规则 = 写一个函数 + 在 SCREEN_RULES 里加一行。
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from ..indicators import add_kdj, add_zhixing

ScreenRule = Callable[[pd.DataFrame, dict], bool]


def above_ma(daily: pd.DataFrame, cfg: dict) -> bool:
    """收盘价站上 N 日均线。"""
    n = cfg.get("ma_window", 20)
    if len(daily) < n:
        return False
    ma = daily["close"].rolling(n).mean().iloc[-1]
    return bool(daily["close"].iloc[-1] >= ma)


def volume_surge(daily: pd.DataFrame, cfg: dict) -> bool:
    """最近一日成交量 >= 前 lookback 日均量 * 倍数。"""
    lookback = cfg.get("vol_lookback", 5)
    mult = cfg.get("vol_surge_mult", 1.5)
    if len(daily) < lookback + 1:
        return False
    recent = daily["volume"].iloc[-1]
    base = daily["volume"].iloc[-(lookback + 1):-1].mean()
    return bool(recent >= base * mult)


def zhixing_pick(daily: pd.DataFrame, cfg: dict) -> bool:
    """知行合一选股标准(日线,三条件同时满足):

    1. 收盘价 > 知行短期趋势线
    2. 知行短期趋势线 > 知行多空线
    3. KDJ 的 J 值贴近近 N 日最低(A 方案:高于最低 (tol-1) 的幅度以内;对负 J 也成立)
    """
    ema_p = cfg.get("zx_ema", 10)
    ma_periods = tuple(cfg.get("zx_ma_periods", (14, 28, 57, 114)))
    n = cfg.get("kdj_n", 9)
    kp = cfg.get("kdj_k", 3)
    dp = cfg.get("kdj_d", 3)
    jwin = cfg.get("j_low_window", 20)
    jtol = cfg.get("j_low_tol", 1.05)

    need = max(max(ma_periods), jwin)
    if len(daily) < need:
        return False

    df = add_zhixing(daily, ema_p, ma_periods)
    df = add_kdj(df, n, kp, dp)
    last = df.iloc[-1]
    if pd.isna(last["zx_short"]) or pd.isna(last["zx_bull"]):
        return False

    cond_price = last["close"] > last["zx_short"]
    cond_trend = last["zx_short"] > last["zx_bull"]
    j_low = df["j"].iloc[-jwin:].min()
    band = abs(j_low) * (jtol - 1)  # A 方案:高于最低 5% 幅度以内(对负 J 也正确)
    cond_j = last["j"] <= j_low + band
    return bool(cond_price and cond_trend and cond_j)


# 注册表:加规则 = 加一行。当前选股标准 = 知行合一。
# (above_ma / volume_surge 作为可选规则保留,未启用)
SCREEN_RULES: list[ScreenRule] = [zhixing_pick]


def screen(
    codes: list[str],
    source,
    rules: list[ScreenRule],
    cfg: dict,
    start: str,
    end: str,
) -> list[str]:
    """对候选 codes 逐个取日线,命中全部 rules 的入选。"""
    selected: list[str] = []
    for code in codes:
        daily = source.get_daily_bars(code, start, end)
        if all(rule(daily, cfg) for rule in rules):
            selected.append(code)
    return selected
