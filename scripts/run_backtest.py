"""回测入口:对某只股票、某条盯盘规则做历史回测,打印绩效。

用法:
    .venv/bin/python scripts/run_backtest.py CODE RULE [--forward N] [--days D] [--cost C]
示例:
    .venv/bin/python scripts/run_backtest.py 000001 ma_cross --forward 5 --days 365
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant.backtest import DEFAULT_COST, backtest
from quant.config import load_config
from quant.datasource.akshare_source import AkshareSource
from quant.signals.backtest_rules import BACKTEST_ENTRY_TIMING, BACKTEST_ONLY_RULES
from quant.signals.monitor_rules import MONITOR_RULES

ROOT = Path(__file__).resolve().parents[1]
RULES_BY_NAME = {r.__name__: r for r in [*MONITOR_RULES, *BACKTEST_ONLY_RULES]}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("code")
    p.add_argument("rule", choices=list(RULES_BY_NAME))
    p.add_argument("--forward", type=int, default=5)
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--cost", type=float, default=DEFAULT_COST)
    args = p.parse_args()

    cfg_path = ROOT / "config" / "config.toml"
    cfg_path = cfg_path if cfg_path.exists() else ROOT / "config" / "config.example.toml"
    rules_cfg = load_config(cfg_path).rules

    source = AkshareSource()
    end = date.today()
    start = end - timedelta(days=args.days)
    daily = source.get_daily_bars(
        args.code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    )

    stats = backtest(
        daily,
        RULES_BY_NAME[args.rule],
        rules_cfg,
        forward=args.forward,
        cost=args.cost,
        entry_timing=BACKTEST_ENTRY_TIMING.get(args.rule, "next_open"),
    )
    print(f"{args.code} / {args.rule} / forward={args.forward} / cost={args.cost}")
    print(f"  触发笔数: {stats.trades}")
    print(f"  胜率:     {stats.win_rate:.1%}")
    print(f"  平均收益: {stats.avg_return:.2%}")


if __name__ == "__main__":
    main()
