from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from quant.amv0 import UNIVERSE, Instrument, compute_cyc, dma, sina_symbol, universe_list
from quant.web.amv0_service import Amv0Service, effective_session_date


def test_dma_matches_manual_recursion():
    values = np.array([10.0, 12.0, 14.0])
    alpha = np.array([0.5, 0.5, 0.25])
    out = dma(values, alpha)
    assert out[0] == 10.0
    assert out[1] == pytest.approx(0.5 * 12 + 0.5 * 10)
    assert out[2] == pytest.approx(0.25 * 14 + 0.75 * out[1])


def test_dma_clamps_alpha_out_of_range():
    out = dma(np.array([10.0, 20.0, 30.0]), np.array([1.0, -1.0, 5.0]))
    assert out[1] == 10.0   # alpha<=0 -> 完全保持上一值
    assert out[2] == 30.0   # alpha>1 -> 截断为 1


def test_sina_symbol_prefix():
    assert sina_symbol("512880") == "sh512880"
    assert sina_symbol("159825") == "sz159825"


def test_universe_includes_agriculture_etf():
    assert UNIVERSE["159825"] == ("农业ETF", "农林牧渔")
    assert any(item.sector == "农林牧渔" for item in universe_list())


def _fake_frames(n=40, split_at=None):
    """构造原始(不复权)与前复权两套行情;split_at 处模拟 1:2 份额折算。"""
    dates = pd.bdate_range("2026-01-01", periods=n).strftime("%Y-%m-%d")
    close = np.linspace(2.0, 3.0, n)
    raw_close = close.copy()
    if split_at is not None:
        raw_close[:split_at] = close[:split_at] * 2   # 折算前原始价是复权价的两倍
    volume = np.full(n, 1_000_000.0)
    raw = pd.DataFrame(
        {
            "date": dates,
            "open": raw_close, "high": raw_close * 1.01, "low": raw_close * 0.99,
            "close": raw_close, "volume": volume, "amount": raw_close * volume,
        }
    )
    qfq = pd.DataFrame(
        {
            "date": dates, "q_open": close, "q_close": close,
            "q_high": close * 1.01, "q_low": close * 0.99, "q_vol": volume / 100,
        }
    )
    return raw, qfq


def test_compute_cyc_basic_shape_and_columns():
    raw, qfq = _fake_frames()
    out = compute_cyc(raw, qfq, capital=2e7)
    for col in ["amv0", "cyc5", "cyc13", "cyc34", "cys0", "alpha", "above0"]:
        assert col in out.columns
    assert len(out) == len(raw)
    assert out["amv0"].notna().all()


def test_compute_cyc_amv0_tracks_rising_price_from_below():
    """价格单调上行时,成本线应落后于价格(全体持仓成本低于现价)。"""
    raw, qfq = _fake_frames()
    out = compute_cyc(raw, qfq, capital=2e7)
    assert out["amv0"].iloc[-1] < out["close"].iloc[-1]
    assert out["cys0"].iloc[-1] > 0
    assert out["above0"].iloc[-1] == 1


def test_compute_cyc_removes_split_artifact():
    """份额折算不得产生假跌:复权后单日涨跌幅应保持温和。"""
    raw, qfq = _fake_frames(split_at=20)
    out = compute_cyc(raw, qfq, capital=2e7)
    pct = out["close"].pct_change().abs().dropna()
    assert pct.max() < 0.05
    # 均价也必须走复权口径,否则会在折算日跳变
    assert (out["avg_price"] / out["close"] - 1).abs().max() < 0.02


def test_effective_session_date_rolls_back_over_weekend():
    # 2026-08-29 是周六 -> 回退到周五 08-28
    assert effective_session_date(lambda: datetime(2026, 8, 29, 10, 0)).isoformat() == "2026-08-28"
    # 周一 07:00(08:00 前) -> 回退到上周五
    assert effective_session_date(lambda: datetime(2026, 8, 31, 7, 0)).isoformat() == "2026-08-28"
    # 周一 16:00 -> 当天
    assert effective_session_date(lambda: datetime(2026, 8, 31, 16, 0)).isoformat() == "2026-08-31"


def _service(tmp_path, clock):
    return Amv0Service(
        cache_dir=tmp_path,
        clock=clock,
        instruments=[Instrument("159825", "农业ETF", "农林牧渔")],
    )


def test_is_stale_when_cache_missing(tmp_path):
    svc = _service(tmp_path, lambda: datetime(2026, 8, 31, 16, 0))
    assert svc.is_stale() is True


def test_cached_session_is_not_stale(tmp_path):
    clock = lambda: datetime(2026, 8, 31, 16, 0)  # noqa: E731
    svc = _service(tmp_path, clock)
    svc._write_json(svc._meta_path, {"session_date": "2026-08-31", "ok": ["159825"]})
    assert svc.is_stale() is False


