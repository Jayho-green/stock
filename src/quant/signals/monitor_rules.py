"""盯盘信号规则。

每条规则签名: ``(df, cfg) -> Signal | None``
- df: Bars DataFrame(分钟K,按时间升序,标准 schema)
- cfg: dict,提供阈值/窗口参数,缺省走默认值
- 返回:最后一根 bar 上"刚触发"的 Signal,否则 None(穿越类规则用前一根状态判断,避免重复)

加新规则 = 写一个函数 + 在 MONITOR_RULES 里加一行。
"""

from __future__ import annotations

import pandas as pd

from ..indicators import add_macd, add_rsi, add_volume_features
from .types import Signal


def _last(df: pd.DataFrame):
    return df.iloc[-1]


def ma_cross(df: pd.DataFrame, cfg: dict) -> Signal | None:
    """均线金叉(long)/死叉(short):短均线穿越长均线。"""
    short = cfg.get("ma_short", 5)
    long_ = cfg.get("ma_long", 20)
    if len(df) < long_ + 1:
        return None
    ms = df["close"].rolling(short).mean()
    ml = df["close"].rolling(long_).mean()
    if pd.isna(ms.iloc[-2]) or pd.isna(ml.iloc[-2]):
        return None
    prev = ms.iloc[-2] - ml.iloc[-2]
    curr = ms.iloc[-1] - ml.iloc[-1]
    last = _last(df)
    detail = {"ma_short": short, "ma_long": long_}
    if prev <= 0 and curr > 0:
        return Signal("ma_cross", "long", last["datetime"], float(last["close"]), detail)
    if prev >= 0 and curr < 0:
        return Signal("ma_cross", "short", last["datetime"], float(last["close"]), detail)
    return None


def macd_cross(df: pd.DataFrame, cfg: dict) -> Signal | None:
    """MACD 金叉(dif 上穿 dea,long)/死叉(short)。"""
    fast = cfg.get("macd_fast", 12)
    slow = cfg.get("macd_slow", 26)
    signal = cfg.get("macd_signal", 9)
    if len(df) < slow + 2:
        return None
    m = add_macd(df, fast, slow, signal)
    diff = m["dif"] - m["dea"]
    prev, curr = diff.iloc[-2], diff.iloc[-1]
    if pd.isna(prev):
        return None
    last = _last(df)
    detail = {"dif": float(m["dif"].iloc[-1]), "dea": float(m["dea"].iloc[-1])}
    if prev <= 0 and curr > 0:
        return Signal("macd_cross", "long", last["datetime"], float(last["close"]), detail)
    if prev >= 0 and curr < 0:
        return Signal("macd_cross", "short", last["datetime"], float(last["close"]), detail)
    return None


def rsi_extreme(df: pd.DataFrame, cfg: dict) -> Signal | None:
    """RSI 超卖(<=阈值,long 反弹)/超买(>=阈值,short)。"""
    period = cfg.get("rsi_period", 14)
    oversold = cfg.get("rsi_oversold", 30)
    overbought = cfg.get("rsi_overbought", 70)
    if len(df) < period + 1:
        return None
    val = add_rsi(df, period)["rsi"].iloc[-1]
    if pd.isna(val):
        return None
    last = _last(df)
    if val <= oversold:
        return Signal("rsi_extreme", "long", last["datetime"], float(last["close"]), {"rsi": float(val)})
    if val >= overbought:
        return Signal("rsi_extreme", "short", last["datetime"], float(last["close"]), {"rsi": float(val)})
    return None


def volume_spike(df: pd.DataFrame, cfg: dict) -> Signal | None:
    """放量异动:量比 >= 阈值。放量上涨 long,放量下跌 short。"""
    window = cfg.get("vol_window", 5)
    mult = cfg.get("vol_spike_mult", 2.0)
    if len(df) < window + 1:
        return None
    ratio = add_volume_features(df, window)["vol_ratio"].iloc[-1]
    if pd.isna(ratio) or ratio < mult:
        return None
    last = _last(df)
    direction = "long" if last["close"] >= last["open"] else "short"
    return Signal(
        "volume_spike", direction, last["datetime"], float(last["close"]),
        {"vol_ratio": float(ratio), "mult": mult},
    )


def break_intraday_high_low(df: pd.DataFrame, cfg: dict) -> Signal | None:
    """突破当日(此前所有 bar)最高 -> long;跌破当日最低 -> short。

    假设传入的是当日分钟K。
    """
    if len(df) < 2:
        return None
    last = _last(df)
    prior_high = df["high"].iloc[:-1].max()
    prior_low = df["low"].iloc[:-1].min()
    if last["high"] > prior_high:
        return Signal(
            "break_intraday_high_low", "long", last["datetime"], float(last["close"]),
            {"prior_high": float(prior_high)},
        )
    if last["low"] < prior_low:
        return Signal(
            "break_intraday_high_low", "short", last["datetime"], float(last["close"]),
            {"prior_low": float(prior_low)},
        )
    return None


# 注册表:加规则 = 加一行
MONITOR_RULES = [
    ma_cross,
    macd_cross,
    rsi_extreme,
    volume_spike,
    break_intraday_high_low,
]
