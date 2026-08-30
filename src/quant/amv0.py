"""指南针成本均线(CYC)体系。

0AMV = 无穷成本均线 = CYC∞。通达信源码为::

    无穷成本均线: DMA(CLOSE, VOL/CAPITAL);
    CYC∞:        DMA(AMOUNT/(100*VOL), VOL/(100*FINANCE(7)));
    CYC5/13/34:  MA(AMOUNT/(100*VOL), 5/13/34);

DMA(X,A) 即 ``Y_t = A_t*X_t + (1-A_t)*Y_{t-1}``,展开后::

    0AMV_t = 换手率_t * 当日均价_t + (1-换手率_t) * 0AMV_{t-1}

本质是以每日换手率为衰减因子的持仓成本线,等价于筹码分布的均值。
记忆半衰期约 ``ln2/平均换手率`` 个交易日。

数据源说明:本机东方财富接口不可达,统一走新浪(成交额)+腾讯(前复权价/流通份额)。
ETF 存在份额折算(拆分),必须用腾讯前复权价反解复权因子修正,否则会出现
单日 -50% 的假跌并彻底污染指标。
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

# 各板块主流 ETF: code -> (名称, 板块)
UNIVERSE: dict[str, tuple[str, str]] = {
    # 科技
    "512480": ("半导体ETF", "科技"),
    "159995": ("芯片ETF", "科技"),
    "512760": ("半导体设备", "科技"),
    "515050": ("5G通信ETF", "科技"),
    "159819": ("人工智能ETF", "科技"),
    "512720": ("计算机ETF", "科技"),
    # 新能源
    "515790": ("光伏ETF", "新能源"),
    "515030": ("新能源车ETF", "新能源"),
    "516160": ("新能源ETF", "新能源"),
    "159611": ("电力ETF", "新能源"),
    # 金融
    "512880": ("券商ETF", "金融"),
    "512800": ("银行ETF", "金融"),
    "512070": ("证券保险ETF", "金融"),
    # 周期
    "515220": ("煤炭ETF", "周期"),
    "512400": ("有色金属ETF", "周期"),
    "518880": ("黄金ETF", "周期"),
    "512200": ("房地产ETF", "周期"),
    "515210": ("钢铁ETF", "周期"),
    # 医药
    "512170": ("医疗ETF", "医药"),
    "159929": ("医药ETF", "医药"),
    "512010": ("医药ETF易方达", "医药"),
    # 消费
    "159928": ("消费ETF", "消费"),
    "512690": ("酒ETF", "消费"),
    "516110": ("汽车ETF", "消费"),
    # 农林牧渔
    "159825": ("农业ETF", "农林牧渔"),
    # 传媒 / 军工
    "512980": ("传媒ETF", "传媒"),
    "516010": ("游戏ETF", "传媒"),
    "512660": ("军工ETF", "军工"),
    # 宽基
    "510300": ("沪深300ETF", "宽基"),
    "510500": ("中证500ETF", "宽基"),
    "588000": ("科创50ETF", "宽基"),
    "159915": ("创业板ETF", "宽基"),
    "510050": ("上证50ETF", "宽基"),
    "512100": ("中证1000ETF", "宽基"),
}

SECTOR_ORDER = [
    "科技", "新能源", "金融", "周期", "医药", "消费", "农林牧渔", "传媒", "军工", "宽基",
]

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def sina_symbol(code: str) -> str:
    """ETF 代码 -> 新浪/腾讯前缀符号。5 开头为沪市,其余为深市。"""

    return ("sh" if code.startswith("5") else "sz") + code


class DataUnavailable(RuntimeError):
    """数据源不可用(网络异常/接口变更)。"""


def _curl(url: str, timeout: int = 25, gbk: bool = False) -> str:
    out = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", _UA, url],
        capture_output=True,
    )
    if out.returncode != 0:
        raise DataUnavailable(f"curl 失败: {url}")
    return out.stdout.decode("gbk", "ignore") if gbk else out.stdout.decode("utf-8", "ignore")


def fetch_raw_bars(code: str) -> pd.DataFrame:
    """新浪 ETF 日线(不复权,含成交额)。返回 date/open/high/low/close/volume/amount。"""

    import akshare as ak

    frame = ak.fund_etf_hist_sina(symbol=sina_symbol(code))
    if frame is None or frame.empty:
        raise DataUnavailable(f"新浪未返回 {code} 数据")
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    return frame.sort_values("date").reset_index(drop=True)


def fetch_qfq_bars(code: str, count: int = 800) -> pd.DataFrame:
    """腾讯前复权日线。count 上限约 800,用于反解复权因子修正 ETF 份额折算。"""

    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={sina_symbol(code)},day,,,{count},qfq"
    )
    payload = json.loads(_curl(url))
    data = payload.get("data")
    if not isinstance(data, dict) or not data:
        raise DataUnavailable(f"腾讯未返回 {code} 前复权数据: {payload.get('msg')}")
    node = next(iter(data.values()))
    rows = node.get("qfqday") or node.get("day")
    if not rows:
        raise DataUnavailable(f"腾讯 {code} 前复权数据为空")
    frame = pd.DataFrame(
        [r[:6] for r in rows],
        columns=["date", "q_open", "q_close", "q_high", "q_low", "q_vol"],
    )
    for col in frame.columns[1:]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    return frame.sort_values("date").reset_index(drop=True)


def fetch_capital(codes: list[str]) -> dict[str, float]:
    """腾讯实时行情反解流通份额(股) = 流通市值(亿元) * 1e8 / 现价。"""

    result: dict[str, float] = {}
    for i in range(0, len(codes), 15):
        batch = [sina_symbol(c) for c in codes[i : i + 15]]
        text = _curl("https://qt.gtimg.cn/q=" + ",".join(batch), gbk=True)
        for line in text.split("\n"):
            if "=" not in line:
                continue
            fields = line.split("=", 1)[1].strip(' ";').split("~")
            if len(fields) < 46:
                continue
            try:
                price = float(fields[3])
                float_mktcap_yi = float(fields[44])
            except (TypeError, ValueError):
                continue
            if price > 0 and float_mktcap_yi > 0:
                result[fields[2]] = float_mktcap_yi * 1e8 / price
        time.sleep(0.35)
    return result


def dma(values: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """通达信 DMA(X,A): Y_t = A_t*X_t + (1-A_t)*Y_{t-1}。"""

    out = np.empty(len(values), dtype=float)
    if len(values) == 0:
        return out
    out[0] = values[0]
    for t in range(1, len(values)):
        a = alpha[t]
        if not np.isfinite(a) or a <= 0:
            a = 0.0
        elif a > 1:
            a = 1.0
        out[t] = a * values[t] + (1 - a) * out[t - 1]
    return out


def compute_cyc(raw: pd.DataFrame, qfq: pd.DataFrame, capital: float) -> pd.DataFrame:
    """合并不复权成交额与前复权价,输出完整成本均线体系。"""

    merged = qfq.merge(
        raw[["date", "close", "volume", "amount"]], on="date", how="left"
    ).sort_values("date").reset_index(drop=True)
    merged = merged[merged["close"].notna() & (merged["volume"] > 0)].reset_index(drop=True)
    if merged.empty:
        raise DataUnavailable("复权与原始行情无交集")

    factor = merged["q_close"] / merged["close"]          # 复权因子
    avg_price = (merged["amount"] / merged["volume"]) * factor   # 复权后当日成交均价
    vol_adj = merged["volume"] / factor                    # 折算到当前份额口径的股数
    alpha = np.clip((vol_adj / capital).to_numpy(), 0, 1) if capital else np.zeros(len(merged))

    out = pd.DataFrame(
        {
            "date": merged["date"],
            "open": merged["q_open"],
            "high": merged["q_high"],
            "low": merged["q_low"],
            "close": merged["q_close"],
            "volume": vol_adj.round(0),
            "avg_price": avg_price,
            "alpha": alpha,
        }
    )
    out["amv0"] = dma(out["avg_price"].to_numpy(), alpha)
    for window in (5, 13, 34):
        out[f"cyc{window}"] = out["avg_price"].rolling(window).mean()
    out["cys0"] = (out["close"] / out["amv0"] - 1) * 100
    out["slope0_20"] = out["amv0"].pct_change(20) * 100
    out["above0"] = (out["close"] > out["amv0"]).astype(int)
    add_signals(out)
    return out


# 超跌反转信号阈值:CYS0 低于此值视为"全体持仓深度套牢"
DEEP_DISCOUNT = -8.0
WATCH_DISCOUNT = -6.0


def add_signals(out: pd.DataFrame) -> pd.DataFrame:
    """超跌反转信号:深度折价 + 乖离率回升。

    实证(34只ETF/约600日/2023-05~2026-08):该信号平均在距 60 日低点仅 1.6% 处触发,
    未来 20 日均收益 +5.5%、胜率 67%、平均最大回撤 -3.3%;
    而"三线多头排列"要等价格已从低点涨 12.9% 才成立,未来 20 日均收益 0.9%,
    反而低于 1.5% 的全样本基准。

    重要限制:按日聚类后该信号的**横截面**超额收益并不显著(+0.51%, CI 含 0),
    收益主要来自大盘 beta。它回答"何时加仓",不回答"买哪个板块"。
    """

    cys_up = out["cys0"] > out["cys0"].shift(1)
    out["deep_discount"] = (out["cys0"] < DEEP_DISCOUNT).astype(int)
    out["buy_signal"] = ((out["cys0"] < DEEP_DISCOUNT) & cys_up).astype(int)
    out["align_signal"] = (
        (out["cyc5"] > out["cyc13"])
        & (out["cyc13"] > out["amv0"])
        & ~((out["cyc5"].shift(1) > out["cyc13"].shift(1)) & (out["cyc13"].shift(1) > out["amv0"].shift(1)))
    ).astype(int)
    return out


def zone_of(cys0: float | None) -> str:
    """按 CYS0 给出持仓成本分区,用于面板着色与文案。"""

    if cys0 is None or pd.isna(cys0):
        return "未知"
    if cys0 < DEEP_DISCOUNT:
        return "深度折价"
    if cys0 < WATCH_DISCOUNT:
        return "折价"
    if cys0 <= 3:
        return "成本区"
    return "溢价"


@dataclass(frozen=True)
class Instrument:
    code: str
    name: str
    sector: str


def universe_list() -> list[Instrument]:
    """按板块顺序返回标的列表。"""

    items = [Instrument(c, n, s) for c, (n, s) in UNIVERSE.items()]
    items.sort(
        key=lambda x: (
            SECTOR_ORDER.index(x.sector) if x.sector in SECTOR_ORDER else 99,
            x.code,
        )
    )
    return items
