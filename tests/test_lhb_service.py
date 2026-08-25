"""LhbService 与 /api/lhb 接口测试:日期解析、回退、缓存、行业持久化。"""

import json
from datetime import datetime

import pandas as pd
import pytest

from quant.lhb import build_payload
from quant.web.lhb_service import LhbService, parse_date_param


def _detail(codes):
    return pd.DataFrame(
        {
            "代码": codes,
            "名称": [f"股{c[-2:]}" for c in codes],
            "收盘价": [10.0] * len(codes),
            "涨跌幅": [5.0] * len(codes),
            "龙虎榜净买额": [1e7] * len(codes),
            "龙虎榜买入额": [2e7] * len(codes),
            "龙虎榜卖出额": [1e7] * len(codes),
            "龙虎榜成交额": [3e7] * len(codes),
            "换手率": [5.0] * len(codes),
            "流通市值": [50e8] * len(codes),
            "上榜原因": ["日涨幅偏离值达7%"] * len(codes),
        }
    )


class FakeLhbSource:
    """20260710 有数据,其余日期为空;记录取数与行业查询次数。"""

    def __init__(self):
        self.detail_calls = []
        self.industry_calls = []

    def get_lhb_detail(self, start, end):
        self.detail_calls.append(start)
        return _detail(["600001", "300002"]) if start == "20260710" else pd.DataFrame()

    def get_lhb_org(self, start, end):
        if start != "20260710":
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "代码": ["600001"],
                "名称": ["股01"],
                "买方机构数": [2],
                "卖方机构数": [0],
                "机构买入总额": [3e7],
                "机构卖出总额": [0.0],
                "机构买入净额": [3e7],
                "机构净买额占总成交额比": [10.0],
            }
        )

    def get_stock_industry(self, code):
        self.industry_calls.append(code)
        return {"600001": "银行"}.get(code)


def _clock():
    return datetime(2026, 7, 12, 10, 0)  # 周日:自动模式应回退到 7-10(周五)


def test_parse_date_param():
    assert parse_date_param(None) is None
    assert parse_date_param("2026-07-10").isoformat() == "2026-07-10"
    assert parse_date_param("20260710").isoformat() == "2026-07-10"
    for bad in ("2026/07/10", "abc", "2099-01-01", "2026-13-01"):
        with pytest.raises(ValueError):
            parse_date_param(bad)


def test_auto_fallback_to_latest_disclosed(tmp_path):
    svc = LhbService(FakeLhbSource(), cache_dir=tmp_path, clock=_clock, async_fill=False)
    payload = svc.get_board(None)
    assert payload["date"] == "2026-07-10"
    assert payload["summary"]["stocks"] == 2
    assert payload["summary"]["org_net"] == 3e7


def test_explicit_date_and_disk_cache(tmp_path):
    src = FakeLhbSource()
    svc = LhbService(src, cache_dir=tmp_path, clock=_clock, async_fill=False)
    p1 = svc.get_board("2026-07-10")
    assert p1["summary"]["stocks"] == 2
    assert (tmp_path / "20260710.json").exists()
    # 新实例命中磁盘缓存,不再发起网络取数
    src2 = FakeLhbSource()
    svc2 = LhbService(src2, cache_dir=tmp_path, clock=_clock, async_fill=False)
    p2 = svc2.get_board("2026-07-10")
    assert p2["summary"]["stocks"] == 2
    assert src2.detail_calls == []


def test_industry_persisted_and_incremental(tmp_path):
    src = FakeLhbSource()
    svc = LhbService(src, cache_dir=tmp_path, clock=_clock, async_fill=False)
    p = svc.get_board("2026-07-10")
    saved = json.loads((tmp_path / "industry_map.json").read_text("utf-8"))
    assert saved["600001"] == "银行"
    a = next(s for s in p["stocks"] if s["code"] == "600001")
    b = next(s for s in p["stocks"] if s["code"] == "300002")
    assert a["industry"] == "银行"
    assert b["industry"] == "未分类"  # 查不到行业时兜底
    assert p["industry_pending"] == 1


def test_bulk_industry_map_preferred(tmp_path):
    """数据源提供 get_industry_map 时优先批量归类,不再逐只请求。"""

    class BulkSource(FakeLhbSource):
        def get_industry_map(self):
            return {"600001": "银行", "300002": "半导体"}

    src = BulkSource()
    svc = LhbService(src, cache_dir=tmp_path, clock=_clock, async_fill=False)
    p = svc.get_board("2026-07-10")
    inds = {s["code"]: s["industry"] for s in p["stocks"]}
    assert inds == {"600001": "银行", "300002": "半导体"}
    assert p["industry_pending"] == 0
    assert src.industry_calls == []  # 批量已覆盖,未走逐只兜底
    semi = next(x for x in p["sectors"] if x["industry"] == "半导体")
    assert semi["count"] == 1


def test_large_industry_gap_does_not_call_single_source_when_bulk_fails(tmp_path):
    """批量行业源失败且缺口较大时,避免逐只打东财单股接口。"""

    class ManyMissingSource(FakeLhbSource):
        def get_lhb_detail(self, start, end):
            self.detail_calls.append(start)
            return _detail([f"600{i:03d}" for i in range(30)]) if start == "20260710" else pd.DataFrame()

        def get_industry_map(self):
            return {}

        def get_stock_industry(self, code):
            self.industry_calls.append(code)
            return "银行"

    src = ManyMissingSource()
    svc = LhbService(src, cache_dir=tmp_path, clock=_clock, async_fill=False)
    payload = svc.get_board("2026-07-10")

    assert payload["industry_pending"] == 30
    assert src.industry_calls == []


