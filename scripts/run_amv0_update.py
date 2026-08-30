"""收盘后刷新 0AMV(无穷成本均线)缓存。

由 launchd 每个交易日 15:35 触发,也可手动执行:

    .venv/bin/python scripts/run_amv0_update.py [--force]
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant.web.amv0_service import Amv0Service  # noqa: E402


def main() -> int:
    force = "--force" in sys.argv
    service = Amv0Service(cache_dir=ROOT / "data" / "amv0_cache")
    started = datetime.now()
    print(f"[{started:%Y-%m-%d %H:%M:%S}] 开始刷新 0AMV 缓存 (force={force})", flush=True)

    result = service.refresh(force=force)
    if result.get("skipped"):
        print(f"缓存已是最新 ({result.get('session_date')}), 跳过。", flush=True)
        return 0

    elapsed = (datetime.now() - started).total_seconds()
    print(
        f"完成: 交易日 {result['session_date']}, 成功 {len(result['ok'])} 只, "
        f"失败 {len(result['failed'])} 只, 耗时 {elapsed:.1f}s",
        flush=True,
    )
    for item in result["failed"]:
        print(f"  失败 {item['code']} {item['name']}: {item['error']}", flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
