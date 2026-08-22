"""龙虎榜纯函数逻辑测试:归一化、合并去重、板块聚合、payload 组装(离线 fixture)。"""

import pandas as pd

from quant.lhb import (
    build_payload,
    group_by_industry,
    merge_lhb,
    normalize_lhb_detail,
    normalize_lhb_org,
)


def _detail_raw():
    """模拟 ak.stock_lhb_detail_em 返回(中文列;600001 同日两个榜单)。"""
    return pd.DataFrame(
        {
            "序号": [1, 2, 3],
            "代码": ["600001", "600001", "300002"],
            "名称": ["甲股份", "甲股份", "乙科技"],
            "上榜日": ["2026-07-10"] * 3,
            "解读": ["主力做多", "主力做多", "机构抢筹"],
            "收盘价": [10.5, 10.5, 55.2],
            "涨跌幅": [9.98, 9.98, -5.2],
            "龙虎榜净买额": [5e7, 2e7, -3e7],
            "龙虎榜买入额": [9e7, 3e7, 4e7],
            "龙虎榜卖出额": [4e7, 1e7, 7e7],
            "龙虎榜成交额": [1.3e8, 4e7, 1.1e8],
            "市场总成交额": [9e8, 9e8, 6e8],
            "换手率": [12.3, 12.3, 8.8],
            "流通市值": [80e8, 80e8, 120e8],
            "上榜原因": ["日涨幅偏离值达7%", "连续三个交易日涨幅偏离20%", "日跌幅偏离值达7%"],
            "上榜后1日": [1.2, 1.2, None],
            "上榜后5日": [None, None, None],
        }
    )


def _org_raw():
    """模拟 ak.stock_lhb_jgmmtj_em 返回(仅 600001 有机构参与)。"""
    return pd.DataFrame(
        {
            "序号": [1],
            "名称": ["甲股份"],
            "代码": ["600001"],
            "上榜日期": ["2026-07-10"],
            "买方机构数": [3],
            "卖方机构数": [1],
            "机构买入总额": [6e7],
            "机构卖出总额": [1e7],
            "机构买入净额": [5e7],
            "市场总成交额": [9e8],
            "机构净买额占总成交额比": [5.56],
        }
    )


def test_normalize_detail_maps_columns():
    df = normalize_lhb_detail(_detail_raw())
    assert list(df["code"]) == ["600001", "600001", "300002"]
    assert df.iloc[0]["lhb_net_buy"] == 5e7
    assert df.iloc[2]["change_pct"] == -5.2


def test_normalize_handles_empty_and_missing_cols():
    assert len(normalize_lhb_detail(pd.DataFrame())) == 0
    assert len(normalize_lhb_org(None)) == 0
    # 缺列不抛错,填 None
    df = normalize_lhb_detail(pd.DataFrame({"代码": ["000001"], "名称": ["平安银行"]}))
    assert df.iloc[0]["close"] is None


def test_normalize_fuzzy_column_match():
    """列名前后缀漂移(akshare 版本差异)仍能匹配。"""
    raw = _org_raw().rename(columns={"机构净买额占总成交额比": "机构净买额占总成交额比(%)"})
    df = normalize_lhb_org(raw)
    assert df.iloc[0]["org_net_ratio"] == 5.56


def test_merge_dedups_and_joins_org():
    stocks = merge_lhb(normalize_lhb_detail(_detail_raw()), normalize_lhb_org(_org_raw()))
    assert len(stocks) == 2  # 600001 两行合并为一
    a = next(s for s in stocks if s["code"] == "600001")
    b = next(s for s in stocks if s["code"] == "300002")
    assert a["has_org"] and a["org_net"] == 5e7 and a["org_buy_count"] == 3
    assert len(a["reasons"]) == 2
    assert a["lhb_amount"] == 1.3e8  # 保留成交额最大的主榜单
    assert not b["has_org"] and b["org_net"] == 0.0
    # 机构净买额缺失时用 买-卖 兜底
    org = _org_raw().drop(columns=["机构买入净额"])
    stocks2 = merge_lhb(normalize_lhb_detail(_detail_raw()), normalize_lhb_org(org))
    a2 = next(s for s in stocks2 if s["code"] == "600001")
    assert a2["org_net"] == 5e7


def test_group_by_industry():
    stocks = merge_lhb(normalize_lhb_detail(_detail_raw()), normalize_lhb_org(_org_raw()))
    for s in stocks:
        s["industry"] = {"600001": "银行", "300002": "半导体"}.get(s["code"], "未分类")
    sectors = group_by_industry(stocks)
    bank = next(x for x in sectors if x["industry"] == "银行")
    semi = next(x for x in sectors if x["industry"] == "半导体")
    assert bank["org_net"] == 5e7 and bank["net_buy_count"] == 1 and bank["org_count"] == 1
    assert semi["org_net"] == 0.0 and semi["count"] == 1 and semi["org_count"] == 0
    assert sectors[0]["industry"] == "银行"  # 按净额降序


def test_build_payload_summary():
    payload = build_payload(_detail_raw(), _org_raw(), {"600001": "银行"}, "2026-07-10")
    s = payload["summary"]
    assert payload["date"] == "2026-07-10"
    assert s["stocks"] == 2 and s["org_stocks"] == 1
    assert s["org_buy"] == 6e7 and s["org_sell"] == 1e7 and s["org_net"] == 5e7
    assert s["net_buy_count"] == 1 and s["net_sell_count"] == 0
    stock = next(x for x in payload["stocks"] if x["code"] == "300002")
    assert stock["industry"] == "未分类"
    # JSON 可序列化(无 NaN/numpy 类型残留)
    import json

    json.dumps(payload, allow_nan=False)
