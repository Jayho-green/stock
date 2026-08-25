"""技术指标:全部为纯函数,输入 Bars DataFrame,返回新增指标列的副本。

Bars DataFrame 标准 schema(按时间升序):
    datetime, open, high, low, close, volume
"""

from __future__ import annotations

import pandas as pd


def add_ma(df: pd.DataFrame, windows: tuple[int, ...] = (5, 20)) -> pd.DataFrame:
    """新增简单移动均线列 maN。"""
    df = df.copy()
    for w in windows:
        df[f"ma{w}"] = df["close"].rolling(w).mean()
    return df


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """新增 RSI 列(Wilder 平滑)。全程上涨 -> 100,全程下跌 -> 0。"""
    df = df.copy()
    delta = df["close"].diff().fillna(0.0)
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - 100 / (1 + rs)
    # avg_loss==0:有涨无跌 -> 100
    rsi = rsi.where(avg_loss != 0, 100.0)
    # 涨跌皆为 0(横盘)-> 中性 50,避免被误判为超买
    both_zero = (avg_gain == 0) & (avg_loss == 0)
    rsi = rsi.where(~both_zero, 50.0)
    df["rsi"] = rsi
    return df


def add_macd(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """新增 MACD 三列:dif, dea, macd(macd = 2*(dif-dea),A股习惯)。"""
    df = df.copy()
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    df["dif"] = ema_fast - ema_slow
    df["dea"] = df["dif"].ewm(span=signal, adjust=False).mean()
    df["macd"] = 2 * (df["dif"] - df["dea"])
    return df


def add_kdj(
    df: pd.DataFrame, n: int = 9, k_period: int = 3, d_period: int = 3
) -> pd.DataFrame:
    """新增 KDJ 三列:k, d, j(通达信口径)。

    RSV = (C-LLV(L,n))/(HHV(H,n)-LLV(L,n))*100
    K = SMA(RSV,k_period,1) = EMA(alpha=1/k_period);D = SMA(K,d_period,1);J = 3K-2D
    """
    df = df.copy()
    low_n = df["low"].rolling(n, min_periods=1).min()
    high_n = df["high"].rolling(n, min_periods=1).max()
    denom = high_n - low_n
    rsv = (df["close"] - low_n) / denom * 100
    rsv = rsv.where(denom != 0, 0.0)  # 高低相等(横盘)时 RSV=0
    k = rsv.ewm(alpha=1 / k_period, adjust=False).mean()
    d = k.ewm(alpha=1 / d_period, adjust=False).mean()
    df["k"] = k
    df["d"] = d
    df["j"] = 3 * k - 2 * d
    return df


def add_zhixing(
    df: pd.DataFrame,
    ema_period: int = 10,
    ma_periods: tuple[int, ...] = (14, 28, 57, 114),
) -> pd.DataFrame:
    """新增"知行合一线"两列:

    - zx_short 知行短期趋势线 = EMA(EMA(C, ema_period), ema_period)
    - zx_bull  知行多空线     = 多条 MA(C, p) 的平均
    """
    df = df.copy()
    e1 = df["close"].ewm(span=ema_period, adjust=False).mean()
    df["zx_short"] = e1.ewm(span=ema_period, adjust=False).mean()
    ma_sum = sum(df["close"].rolling(p).mean() for p in ma_periods)
    df["zx_bull"] = ma_sum / len(ma_periods)
    return df


def add_volume_features(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """新增量价特征:

    - vol_ma: 前 window 根(不含当根)的平均成交量
    - vol_ratio: 当根成交量 / vol_ma(量比)
    """
    df = df.copy()
    vol_ma = df["volume"].rolling(window).mean().shift(1)
    df["vol_ma"] = vol_ma
    df["vol_ratio"] = df["volume"] / vol_ma
    return df
