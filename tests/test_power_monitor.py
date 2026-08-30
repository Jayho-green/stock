import json
from datetime import datetime, timedelta

import pandas as pd
import pytest

from quant.config import Config
from quant.web.service import (
    POWER_FALLBACK,
    POWER_WATCH_SIZE,
    DashboardService,
    _in_trading_window,
    _limit_up_threshold,
)


def _cfg():
    return Config(watchlist=[{"code": "000001", "name": "平安银行"}])


def _at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm)


WED_930 = _at(2026, 8, 26, 9, 30)  # 周三 9:30 开盘十分钟内
WED_1400 = _at(2026, 8, 26, 14, 0)  # 周三盘中
SAT_1000 = _at(2026, 8, 29, 10, 0)  # 周六
WED_1530 = _at(2026, 8, 26, 15, 30)  # 周三收盘后


class PowerFakeSource:
    def __init__(self, board_df=None, realtime=None):
        self.board_df = board_df
        self.realtime = realtime or {}
        self.board_calls = 0
        self.realtime_calls = 0

    def get_minute_bars(self, code, period="1"):
        return pd.DataFrame()

    def get_all_code_name(self):
        return pd.DataFrame([{"code": "000001", "name": "平安银行"}])

    def get_industry_board_cons(self, board="电力行业"):
        self.board_calls += 1
        if isinstance(self.board_df, Exception):
            raise self.board_df
        return self.board_df

    def get_realtime_board(self, codes=None):
        self.realtime_calls += 1
        rows = [
            {"code": c, "name": v["name"], "price": v["price"], "pct": v["pct"]}
            for c, v in self.realtime.items()
            if codes is None or str(c).zfill(6) in {str(x).zfill(6) for x in codes}
        ]
        return pd.DataFrame(rows)


def _board_df():
    return pd.DataFrame(
        {
            "code": ["600001", "600002", "600003", "600004"],
            "name": ["华能国际", "ST华电", "国电电力", "长江电力"],
            "price": [10.0, 5.0, 8.0, 25.0],
            "pct": [1.0, 1.0, 1.0, 1.0],
            "amount": [100.0, 9999.0, 80.0, 60.0],
            "turnover": [1.0, 5.0, 2.0, 0.5],
        }
    )


def _svc(tmp_path, source, clock=None):
    return DashboardService(
        source,
        _cfg(),
        clock=clock or (lambda: WED_930),
        power_watch_path=tmp_path / "power_watch.toml",
        power_state_path=tmp_path / "power_monitor.json",
    )


# ---- 涨停幅度 ----


def test_limit_up_threshold():
    assert _limit_up_threshold("600011", "华能国际") == 10.0
    assert _limit_up_threshold("300001", "宁德") == 20.0
    assert _limit_up_threshold("688001", "华兴") == 20.0
    assert _limit_up_threshold("600744", "ST华银") == 5.0
    assert _limit_up_threshold("830001", "北交") == 30.0


# ---- 交易窗口 ----


def test_in_trading_window():
    assert _in_trading_window(WED_930) is True
    assert _in_trading_window(WED_1400) is True
    assert _in_trading_window(SAT_1000) is False
    assert _in_trading_window(WED_1530) is False
    assert _in_trading_window(_at(2026, 8, 26, 9, 10)) is False
    assert _in_trading_window(_at(2026, 8, 26, 9, 25)) is True


# ---- 名单:兜底/活跃排序/排ST/落盘 ----


def test_watch_fallback_when_no_board_api():
    class NoBoard(PowerFakeSource):
        get_industry_board_cons = None  # type: ignore[assignment]

    svc = DashboardService(NoBoard(), _cfg(), clock=lambda: WED_930)
    r = svc.get_power_monitor()
    assert [w["code"] for w in r["watch"]] == [c for c, _ in POWER_FALLBACK]
    assert r["hit"] is False


def test_watch_picks_active_stocks_and_persists(tmp_path):
    src = PowerFakeSource(board_df=_board_df())
    svc = _svc(tmp_path, src)
    watch = svc.get_power_monitor()["watch"]
    # ST华电成交额最大但被排除;按成交额降序
    assert [w["name"] for w in watch] == ["华能国际", "国电电力", "长江电力"]
    assert (tmp_path / "power_watch.toml").exists()
    assert src.board_calls == 1


def test_watch_reads_disk_without_refetch(tmp_path):
    (tmp_path / "power_watch.toml").write_text(
        '[[watchlist]]\ncode = "600001"\nname = "华能国际"\n', encoding="utf-8"
    )
    src = PowerFakeSource(board_df=_board_df())
    svc = _svc(tmp_path, src)
    watch = svc.get_power_monitor()["watch"]
    assert watch == [{"code": "600001", "name": "华能国际"}]
    assert src.board_calls == 0  # 磁盘有名单不重新拉


