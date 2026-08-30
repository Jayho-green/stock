"""同花顺行业资金流采集与 ETF 映射。

同花顺只提供快照(即时/3日/5日/10日排行),没有历史接口,因此必须每日落盘累积。
"即时" 为当日净额;3/5/10 日排行为区间累计,可在漏采时做有限回补参考。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pandas as pd

PERIODS = ("即时", "3日排行", "5日排行", "10日排行")

# 同花顺行业 -> 本项目 ETF 代码。一个行业可映射多只,取先出现者为主。
INDUSTRY_TO_ETF: dict[str, list[str]] = {
    # 农林牧渔
    "种植业与林业": ["159825"], "农产品加工": ["159825"], "农化制品": ["159825"],
    "养殖业": ["159825"],
    # 科技 / 半导体
    "半导体": ["512480", "159995"], "元件": ["512480"], "电子化学品": ["512480"],
    "光学光电子": ["512480"], "其他电子": ["512480"], "消费电子": ["512480"],
    "软件开发": ["512720"], "计算机设备": ["512720"],
    "通信设备": ["515050"], "通信服务": ["515050"],
    # 新能源
    "光伏设备": ["515790"], "电池": ["515030", "516160"], "风电设备": ["516160"],
    "其他电源设备": ["516160"], "电网设备": ["159611"], "电力": ["159611"],
    "能源金属": ["516160"],
    # 金融
    "证券": ["512880", "512070"], "银行": ["512800"], "多元金融": ["512070"],
    # 周期 / 资源
    "煤炭开采加工": ["515220"], "贵金属": ["518880", "512400"], "工业金属": ["512400"],
    "小金属": ["512400"], "金属新材料": ["512400"], "钢铁": ["515210"],
    "房地产": ["512200"], "油气开采及服务": ["515220"], "石油加工贸易": ["515220"],
    # 医药
    "医疗器械": ["512170"], "医疗服务": ["512170"], "化学制药": ["159929", "512010"],
    "生物制品": ["159929"], "医药商业": ["512010"],
    # 消费
    "白酒": ["512690"], "饮料制造": ["512690"], "食品加工制造": ["159928"],
    "美容护理": ["159928"], "白色家电": ["159928"], "小家电": ["159928"],
    "汽车整车": ["516110"], "汽车零部件": ["516110"], "汽车服务及其他": ["516110"],
    # 传媒 / 军工
    "文化传媒": ["512980"], "影视院线": ["512980"], "游戏": ["516010"],
    "军工装备": ["512660"], "军工电子": ["512660"],
}


def fetch_snapshot(period: str = "即时") -> pd.DataFrame:
    """拉取一个周期的行业资金流排行。"""

    import akshare as ak

    frame = ak.stock_fund_flow_industry(symbol=period)
    if frame is None or frame.empty:
        raise RuntimeError(f"同花顺未返回 {period} 数据")
    out = frame.copy()
    out["净额"] = pd.to_numeric(out["净额"], errors="coerce")
    out["流入资金"] = pd.to_numeric(out.get("流入资金"), errors="coerce")
    out["流出资金"] = pd.to_numeric(out.get("流出资金"), errors="coerce")
    return out


def top_inflow(frame: pd.DataFrame, n: int = 2) -> list[dict]:
    """净额最高的 n 个行业,并附上可交易的对应 ETF。"""

    ranked = frame.dropna(subset=["净额"]).sort_values("净额", ascending=False)
    picks: list[dict] = []
    for _, row in ranked.iterrows():
        etfs = INDUSTRY_TO_ETF.get(str(row["行业"]), [])
        if not etfs:
            continue                       # 没有对应 ETF 的行业不可交易,跳过
        picks.append({
            "industry": str(row["行业"]),
            "net": float(row["净额"]),
            "etfs": etfs,
            "pct": float(row.get("行业-涨跌幅") or 0) if "行业-涨跌幅" in row else None,
        })
        if len(picks) >= n:
            break
    return picks


class SectorFlowStore:
    """按交易日落盘,每日一个文件。"""

    def __init__(self, root: Path | str, clock: Callable[[], datetime] = datetime.now):
        self.root = Path(root)
        self.clock = clock

    def _path(self, day: str) -> Path:
        return self.root / f"{day}.json"

    def has(self, day: str) -> bool:
        return self._path(day).exists()

    def write(self, day: str, payload: dict) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(day)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return path

    def read(self, day: str) -> dict | None:
        try:
            return json.loads(self._path(day).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    def days(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.stem for p in self.root.glob("*.json"))

    def snapshot(self, periods: tuple[str, ...] = PERIODS) -> dict:
        """采集当日全部周期并落盘。"""

        now = self.clock()
        day = now.strftime("%Y-%m-%d")
        data: dict[str, list] = {}
        errors: list[str] = []
        for period in periods:
            try:
                frame = fetch_snapshot(period)
                data[period] = json.loads(frame.to_json(orient="records", force_ascii=False))
            except Exception as exc:
                errors.append(f"{period}: {str(exc)[:120]}")

        instant = pd.DataFrame(data.get("即时", []))
        payload = {
            "day": day,
            "captured_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "periods": data,
            "top2": top_inflow(instant, 2) if not instant.empty else [],
            "errors": errors,
        }
        self.write(day, payload)
        return payload
