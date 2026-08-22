"""收盘后归档当日龙虎榜:拉取、行业归类并落盘 data/lhb_cache/{YYYYMMDD}.json。

归档后网页面板直接读磁盘,不再重复请求东财;配合 launchd 每个工作日 18:30 自动跑。

用法:
    .venv/bin/python scripts/run_lhb_archive.py              # 归档今天(无数据自动跳过)
    .venv/bin/python scripts/run_lhb_archive.py 2026-07-10   # 补归档指定日期
    .venv/bin/python scripts/run_lhb_archive.py 2026-07-01 2026-07-10  # 回补区间
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant.datasource.akshare_source import AkshareSource
from quant.web.lhb_service import LhbService, parse_date_param

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    d = None
    end = None
    if len(sys.argv) > 1:
        try:
            d = parse_date_param(sys.argv[1])
            if len(sys.argv) > 2:
                end = parse_date_param(sys.argv[2])
        except ValueError as e:
            print(f"日期参数错误: {e}")
            return 2
    if len(sys.argv) > 3:
        print("参数过多:最多支持 起始日期 结束日期")
        return 2
    if d is not None and end is not None and end < d:
        print("日期参数错误:结束日期不能早于起始日期")
        return 2
    svc = LhbService(AkshareSource(), cache_dir=ROOT / "data" / "lhb_cache", async_fill=False)
    if d is not None and end is not None:
        ok = 0
        cur = d
        while cur <= end:
            try:
                payload = svc.archive_day(cur)
            except Exception as e:
                print(f"{cur.isoformat()} 归档失败:{e}")
                cur += timedelta(days=1)
                continue
            s = payload["summary"]
            if s["stocks"]:
                ok += 1
                print(
                    f"已归档 {payload['date']}: 上榜 {s['stocks']} 只,机构参与 {s['org_stocks']} 只,"
                    f"机构净买入 {s['org_net'] / 1e8:.2f} 亿,待归类 {payload.get('industry_pending', 0)} 只"
                )
            else:
                print(f"{payload['date']} 无龙虎榜数据,跳过")
            cur += timedelta(days=1)
        print(f"区间归档完成:有效交易日 {ok} 天")
        return 0
    try:
        payload = svc.archive_day(d)
    except Exception as e:
        print(f"归档失败(网络/接口异常): {e}")
        return 1
    s = payload["summary"]
    if s["stocks"] == 0:
        print(f"{payload['date']} 无龙虎榜数据(非交易日或尚未披露),跳过落盘")
        return 0
    print(
        f"已归档 {payload['date']}: 上榜 {s['stocks']} 只,机构参与 {s['org_stocks']} 只,"
        f"机构净买入 {s['org_net'] / 1e8:.2f} 亿,待归类 {payload.get('industry_pending', 0)} 只"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
