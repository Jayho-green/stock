"""基于指南针活跃市值(0AMV)的板块轮动策略回测。

入场:0AMV 两日累计涨幅 > 4% 且这两天都是红K(收>开)。
     在信号确认日(第二天)收盘,选出这两日资金流入最多的两个板块 ETF,
     两日涨幅**大**的买 40%,涨幅**小**的买 60%(向落后者倾斜)。

出场(三套方案,均以 0AMV 的阴线为触发):
  S1 分档:首根阴线跌幅 >1.3% 全清;0.5%~1.3% 清一半,下一根阴线清完;
          <0.5% 各减 30%,下一根阴线清完。
  S2 首根阴线即全部清仓。
  S3 首根阴线清一半,第二根阴线清完。

重要限制:2020-2026 的**板块资金流历史不存在**(同花顺只提供快照)。
本模块用主动买盘估算作代理:``成交额 × (2*收 - 高 - 低) / (高 - 低)``。
换用其他口径结果差异巨大(累计收益可腰斩),因此选板块这一步不可当作可靠结论。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

STRATEGIES = ("S1", "S2", "S3")
FLOW_KEYS = {
    "mf2": "主动买盘估算",
    "amt2": "两日成交额",
    "sgn2": "成交额×涨跌方向",
}
EXCLUDE_SECTORS = {"宽基"}          # 宽基指数不算"板块"
PRICE_SCALE = 10.0                  # 0AMV 原始值 ÷10 = 实际点位


def load_amv(con) -> pd.DataFrame:
    """从 market.sqlite 读 0AMV 日线并派生入场信号。"""

    d = pd.read_sql(
        "SELECT trade_date,open_raw,close_raw,high_raw,low_raw,amount_raw,is_final "
        "FROM period_bars WHERE period='day' ORDER BY trade_date",
        con,
    )
    d = d[d.is_final == 1].copy()
    for col in ("open", "close", "high", "low"):
        d[col] = d[f"{col}_raw"] / PRICE_SCALE
    d["date"] = pd.to_datetime(d.trade_date.astype(str))
    d["amount"] = d.amount_raw / 1e8
    d = d[["date", "open", "high", "low", "close", "amount"]].reset_index(drop=True)
    d["prev_close"] = d.close.shift(1)
    d["ret"] = d.close.pct_change() * 100
    d["ret2"] = (d.close / d.close.shift(2) - 1) * 100
    d["is_red"] = d.close > d.open
    d["is_yin"] = d.close < d.open
    # 跌幅按对昨收计;若当日实为上涨(跳空高开后收阴),跌幅记 0
    d["drop_pct"] = np.maximum(0.0, (d.prev_close - d.close) / d.prev_close * 100)
    d["signal"] = (d.ret2 > 4) & d.is_red & d.is_red.shift(1).fillna(False)
    return d


def prepare_panel(panel: pd.DataFrame) -> pd.DataFrame:
    """给 ETF 面板补上两日涨幅与三种资金流代理列。"""

    p = panel[~panel.sector.isin(EXCLUDE_SECTORS)].copy()
    p["date"] = pd.to_datetime(p["date"])
    p = p.sort_values(["code", "date"]).reset_index(drop=True)
    g = p.groupby("code", group_keys=False)
    p["ret2"] = (p.close / g["close"].shift(2) - 1) * 100
    span = (p.high - p.low).replace(0, np.nan)
    p["mf"] = p.amount * ((2 * p.close - p.high - p.low) / span)
    p["mf2"] = g["mf"].transform(lambda s: s.rolling(2).sum())
    p["amt2"] = g["amount"].transform(lambda s: s.rolling(2).sum())
    signed = p.amount * np.sign(g["close"].diff())
    p["sgn2"] = signed.groupby(p.code).transform(lambda s: s.rolling(2).sum())
    return p


def _cut_fraction(strategy: str, drop_pct: float, state: int) -> float:
    """给定策略、当根阴线跌幅、已触发次数,返回本次清仓比例(占原始仓位)。"""

    if strategy == "S2":
        return 1.0
    if strategy == "S3":
        return 0.5 if state == 0 else 1.0
    if state > 0:
        return 1.0
    if drop_pct > 1.3:
        return 1.0
    if drop_pct > 0.5:
        return 0.5
    return 0.3


@dataclass
class BacktestResult:
    trades: pd.DataFrame          # 每腿一行
    rounds: pd.DataFrame          # 每轮一行(两腿合并)


def backtest(amv: pd.DataFrame, panel: pd.DataFrame, strategy: str,
             flow_key: str = "mf2", start: str = "2020-01-01",
             end: str = "2026-12-31") -> BacktestResult:
    p = panel[(panel.date >= start) & (panel.date <= end)]
    by_date = {d: x for d, x in p.groupby("date")}
    px = {(r.code, r.date): r.close for r in p.itertuples()}
    dates = sorted(by_date)
    amv_idx = amv.set_index("date")
    signals = set(amv[(amv.signal) & (amv.date >= start) & (amv.date <= end)].date)

    trades: list[dict] = []
    i = 0
    while i < len(dates):
        day = dates[i]
        if day not in signals:
            i += 1
            continue
        cand = by_date[day].dropna(subset=[flow_key, "ret2", "close"])
        if len(cand) < 2:
            i += 1
            continue
        top = cand.nlargest(2, flow_key)
        a, b = top.iloc[0], top.iloc[1]
        hi, lo = (a, b) if a.ret2 >= b.ret2 else (b, a)
        legs = [(hi, 0.40), (lo, 0.60)]        # 涨得多的 40%,少的 60%

        remain = {leg.code: w for leg, w in legs}
        entry_px = {leg.code: leg.close for leg, _ in legs}
        exits: list[tuple] = []
        state = 0
        j = i + 1
        while j < len(dates) and any(v > 1e-9 for v in remain.values()):
            dd = dates[j]
            if dd in amv_idx.index and bool(amv_idx.loc[dd, "is_yin"]):
                frac = _cut_fraction(strategy, float(amv_idx.loc[dd, "drop_pct"]), state)
                for code in list(remain):
                    if remain[code] <= 1e-9 or (code, dd) not in px:
                        continue
                    size = remain[code] if frac >= 1.0 else remain[code] * frac
                    exits.append((code, dd, size, px[(code, dd)]))
                    remain[code] -= size
                state += 1
            j += 1
        for code in list(remain):                      # 数据末尾仍未平仓
            if remain[code] > 1e-9:
                tail = [d for d in dates[i + 1:] if (code, d) in px]
                if tail:
                    exits.append((code, tail[-1], remain[code], px[(code, tail[-1])]))
                    remain[code] = 0

        for leg, weight in legs:
            legs_out = [e for e in exits if e[0] == leg.code]
            if not legs_out:
                continue
            pnl = sum(sz * (q / entry_px[leg.code] - 1) for _, _, sz, q in legs_out) / weight * 100
            exit_day = max(e[1] for e in legs_out)
            trades.append({
                "entry": day, "exit": exit_day, "hold": (exit_day - day).days,
                "code": leg.code, "name": leg["name"], "sector": leg.sector,
                "weight": weight, "entry_px": float(leg.close),
                "ret2_at_entry": float(leg.ret2), "pnl": pnl,
            })
        i = dates.index(max(e[1] for e in exits)) + 1 if exits else i + 1

    t = pd.DataFrame(trades)
    if t.empty:
        return BacktestResult(t, pd.DataFrame())
    t["contrib"] = t.pnl * t.weight
    rounds = []
    for entry, grp in t.groupby("entry"):
        hi = grp[grp.weight == 0.40]
        lo = grp[grp.weight == 0.60]
        if hi.empty or lo.empty:
            continue
        hi, lo = hi.iloc[0], lo.iloc[0]
        rounds.append({
            "entry": entry, "exit": grp.exit.max(), "hold": int(grp.hold.max()),
            "hi_name": hi["name"], "hi_pnl": hi.pnl, "lo_name": lo["name"], "lo_pnl": lo.pnl,
            "pnl": hi.pnl * 0.40 + lo.pnl * 0.60,
        })
    r = pd.DataFrame(rounds).sort_values("entry").reset_index(drop=True)
    r["equity"] = (1 + r.pnl / 100).cumprod()
    return BacktestResult(t, r)


def summarize(res: BacktestResult) -> dict:
    r = res.rounds
    if r.empty:
        return {}
    cum = (r.equity.iloc[-1] - 1) * 100
    years = max((r.entry.iloc[-1] - r.entry.iloc[0]).days / 365.25, 1e-9)
    peak = r.equity.cummax()
    return {
        "rounds": int(len(r)),
        "mean": float(r.pnl.mean()),
        "median": float(r.pnl.median()),
        "win_rate": float((r.pnl > 0).mean() * 100),
        "cumulative": float(cum),
        "annualized": float(((1 + cum / 100) ** (1 / years) - 1) * 100),
        "best": float(r.pnl.max()),
        "worst": float(r.pnl.min()),
        "avg_hold": float(r.hold.mean()),
        "days_in_market": int(r.hold.sum()),
        "max_drawdown": float(((r.equity / peak - 1).min()) * 100),
    }