def test_cached_payload_reclassified_from_industry_map(tmp_path):
    """旧归档里全是未分类时,读取接口会按最新行业映射重算板块并回写缓存。"""

    class BulkSource(FakeLhbSource):
        def get_industry_map(self):
            return {"600001": "银行", "300002": "半导体"}

    raw = build_payload(_detail(["600001", "300002"]), pd.DataFrame(), {}, "2026-07-10")
    (tmp_path / "20260710.json").write_text(json.dumps(raw, ensure_ascii=False), "utf-8")

    svc = LhbService(BulkSource(), cache_dir=tmp_path, clock=_clock, async_fill=True)
    payload = svc.get_board("2026-07-10")

    assert payload["industry_pending"] == 0
    assert {s["code"]: s["industry"] for s in payload["stocks"]} == {
        "600001": "银行",
        "300002": "半导体",
    }
    saved = json.loads((tmp_path / "20260710.json").read_text("utf-8"))
    assert saved["industry_pending"] == 0
    assert {x["industry"] for x in saved["sectors"]} == {"银行", "半导体"}


def test_async_fill_returns_immediately_then_backfills(tmp_path):
    """异步模式:首次请求立即返回(可能未分类),后台补齐后下次请求已归类。"""
    import time as _time

    src = FakeLhbSource()
    svc = LhbService(src, cache_dir=tmp_path, clock=_clock, async_fill=True)
    p1 = svc.get_board("2026-07-10")
    assert p1["summary"]["stocks"] == 2  # 不被行业补缺阻塞
    for _ in range(50):  # 等后台线程完成(FakeSource 极快)
        if "600001" in src.industry_calls and not svc._filling:
            break
        _time.sleep(0.05)
    p2 = svc.get_board("2026-07-10")
    a = next(s for s in p2["stocks"] if s["code"] == "600001")
    assert a["industry"] == "银行"


def test_sector_trends_daily_and_weekly_from_cache(tmp_path):
    """趋势接口只读归档缓存,按日/周聚合板块机构净额。"""

    def write_day(day, org_net):
        detail = _detail(["600001", "300002"])
        org = pd.DataFrame(
            {
                "代码": ["600001", "300002"],
                "买方机构数": [1, 1],
                "卖方机构数": [0, 0],
                "机构买入总额": [max(org_net, 0), 2e7],
                "机构卖出总额": [max(-org_net, 0), 0.0],
                "机构买入净额": [org_net, 2e7],
                "机构净买额占总成交额比": [1.0, 1.0],
            }
        )
        payload = build_payload(
            detail,
            org,
            {"600001": "银行", "300002": "半导体"},
            day,
        )
        name = day.replace("-", "")
        (tmp_path / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False), "utf-8")

    write_day("2026-07-09", -1e7)
    write_day("2026-07-10", 3e7)

    svc = LhbService(FakeLhbSource(), cache_dir=tmp_path, clock=_clock, async_fill=True)
    daily = svc.get_sector_trends(days=7, end_date="2026-07-10", period="daily", top=2)
    assert daily["cached_days"] == 2
    assert daily["industries"][0] in {"银行", "半导体"}
    assert [p["date"] for p in daily["points"]] == ["2026-07-09", "2026-07-10"]
    assert daily["points"][1]["sectors"]["银行"]["org_net"] == 3e7

    weekly = svc.get_sector_trends(days=7, end_date="2026-07-10", period="weekly", top=2)
    assert len(weekly["points"]) == 1
    assert weekly["points"][0]["sectors"]["银行"]["org_net"] == 2e7
    assert weekly["points"][0]["sectors"]["半导体"]["org_net"] == 4e7


def test_archive_day_writes_disk_including_today(tmp_path):
    """归档任务:当日也落盘;之后读取直接命中磁盘不再取数。"""
    from datetime import date

    src = FakeLhbSource()

    def friday_clock():
        return datetime(2026, 7, 10, 18, 30)  # 归档当天(周五)盘后

    svc = LhbService(src, cache_dir=tmp_path, clock=friday_clock, async_fill=False)
    payload = svc.archive_day()
    assert payload["date"] == "2026-07-10"
    assert (tmp_path / "20260710.json").exists()
    # 同日再查:直接读盘,不发起新的榜单请求
    calls_before = list(src.detail_calls)
    svc2 = LhbService(src, cache_dir=tmp_path, clock=friday_clock, async_fill=False)
    assert svc2.get_board("2026-07-10")["summary"]["stocks"] == 2
    assert src.detail_calls == calls_before
    # 无数据日不落盘
    assert svc.archive_day(date(2026, 7, 11))["summary"]["stocks"] == 0
    assert not (tmp_path / "20260711.json").exists()


def test_empty_day_returns_empty_payload(tmp_path):
    svc = LhbService(FakeLhbSource(), cache_dir=tmp_path, clock=_clock, async_fill=False)
    payload = svc.get_board("2026-07-11")
    assert payload["summary"]["stocks"] == 0 and payload["stocks"] == []
    assert payload["industry_pending"] == 0


def test_api_endpoint(tmp_path):
    from fastapi.testclient import TestClient

    from quant.config import Config
    from quant.web.app import create_app
    from quant.web.service import DashboardService

    class NullSource:
        pass

    cfg = Config(watchlist=[], rules={})
    svc = DashboardService(NullSource(), cfg)
    lhb = LhbService(FakeLhbSource(), cache_dir=tmp_path, clock=_clock, async_fill=False)
    client = TestClient(create_app(svc, screen_runner=None, lhb_service=lhb))

    resp = client.get("/api/lhb?date=2026-07-10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["org_stocks"] == 1
    assert client.get("/api/lhb?date=bad").status_code == 400
    assert client.get("/lhb").status_code == 200

    # 未配置服务时返回 503
    client2 = TestClient(create_app(svc))
    assert client2.get("/api/lhb").status_code == 503