def test_watch_board_error_falls_back(tmp_path):
    src = PowerFakeSource(board_df=RuntimeError("限流"))
    svc = _svc(tmp_path, src)
    watch = svc.get_power_monitor()["watch"]
    assert len(watch) == POWER_WATCH_SIZE
    assert watch == [{"code": c, "name": n} for c, n in POWER_FALLBACK]


# ---- 涨停检测 ----


def _realtime():
    return {
        "600001": {"name": "华能国际", "price": 11.0, "pct": 10.03},
        "600003": {"name": "国电电力", "price": 8.4, "pct": 5.0},
    }


def test_detects_limit_up_and_persists(tmp_path):
    src = PowerFakeSource(board_df=_board_df(), realtime=_realtime())
    svc = _svc(tmp_path, src)
    r = svc.get_power_monitor()
    assert r["hit"] is True
    assert len(r["hits"]) == 1
    hit = r["hits"][0]
    assert hit["code"] == "600001"
    assert hit["name"] == "华能国际"
    assert hit["time"] == "09:30"
    assert hit["broken"] is False

    # 模拟服务重启:新实例从磁盘恢复当日涨停记录
    svc2 = _svc(tmp_path, PowerFakeSource(board_df=_board_df(), realtime=_realtime()))
    r2 = svc2.get_power_monitor()
    assert r2["hit"] is True
    assert r2["hits"][0]["code"] == "600001"
    assert r2["hits"][0]["time"] == "09:30"


def test_broken_limit_up_kept_and_marked(tmp_path):
    src = PowerFakeSource(board_df=_board_df(), realtime=_realtime())
    svc = _svc(tmp_path, src)
    assert svc.get_power_monitor()["hits"][0]["broken"] is False

    # 炸板:回落到8%
    src.realtime["600001"]["pct"] = 8.0
    src.realtime["600001"]["price"] = 10.8
    svc._cache.set("power_realtime", None)
    r = svc.get_power_monitor()
    assert len(r["hits"]) == 1  # 记录保留
    assert r["hits"][0]["broken"] is True
    assert r["hits"][0]["pct"] == 8.0


def test_no_detection_outside_trading_hours(tmp_path):
    src = PowerFakeSource(board_df=_board_df(), realtime=_realtime())
    svc = _svc(tmp_path, src, clock=lambda: WED_1530)
    r = svc.get_power_monitor()
    assert r["hit"] is False
    assert src.realtime_calls == 0  # 非交易时段不拉行情


def test_state_resets_on_new_day(tmp_path):
    src = PowerFakeSource(board_df=_board_df(), realtime=_realtime())
    svc = _svc(tmp_path, src, clock=lambda: WED_930)
    assert svc.get_power_monitor()["hit"] is True

    svc.clock = lambda: WED_930 + timedelta(days=1)  # 次日
    svc._cache.set("power_realtime", None)
    src.realtime = {}  # 次日无涨停
    r = svc.get_power_monitor()
    assert r["hit"] is False


def test_realtime_error_degrades_to_no_hit(tmp_path):
    class ErrRealtime(PowerFakeSource):
        def get_realtime_board(self, codes=None):
            raise RuntimeError("限流")

    svc = _svc(tmp_path, ErrRealtime(board_df=_board_df()))
    r = svc.get_power_monitor()
    assert r["hit"] is False
    assert len(r["watch"]) == 3


# ---- API 层 ----


def test_power_monitor_endpoint(tmp_path):
    from fastapi.testclient import TestClient

    from quant.web.app import create_app

    svc = DashboardService(
        PowerFakeSource(),
        _cfg(),
        clock=lambda: WED_1530,  # 非交易时段,稳定断言
        power_watch_path=tmp_path / "power_watch.toml",
        power_state_path=tmp_path / "power_monitor.json",
    )
    c = TestClient(create_app(svc))
    r = c.get("/api/power/monitor")
    assert r.status_code == 200
    body = r.json()
    assert "watch" in body and "hits" in body and "hit" in body
    assert body["hit"] is False
    assert body["trading"] is False

    r2 = c.get("/api/power/monitor?force=true")
    assert r2.status_code == 200


def test_power_state_file_is_valid_json(tmp_path):
    src = PowerFakeSource(board_df=_board_df(), realtime=_realtime())
    svc = _svc(tmp_path, src)
    svc.get_power_monitor()
    data = json.loads((tmp_path / "power_monitor.json").read_text(encoding="utf-8"))
    assert data["date"] == "2026-08-26"
    assert "600001" in data["hits"]
