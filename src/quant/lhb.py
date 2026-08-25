"""龙虎榜机构资金:归一化东财龙虎榜数据、合并机构买卖统计、按行业板块聚合。

数据口径说明:
- 「龙虎榜详情」覆盖当日全部上榜股;「机构买卖统计」仅覆盖买卖席位中出现
  "机构专用" 的上榜股。两者按代码合并,后者缺失时机构金额记 0 且 has_org=False。
- 机构买入/卖出额是交易所披露的机构专用席位真实成交(置信度高),
  但仅覆盖上榜股票,不代表全市场机构行为。
- 同一股票同日可能因多个上榜原因出现多行,金额口径不同(当日榜/3日榜)不可直接
  相加,归一化时保留成交额最大的一行作主数据,其余原因合并进 reasons 列表。

本模块为纯函数,不做网络请求,可离线单测;原始 DataFrame 由数据源层注入。
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

# 列名按关键词模糊匹配,兼容 akshare 版本间的列名漂移。
_DETAIL_COLS = {
    "code": ("代码",),
    "name": ("名称",),
    "close": ("收盘价",),
    "change_pct": ("涨跌幅",),
    "lhb_net_buy": ("龙虎榜净买额",),
    "lhb_buy": ("龙虎榜买入额",),
    "lhb_sell": ("龙虎榜卖出额",),
    "lhb_amount": ("龙虎榜成交额",),
    "total_amount": ("市场总成交额",),
    "turnover": ("换手率",),
    "float_mv": ("流通市值",),
    "reason": ("上榜原因",),
    "interpretation": ("解读",),
    "after_1d": ("上榜后1日",),
    "after_5d": ("上榜后5日",),
}

_ORG_COLS = {
    "code": ("代码",),
    "org_buy_count": ("买方机构数",),
    "org_sell_count": ("卖方机构数",),
    "org_buy": ("机构买入总额",),
    "org_sell": ("机构卖出总额",),
    "org_net": ("机构买入净额",),
    "org_net_ratio": ("机构净买额占总成交额比", "占总成交额比"),
}

_TEXT_FIELDS = {"code", "name", "reason", "interpretation"}


def _find_col(columns: list[str], keywords: tuple[str, ...]) -> str | None:
    """在实际列名中找包含任一关键词的列(先精确后包含)。"""
    for kw in keywords:
        if kw in columns:
            return kw
    for kw in keywords:
        for col in columns:
            if kw in str(col):
                return col
    return None


def _num(v: Any) -> float | None:
    """转 float;NaN/无法解析返回 None。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _pick(raw: pd.DataFrame, spec: dict[str, tuple[str, ...]]) -> pd.DataFrame:
    """按 spec 从原始中文列抽取并重命名;缺失列填 None。"""
    cols = list(raw.columns)
    out = pd.DataFrame(index=raw.index)
    for key, keywords in spec.items():
        col = _find_col(cols, keywords)
        out[key] = raw[col] if col is not None else None
    if "code" in out.columns:
        out["code"] = out["code"].astype(str).str.zfill(6)
    return out


def normalize_lhb_detail(raw: pd.DataFrame) -> pd.DataFrame:
    """龙虎榜详情(东财 stock_lhb_detail_em)→ 标准英文 schema。"""
    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=list(_DETAIL_COLS))
    return _pick(raw, _DETAIL_COLS)


def normalize_lhb_org(raw: pd.DataFrame) -> pd.DataFrame:
    """机构买卖每日统计(东财 stock_lhb_jgmmtj_em)→ 标准英文 schema。"""
    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=list(_ORG_COLS))
    return _pick(raw, _ORG_COLS)


def _row_to_dict(row: pd.Series) -> dict:
    out: dict[str, Any] = {}
    for key, val in row.items():
        if key in _TEXT_FIELDS:
            out[key] = "" if val is None or (isinstance(val, float) and math.isnan(val)) else str(val)
        else:
            out[key] = _num(val)
    return out


