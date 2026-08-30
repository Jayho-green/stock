import pandas as pd
import pytest

from quant.amv_index import ANCHORS, AmvIndexStore, check_anchors, parse_text, two_day_signal
from quant.sector_flow import INDUSTRY_TO_ETF, top_inflow


def test_parse_text_handles_mixed_separators_and_date_formats():
    text = """日期,开盘,最高,最低,收盘
2026-08-26 191500.0 193000.0 190000.0 192100.0
2026/08/27\t191800.0\t192500.0\t190800.0\t191409.0
20260828,191043.0,195661.6,189214.9,189298.4
"""
    frame = parse_text(text)
    assert list(frame["date"]) == ["2026-08-26", "2026-08-27", "2026-08-28"]
    assert frame["close"].iloc[-1] == pytest.approx(189298.4)
    assert frame["high"].iloc[-1] == pytest.approx(195661.6)


def test_parse_text_close_only():
    frame = parse_text("2026-08-27 191409.0\n2026-08-28 189298.4\n")
    assert list(frame.columns) == ["date", "close"]
    assert len(frame) == 2


def test_parse_text_rejects_garbage():
    with pytest.raises(ValueError):
        parse_text("这里没有任何日期行\n只是说明文字\n")


def test_parse_text_dedupes_same_date_keeping_last():
    frame = parse_text("2026-08-28 1.0\n2026-08-28 2.0\n")
    assert len(frame) == 1 and frame["close"].iloc[0] == 2.0


def test_check_anchors_detects_wrong_scale():
    """若导入数据单位错了(例如差 100 倍),锚点核对必须报不符。"""
    good = pd.DataFrame([{"date": "2026-08-28", "close": ANCHORS["2026-08-28"]["close"]}])
    bad = pd.DataFrame([{"date": "2026-08-28", "close": ANCHORS["2026-08-28"]["close"] / 100}])
    assert check_anchors(good)[0]["status"] == "吻合"
    assert check_anchors(bad)[0]["status"] == "不符"
    assert check_anchors(pd.DataFrame([{"date": "2020-01-01", "close": 1.0}]))[0]["status"] == "缺失"


def test_two_day_signal_threshold():
    frame = pd.DataFrame({
        "date": ["d1", "d2", "d3", "d4"],
        "close": [100.0, 102.0, 105.0, 105.5],   # d3 两日累计 +5% -> 触发
    })
    out = two_day_signal(frame, threshold=4.0)
    assert list(out["signal"]) == [0, 0, 1, 0]
    assert out["ret2"].iloc[2] == pytest.approx(5.0)


def test_store_merge_is_incremental(tmp_path):
    store = AmvIndexStore(tmp_path / "0amv.json")
    store.merge(parse_text("2026-08-27 191409.0\n"))
    info = store.merge(parse_text("2026-08-28 189298.4\n"))
    assert info["rows"] == 2 and info["end"] == "2026-08-28"
    # 同日重复导入以新值覆盖
    store.merge(parse_text("2026-08-28 999.0\n"))
    assert store.load().set_index("date").loc["2026-08-28", "close"] == 999.0


def test_top_inflow_skips_industries_without_etf():
    frame = pd.DataFrame([
        {"行业": "综合", "净额": 99.0},          # 无对应 ETF,应跳过
        {"行业": "半导体", "净额": 50.0},
        {"行业": "白酒", "净额": 30.0},
        {"行业": "钢铁", "净额": 10.0},
    ])
    picks = top_inflow(frame, n=2)
    assert [p["industry"] for p in picks] == ["半导体", "白酒"]
    assert picks[0]["etfs"] == INDUSTRY_TO_ETF["半导体"]


def test_industry_map_targets_exist_in_universe():
    from quant.amv0 import UNIVERSE
    for industry, etfs in INDUSTRY_TO_ETF.items():
        for code in etfs:
            assert code in UNIVERSE, f"{industry} 映射到了不存在的 ETF {code}"
