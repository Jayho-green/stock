"""每日收盘后采集同花顺行业资金流快照。

同花顺没有历史接口,只能每日落盘累积 —— 漏一天就少一天样本。
由 launchd 每个工作日 15:20 触发(同花顺资金流盘中即可定型,收盘前采集更稳)。

    .venv/bin/python scripts/run_sector_flow_snapshot.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant.sector_flow import SectorFlowStore  # noqa: E402


def main() -> int:
    store = SectorFlowStore(ROOT / "data" / "sector_flow")
    now = datetime.now()
    if now.weekday() >= 5 and "--force" not in sys.argv:
        print(f"[{now:%Y-%m-%d %H:%M:%S}] 周末休市,跳过采集。")
        return 0

    day = now.strftime("%Y-%m-%d")
    if store.has(day) and "--force" not in sys.argv:
        print(f"[{now:%Y-%m-%d %H:%M:%S}] {day} 已采集过,跳过。")
        return 0

    payload = store.snapshot()
    top = payload["top2"]
    print(f"[{payload['captured_at']}] 已落盘 {day}，累计 {len(store.days())} 个交易日")
    if top:
        for i, t in enumerate(top, 1):
            print(f"  资金流入 TOP{i}: {t['industry']}  净额 {t['net']:+.2f}亿  -> ETF {'/'.join(t['etfs'])}")
    for err in payload["errors"]:
        print(f"  警告: {err}")
    return 0 if not payload["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
