"""选股入口:从科创板+创业板按所选方案筛选股票,输出入选名单。

复用 screener.run_full_screen(定时任务与面板按钮同一套逻辑)。
用法:
    .venv/bin/python scripts/run_screen.py [--config 路径] [--strategy 方案id]
默认方案取 config 的 [screen].strategy,缺省 zhixing。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant.config import load_config
from quant.datasource.akshare_source import AkshareSource
from quant.screener import SCOPES, run_full_screen
from quant.strategies import DEFAULT_STRATEGY, STRATEGIES

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "config" / "watchlist.generated.toml"
CHECKPOINT = ROOT / "data" / "screen.checkpoint.json"
HISTORY = ROOT / "data" / "screen_history.jsonl"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=None)
    p.add_argument("--strategy", default=None, choices=list(STRATEGIES))
    p.add_argument("--scope", default=None, choices=list(SCOPES))
    args = p.parse_args()

    cfg_path = Path(args.config) if args.config else ROOT / "config" / "config.toml"
    if not cfg_path.exists():
        cfg_path = ROOT / "config" / "config.example.toml"
    cfg = load_config(cfg_path)

    strategy = args.strategy or cfg.screen.get("strategy", DEFAULT_STRATEGY)
    print(f"开始选股(方案={strategy}:{STRATEGIES[strategy]['label']})…")
    r = run_full_screen(
        AkshareSource(),
        cfg.screen,
        GENERATED,
        strategy=strategy,
        scope=args.scope,
        checkpoint_path=CHECKPOINT,
        history_path=HISTORY,
    )
    print(f"入选 {r['count']} 只(上限 {r['top_n']},股票池 {r['universe']}),用时 {r['elapsed']}s:")
    for item in r["selected"]:
        print(f"  {item['code']} {item['name']}")
    print(f"已写入 {GENERATED}")
    print(f"历史已追加 {HISTORY}")


if __name__ == "__main__":
    main()
