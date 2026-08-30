"""导入指南针 0AMV(活跃市值指数)日线序列。

用法(三选一):
    # 1) 从文件导入(指南针导出的 txt/csv,或你自己整理的表格)
    .venv/bin/python scripts/import_amv_index.py path/to/0amv.txt

    # 2) 从剪贴板/标准输入粘贴,粘完按 Ctrl-D
    pbpaste | .venv/bin/python scripts/import_amv_index.py -
    .venv/bin/python scripts/import_amv_index.py -

支持的行格式(自动识别分隔符 逗号/制表符/空格):
    2026-08-28,191043.0,195661.6,189214.9,189298.4     # 日期,开,高,低,收
    2026-08-28 189298.4                                # 日期 收
表头行、说明行会被自动跳过。重复日期以后出现的为准。
导入后会自动与客户端截图锚点核对,确认口径一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant.amv_index import AmvIndexStore, check_anchors, parse_text, two_day_signal  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    arg = sys.argv[1]
    if arg == "-":
        print("请粘贴 0AMV 数据，结束后按 Ctrl-D：", file=sys.stderr)
        text = sys.stdin.read()
    else:
        path = Path(arg)
        if not path.exists():
            print(f"文件不存在: {path}")
            return 1
        text = path.read_text(encoding="utf-8", errors="ignore")

    try:
        frame = parse_text(text)
    except ValueError as exc:
        print(f"解析失败: {exc}")
        return 1

    store = AmvIndexStore(ROOT / "data" / "amv_index" / "0amv.json")
    info = store.merge(frame, source=arg)
    print(f"导入成功: 本次 {len(frame)} 行 → 库中共 {info['rows']} 行 ({info['start']} ~ {info['end']})")
    print(f"字段: {[c for c in frame.columns if c != 'date']}")

    print("\n与客户端截图锚点核对:")
    for r in check_anchors(store.load()):
        mark = {"吻合": "✓", "不符": "✗", "缺失": "—"}[r["status"]]
        print(f"  {mark} {r['date']}  {r['status']}  {r['detail']}")

    full = store.load()
    if "close" in full and len(full) > 2:
        sig = two_day_signal(full)
        n = int(sig["signal"].sum())
        print(f"\n两日累计涨幅 ≥4% 的信号: {n} 次 / {len(sig)} 个交易日 ({n/len(sig)*100:.1f}%)")
        if n:
            recent = sig[sig["signal"] == 1].tail(5)
            print("  最近 5 次:", ", ".join(f"{r.date}({r.ret2:+.1f}%)" for r in recent.itertuples()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
