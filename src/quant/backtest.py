"""轻量回测:在历史 bars 上回放一条规则,统计触发后的前向收益。

关键纪律:
- **无未来函数**:第 i 根触发判断只喂 ``df.iloc[:i+1]``,绝不使用 i 之后的数据。
- **计成本**:每笔减去 round-trip 成本(佣金+印花税+滑点的合计比例)。
- 前向收益用 i+forward 根的收盘价衡量"触发之后实际发生了什么"(这是结果度量,非未来函数)。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .signals.types import Signal

Rule = Callable[[pd.DataFrame, dict], "Signal | None"]

# A股 round-trip 默认成本估计:佣金双边~0.0003 + 卖出印花税0.001 + 滑点缓冲 ≈ 0.0013
DEFAULT_COST = 0.0013


@dataclass
class Trade:
    rule: str
    direction: str
    signal_time: Any
    entry_time: Any
    exit_time: Any
    signal_price: float
    entry_price: float
    exit_price: float
    return_pct: float

    def to_record(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "direction": self.direction,
            "signal_time": str(self.signal_time),
            "entry_time": str(self.entry_time),
            "exit_time": str(self.exit_time),
            "signal_price": self.signal_price,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "return_pct": self.return_pct,
        }


@dataclass
class Stats:
    trades: int
    win_rate: float
    avg_return: float
    returns: list[float] = field(default_factory=list)
    total_return: float = 0.0
    max_drawdown: float = 0.0
    best_return: float = 0.0
    worst_return: float = 0.0
    profit_factor: float = 0.0
    trade_records: list[Trade] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)


def _max_drawdown(equity: list[dict[str, Any]]) -> float:
    peak = 1.0
    worst = 0.0
    for point in equity:
        value = float(point["equity"])
        peak = max(peak, value)
        if peak:
            worst = min(worst, value / peak - 1)
    return worst


def backtest(
    df: pd.DataFrame,
    rule: Rule,
    cfg: dict,
    forward: int = 5,
    cost: float = DEFAULT_COST,
    allow_overlap: bool = False,
    entry_timing: str = "next_open",
) -> Stats:
    if forward < 1:
        raise ValueError("forward must be >= 1")
    if entry_timing not in {"next_open", "signal_close"}:
        raise ValueError("entry_timing must be next_open or signal_close")
    opens = df["open"].to_numpy() if "open" in df.columns else df["close"].to_numpy()
    closes = df["close"].to_numpy()
    n = len(df)
    rets: list[float] = []
    trades_out: list[Trade] = []
    equity_curve: list[dict[str, Any]] = []
    equity = 1.0
    i = 0
    while i < n - forward:
        sub = df.iloc[: i + 1]  # 只到第 i 根,杜绝未来函数
        sig = rule(sub, cfg)
        if sig is None:
            i += 1
            continue
        # 默认日线信号在收盘后确认、次日开盘入场;部分盘后选股规则可按信号日收盘价回测。
        entry_idx = i if entry_timing == "signal_close" else i + 1
        exit_idx = i + forward
        signal_price = closes[i]
        entry = closes[entry_idx] if entry_timing == "signal_close" else opens[entry_idx]
        exit_ = closes[exit_idx]
        if entry == 0:
            i += 1
            continue
        if sig.direction == "long":
            r = (exit_ - entry) / entry - cost
        else:  # short:价格下跌为盈利
            r = (entry - exit_) / entry - cost
        ret = float(r)
        rets.append(ret)
        trade = Trade(
            rule=sig.rule,
            direction=sig.direction,
            signal_time=sig.time,
            entry_time=df["datetime"].iloc[entry_idx],
            exit_time=df["datetime"].iloc[exit_idx],
            signal_price=float(signal_price),
            entry_price=float(entry),
            exit_price=float(exit_),
            return_pct=ret,
        )
        trades_out.append(trade)
        equity *= 1 + ret
        equity_curve.append({"time": str(trade.exit_time), "equity": float(equity)})
        i = exit_idx if not allow_overlap else i + 1

    trades = len(rets)
    win_rate = sum(1 for r in rets if r > 0) / trades if trades else 0.0
    avg = sum(rets) / trades if trades else 0.0
    gross_profit = sum(r for r in rets if r > 0)
    gross_loss = abs(sum(r for r in rets if r < 0))
    profit_factor = gross_profit / gross_loss if gross_loss else 0.0
    return Stats(
        trades=trades,
        win_rate=win_rate,
        avg_return=avg,
        returns=rets,
        total_return=equity - 1 if trades else 0.0,
        max_drawdown=_max_drawdown(equity_curve),
        best_return=max(rets) if rets else 0.0,
        worst_return=min(rets) if rets else 0.0,
        profit_factor=profit_factor,
        trade_records=trades_out,
        equity_curve=equity_curve,
    )
