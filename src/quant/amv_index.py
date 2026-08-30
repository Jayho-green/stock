"""指南针活跃市值指数(0AMV)的本地存储与导入。

0AMV 是指南针的专利合成指数(陈浩与指南针创始人共同发明),只存在于指南针客户端
与天狼50 内部,任何公开数据源都没有,公式也从未披露。因此本模块不做抓取,
只负责接收用户从客户端导出/粘贴的序列,并提供统一读取接口供策略回测使用。

已知锚点(用户 2026-08-28 客户端截图,用于校验导入数据是否对得上):
    2026-08-28  开 191043.0  高 195661.6  低 189214.9  收 189298.4  昨收 191409.0
单位为亿元(189298.4 亿元 ≈ 18.9 万亿),与当日全市场成交额 2.10 万亿同一数量级。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

# 客户端截图读到的校验锚点:date -> 收盘价(其余字段可选)
ANCHORS: dict[str, dict[str, float]] = {
    "2026-08-28": {"open": 191043.0, "high": 195661.6, "low": 189214.9, "close": 189298.4},
}

_NUM = r"[-+]?\d[\d,]*\.?\d*"
_DATE_PATTERNS = ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%y-%m-%d", "%y/%m/%d")


def _parse_date(token: str) -> str | None:
    token = token.strip()
    for fmt in _DATE_PATTERNS:
        try:
            return datetime.strptime(token, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_text(text: str) -> pd.DataFrame:
    """宽容解析:支持 CSV、制表符、空格分隔;识别 日期 + 1~5 个数值列。

    每行至少需要 日期 与 收盘价。列数含义:
        1 个数值            -> close
        4 个数值            -> open, high, low, close
        >=5 个数值          -> open, high, low, close, volume
    """

    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p for p in re.split(r"[,\t;\s]+", line) if p]
        if not parts:
            continue
        day = _parse_date(parts[0])
        if day is None:
            continue                                   # 表头或说明行
        nums = []
        for p in parts[1:]:
            if re.fullmatch(_NUM, p):
                nums.append(float(p.replace(",", "")))
        if not nums:
            continue
        if len(nums) >= 4:
            row = {"date": day, "open": nums[0], "high": nums[1], "low": nums[2], "close": nums[3]}
            if len(nums) >= 5:
                row["volume"] = nums[4]
        else:
            row = {"date": day, "close": nums[-1]}
        rows.append(row)

    if not rows:
        raise ValueError("未能从文本中解析出任何 [日期 + 数值] 行")
    frame = pd.DataFrame(rows).drop_duplicates(subset="date", keep="last")
    return frame.sort_values("date").reset_index(drop=True)


def check_anchors(frame: pd.DataFrame, tol: float = 0.005) -> list[dict]:
    """把导入数据与客户端截图锚点比对,确认口径一致(默认容差 0.5%)。"""

    indexed = frame.set_index("date")
    results = []
    for day, expect in ANCHORS.items():
        if day not in indexed.index:
            results.append({"date": day, "status": "缺失", "detail": "导入数据不含该交易日"})
            continue
        got = indexed.loc[day]
        bad = []
        for field, want in expect.items():
            if field not in got or pd.isna(got[field]):
                continue
            diff = abs(float(got[field]) - want) / want
            if diff > tol:
                bad.append(f"{field} 实际{float(got[field]):.1f} 期望{want:.1f} 差{diff*100:.2f}%")
        results.append({
            "date": day,
            "status": "不符" if bad else "吻合",
            "detail": "; ".join(bad) if bad else f"收盘 {float(got.get('close', float('nan'))):.1f}",
        })
    return results


class AmvIndexStore:
    """0AMV 日线序列的本地存储。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def save(self, frame: pd.DataFrame, source: str = "manual") -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": source,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rows": json.loads(frame.to_json(orient="records", force_ascii=False)),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        return {"rows": len(frame), "start": frame["date"].iloc[0], "end": frame["date"].iloc[-1]}

    def load(self) -> pd.DataFrame | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        frame = pd.DataFrame(payload.get("rows", []))
        if frame.empty:
            return None
        return frame.sort_values("date").reset_index(drop=True)

    def merge(self, frame: pd.DataFrame, source: str = "manual") -> dict:
        """增量合并,新数据覆盖同日旧数据。"""

        old = self.load()
        merged = frame if old is None else (
            pd.concat([old, frame], ignore_index=True)
            .drop_duplicates(subset="date", keep="last")
            .sort_values("date")
            .reset_index(drop=True)
        )
        return self.save(merged, source=source)


def two_day_signal(frame: pd.DataFrame, threshold: float = 4.0) -> pd.DataFrame:
    """用户策略的信号列:0AMV 两日累计涨幅 >= threshold%。"""

    out = frame.copy()
    out["ret1"] = out["close"].pct_change() * 100
    out["ret2"] = (out["close"] / out["close"].shift(2) - 1) * 100
    out["signal"] = (out["ret2"] >= threshold).astype(int)
    return out