def merge_lhb(detail: pd.DataFrame, org: pd.DataFrame) -> list[dict]:
    """按代码合并详情与机构统计,同代码多行去重(保留成交额最大行,原因合并)。"""
    if len(detail) == 0:
        return []
    org_map: dict[str, dict] = {}
    if len(org):
        o = org.copy()
        o["_scale"] = o["org_buy"].map(_num).fillna(0) + o["org_sell"].map(_num).fillna(0)
        o = o.sort_values("_scale").drop_duplicates("code", keep="last")
        for r in o.itertuples():
            org_map[str(r.code)] = {
                "org_buy_count": _num(r.org_buy_count) or 0,
                "org_sell_count": _num(r.org_sell_count) or 0,
                "org_buy": _num(r.org_buy) or 0.0,
                "org_sell": _num(r.org_sell) or 0.0,
                "org_net": _num(r.org_net),
                "org_net_ratio": _num(r.org_net_ratio),
            }

    stocks: list[dict] = []
    for code, grp in detail.groupby("code", sort=False):
        g = grp.copy()
        g["_amt"] = g["lhb_amount"].map(_num).fillna(0)
        main = _row_to_dict(g.sort_values("_amt").iloc[-1].drop("_amt"))
        reasons = [str(r) for r in grp["reason"] if r is not None and str(r) not in ("", "nan")]
        main["reasons"] = list(dict.fromkeys(reasons))  # 去重保序
        main.pop("reason", None)
        info = org_map.get(str(code))
        if info is not None:
            main.update(info)
            if main.get("org_net") is None:
                main["org_net"] = (main["org_buy"] or 0.0) - (main["org_sell"] or 0.0)
            main["has_org"] = True
        else:
            main.update(
                {
                    "org_buy_count": 0,
                    "org_sell_count": 0,
                    "org_buy": 0.0,
                    "org_sell": 0.0,
                    "org_net": 0.0,
                    "org_net_ratio": None,
                    "has_org": False,
                }
            )
        stocks.append(main)
    stocks.sort(key=lambda s: s.get("org_net") or 0.0, reverse=True)
    return stocks


def group_by_industry(stocks: list[dict]) -> list[dict]:
    """按 industry 字段聚合板块机构资金(股票需先注入 industry)。"""
    sectors: dict[str, dict] = {}
    for s in stocks:
        ind = s.get("industry") or "未分类"
        sec = sectors.setdefault(
            ind,
            {
                "industry": ind,
                "count": 0,
                "org_count": 0,
                "org_buy": 0.0,
                "org_sell": 0.0,
                "org_net": 0.0,
                "net_buy_count": 0,
                "net_sell_count": 0,
                "codes": [],
            },
        )
        sec["count"] += 1
        sec["codes"].append(s["code"])
        if s.get("has_org"):
            sec["org_count"] += 1
            sec["org_buy"] += s.get("org_buy") or 0.0
            sec["org_sell"] += s.get("org_sell") or 0.0
            net = s.get("org_net") or 0.0
            sec["org_net"] += net
            if net > 0:
                sec["net_buy_count"] += 1
            elif net < 0:
                sec["net_sell_count"] += 1
    return sorted(sectors.values(), key=lambda x: x["org_net"], reverse=True)


def build_payload(
    detail_raw: pd.DataFrame,
    org_raw: pd.DataFrame,
    industry_map: dict[str, str],
    date_str: str,
) -> dict:
    """组装面板 JSON:个股列表 + 板块聚合 + 汇总指标。"""
    stocks = merge_lhb(normalize_lhb_detail(detail_raw), normalize_lhb_org(org_raw))
    for s in stocks:
        s["industry"] = industry_map.get(s["code"]) or "未分类"
    sectors = group_by_industry(stocks)
    org_stocks = [s for s in stocks if s["has_org"]]
    summary = {
        "stocks": len(stocks),
        "org_stocks": len(org_stocks),
        "org_buy": sum(s["org_buy"] for s in org_stocks),
        "org_sell": sum(s["org_sell"] for s in org_stocks),
        "org_net": sum(s["org_net"] or 0.0 for s in org_stocks),
        "net_buy_count": sum(1 for s in org_stocks if (s["org_net"] or 0) > 0),
        "net_sell_count": sum(1 for s in org_stocks if (s["org_net"] or 0) < 0),
    }
    return {"date": date_str, "summary": summary, "sectors": sectors, "stocks": stocks}