def test_intraday_does_not_refetch_same_day_cache(tmp_path):
    """盘中(15:30 前)若当天已刷新过,不应反复拉取数据源。"""
    svc = _service(tmp_path, lambda: datetime(2026, 8, 31, 11, 0))
    svc._write_json(svc._meta_path, {"session_date": "2026-08-31", "ok": ["159825"]})
    assert svc.is_stale() is False


def test_overview_and_series_from_cache(tmp_path):
    clock = lambda: datetime(2026, 8, 31, 16, 0)  # noqa: E731
    svc = _service(tmp_path, clock)
    raw, qfq = _fake_frames()
    frame = compute_cyc(raw, qfq, capital=2e7)
    svc._write_json(svc._meta_path, {"session_date": "2026-08-31", "updated_at": "2026-08-31 15:35:00", "ok": ["159825"]})
    svc._write_json(
        svc._series_path("159825"),
        {
            "session_date": "2026-08-31", "code": "159825", "name": "农业ETF",
            "sector": "农林牧渔", "capital": 2e7,
            "rows": [
                {c: (r[c] if c == "date" else float(r[c])) for c in
                 ["date", "open", "high", "low", "close", "volume", "amv0", "cyc5", "cyc13", "cyc34", "cys0", "alpha"]}
                for _, r in frame.iterrows()
            ],
        },
    )

    overview = svc.get_overview(auto_refresh=False)
    assert overview["rows"][0]["code"] == "159825"
    assert overview["rows"][0]["above0"] is True
    assert overview["sectors"][0]["sector"] == "农林牧渔"
    assert len(overview["breadth"]) > 0

    series = svc.get_series("159825", days=30)
    assert series["name"] == "农业ETF"
    assert len(series["ohlc"]) == 30
    assert len(series["ohlc"][0]) == 4   # ECharts [open, close, low, high]
    assert len(series["amv0"]) == 30


def test_get_series_missing_code_raises(tmp_path):
    svc = _service(tmp_path, lambda: datetime(2026, 8, 31, 16, 0))
    with pytest.raises(KeyError):
        svc.get_series("999999")


def test_add_signals_marks_deep_discount_rebound():
    """深度折价且乖离回升当日应触发买点信号。"""
    from quant.amv0 import add_signals, zone_of

    frame = pd.DataFrame({
        "cys0": [-12.0, -14.0, -13.0, -2.0, -9.0],   # idx2: 深折价且回升; idx4: 深折价但下跌
        "cyc5": [1, 1, 1, 3, 1], "cyc13": [2, 2, 2, 2, 2], "amv0": [3, 3, 3, 1, 3],
    })
    out = add_signals(frame)
    assert list(out["buy_signal"]) == [0, 0, 1, 0, 0]
    assert list(out["deep_discount"]) == [1, 1, 1, 0, 1]
    assert out["align_signal"].iloc[3] == 1      # idx3 首次形成 cyc5>cyc13>amv0
    assert zone_of(-12.0) == "深度折价"
    assert zone_of(-7.0) == "折价"
    assert zone_of(0.0) == "成本区"
    assert zone_of(9.0) == "溢价"


def test_compute_cyc_emits_signal_columns():
    raw, qfq = _fake_frames()
    out = compute_cyc(raw, qfq, capital=2e7)
    for col in ["buy_signal", "align_signal", "deep_discount"]:
        assert col in out.columns
        assert out[col].isin([0, 1]).all()


def test_overview_exposes_watch_list(tmp_path):
    """CYS0 低于 -6 的标的应进入超跌观察区。"""
    clock = lambda: datetime(2026, 8, 31, 16, 0)  # noqa: E731
    svc = _service(tmp_path, clock)
    svc._write_json(svc._meta_path, {"session_date": "2026-08-31", "ok": ["159825"]})
    svc._write_json(svc._series_path("159825"), {
        "session_date": "2026-08-31", "code": "159825", "name": "农业ETF",
        "sector": "农林牧渔", "capital": 2e7,
        "rows": [
            {"date": "2026-08-28", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1,
             "amv0": 1.2, "cyc5": 1, "cyc13": 1, "cyc34": 1, "cys0": -14.0, "alpha": 0.05,
             "buy_signal": 0, "align_signal": 0, "deep_discount": 1},
            {"date": "2026-08-31", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1,
             "amv0": 1.15, "cyc5": 1, "cyc13": 1, "cyc34": 1, "cys0": -12.0, "alpha": 0.05,
             "buy_signal": 1, "align_signal": 0, "deep_discount": 1},
        ],
    })
    ov = svc.get_overview(auto_refresh=False)
    assert [w["code"] for w in ov["watch"]] == ["159825"]
    assert ov["watch"][0]["zone"] == "深度折价"
    assert ov["rows"][0]["buy_signal"] == 1
    assert ov["rows"][0]["days_since_signal"] == 0

    series = svc.get_series("159825", days=30)
    assert series["buy_marks"] == [[1, 1]]
