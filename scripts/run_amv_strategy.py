"""跑 0AMV 板块轮动策略回测,结果写入 data/amv_strategy/results.json。

    .venv/bin/python scripts/run_amv_strategy.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant.amv_strategy import (  # noqa: E402
    FLOW_KEYS, STRATEGIES, backtest, load_amv, prepare_panel, summarize,
)

OUT = ROOT / "data" / "amv_strategy"


def main() -> int:
    con = sqlite3.connect(ROOT / "market.sqlite")
    amv = load_amv(con)
    panel = prepare_panel(pd.read_csv(OUT / "etf_panel.csv"))
    print(f"0AMV {len(amv)} 根 ({amv.date.min().date()}~{amv.date.max().date()})，"
          f"ETF 面板 {panel.code.nunique()} 只 / {len(panel)} 行")

    payload = {"strategies": {}, "sensitivity": [], "flow_keys": FLOW_KEYS,
               "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")}

    for s in STRATEGIES:
        res = backtest(amv, panel, s)
        stat = summarize(res)
        r = res.rounds.copy()
        r["entry"] = r.entry.dt.strftime("%Y-%m-%d")
        r["exit"] = r.exit.dt.strftime("%Y-%m-%d")
        yearly = (res.rounds.assign(yr=res.rounds.entry.dt.year)
                  .groupby("yr").agg(rounds=("pnl", "size"), mean=("pnl", "mean"),
                                     win=("pnl", lambda x: (x > 0).mean() * 100),
                                     cum=("pnl", lambda x: ((1 + x / 100).prod() - 1) * 100)))
        legs = (res.trades.groupby("name")
                .agg(n=("pnl", "size"), mean=("pnl", "mean"),
                     win=("pnl", lambda x: (x > 0).mean() * 100), contrib=("contrib", "sum"))
                .sort_values("contrib").reset_index())
        payload["strategies"][s] = {
            "summary": stat,
            "rounds": json.loads(r.to_json(orient="records", force_ascii=False)),
            "yearly": json.loads(yearly.reset_index().to_json(orient="records", force_ascii=False)),
            "legs": json.loads(legs.to_json(orient="records", force_ascii=False)),
        }
        print(f"  {s}: {stat['rounds']} 轮  累计 {stat['cumulative']:+.1f}%  "
              f"年化 {stat['annualized']:+.1f}%  胜率 {stat['win_rate']:.1f}%")

    for key, label in FLOW_KEYS.items():
        for s in STRATEGIES:
            st = summarize(backtest(amv, panel, s, flow_key=key))
            payload["sensitivity"].append({"flow": label, "flow_key": key, "strategy": s, **st})

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"\n已写入 {OUT/'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
