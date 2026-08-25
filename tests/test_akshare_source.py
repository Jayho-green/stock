import pandas as pd
import pytest

from quant.datasource.akshare_source import AkshareSource, _market_symbol, _normalize_bars, _normalize_tx_daily


def test_normalize_minute_columns_and_sort():
    # 模拟 akshare 分钟K原始返回(中文列、时间乱序)
    raw = pd.DataFrame(
        {
            "时间": ["2024-01-01 09:32", "2024-01-01 09:30", "2024-01-01 09:31"],
            "开盘": ["10.0", "9.8", "9.9"],
            "最高": ["10.2", "9.9", "10.0"],
            "最低": ["9.9", "9.7", "9.8"],
            "收盘": ["10.1", "9.9", "10.0"],
            "成交量": ["300", "100", "200"],
            "成交额": ["x", "y", "z"],  # 多余列应被丢弃
        }
    )
    df = _normalize_bars(raw)
    assert list(df.columns) == ["datetime", "open", "high", "low", "close", "volume"]
    # 已按时间升序
    assert df["datetime"].is_monotonic_increasing
    assert df["close"].iloc[0] == 9.9
    assert df["volume"].iloc[0] == 100
    # 数值类型
    assert pd.api.types.is_numeric_dtype(df["close"])


def test_normalize_daily_columns():
    raw = pd.DataFrame(
        {
            "日期": ["2024-01-02", "2024-01-01"],
            "开盘": [10, 9],
            "最高": [11, 9.5],
            "最低": [9.5, 8.5],
            "收盘": [10.5, 9],
            "成交量": [1000, 800],
        }
    )
    df = _normalize_bars(raw)
    assert df["close"].iloc[0] == 9  # 1月1日排前
    assert len(df) == 2


def test_normalize_english_daily_columns():
    raw = pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-01"],
            "open": [10, 9],
            "high": [11, 9.5],
            "low": [9.5, 8.5],
            "close": [10.5, 9],
            "volume": [1000, 800],
        }
    )
    df = _normalize_bars(raw)
    assert list(df.columns) == ["datetime", "open", "high", "low", "close", "volume"]
    assert df["close"].iloc[0] == 9


def test_normalize_tx_daily_uses_amount_as_volume():
    raw = pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-01"],
            "open": [10, 9],
            "high": [11, 9.5],
            "low": [9.5, 8.5],
            "close": [10.5, 9],
            "amount": [1000, 800],
        }
    )
    df = _normalize_tx_daily(raw)
    assert list(df.columns) == ["datetime", "open", "high", "low", "close", "volume"]
    assert df["volume"].iloc[0] == 800


def test_normalize_sina_minute_columns():
    raw = pd.DataFrame(
        {
            "day": ["2024-01-01 09:31", "2024-01-01 09:30"],
            "open": ["10.0", "9.9"],
            "high": ["10.1", "10.0"],
            "low": ["9.8", "9.7"],
            "close": ["10.0", "9.8"],
            "volume": ["200", "100"],
            "amount": ["2000", "1000"],
        }
    )
    df = _normalize_bars(raw)
    assert df["datetime"].iloc[0] == pd.Timestamp("2024-01-01 09:30")
    assert df["close"].iloc[0] == 9.8


def test_market_symbol_for_a_share_codes():
    assert _market_symbol("688136") == "sh688136"
    assert _market_symbol("600519") == "sh600519"
    assert _market_symbol("300750") == "sz300750"
    assert _market_symbol("000001") == "sz000001"


def test_normalize_drops_nonpositive_price_rows():
    # 含一行 0 价占位行(akshare 偶发),应被丢弃,避免把 K 线 y 轴拉到 0
    raw = pd.DataFrame(
        {
            "时间": ["2024-01-01 09:30", "2024-01-01 09:31", "2024-01-01 09:32"],
            "开盘": [10.0, 0.0, 10.1],
            "最高": [10.2, 0.0, 10.3],
            "最低": [9.9, 0.0, 10.0],
            "收盘": [10.1, 0.0, 10.2],
            "成交量": [100, 0, 200],
        }
    )
    df = _normalize_bars(raw)
    assert len(df) == 2
    assert (df["low"] > 0).all()


def test_normalize_missing_column_raises():
    raw = pd.DataFrame({"时间": ["2024-01-01 09:30"], "开盘": [10]})
    with pytest.raises(ValueError):
        _normalize_bars(raw)


def test_get_global_quotes_normalizes_eastmoney_rows(monkeypatch):
    class FakeResp:
        def json(self):
            return {
                "data": {
                    "diff": [
                        {
                            "f12": "005930",
                            "f13": 177,
                            "f14": "三星电子",
                            "f2": 266500.0,
                            "f3": 4.72,
                            "f4": 12000.0,
                            "f18": 254500.0,
                            "f44": 270000.0,
                            "f45": 247000.0,
                            "f46": 255000.0,
                            "f47": 39990000.0,
                            "f124": 1784007231,
                        }
                    ]
                }
            }

    def fake_get(url, params=None, timeout=None, headers=None):
        assert params["secids"] == "177.005930"
        return FakeResp()

    monkeypatch.setattr("requests.get", fake_get)
    rows = AkshareSource().get_global_quotes(
        [{"id": "samsung", "secid": "177.005930", "name": "三星电子", "currency": "KRW"}]
    )
    assert rows[0]["id"] == "samsung"
    assert rows[0]["secid"] == "177.005930"
    assert rows[0]["code"] == "005930"
    assert rows[0]["name"] == "三星电子"
    assert rows[0]["market"] == "KRX"
    assert rows[0]["currency"] == "KRW"
    assert rows[0]["price"] == 266500.0
    assert rows[0]["change_pct"] == 4.72
    assert rows[0]["change"] == 12000.0
    assert rows[0]["prev_close"] == 254500.0
    assert rows[0]["day_high"] == 270000.0
    assert rows[0]["day_low"] == 247000.0
    assert rows[0]["open"] == 255000.0
    assert rows[0]["volume"] == 39990000.0
    assert rows[0]["source_time"]


def test_get_global_intraday_parses_trends(monkeypatch):
    class FakeResp:
        def json(self):
            return {
                "data": {
                    "code": "005930",
                    "market": 177,
                    "name": "三星电子",
                    "preClose": 254500.0,
                    "time": 1784010599,
                    "trends": [
                        "2026-07-14 08:00,255000,255000,255000,255000,0,0,255000.0",
                        "2026-07-14 08:01,255000,259000,260000,254000,1424270,0,259000.0",
                    ],
                }
            }

    def fake_get(url, params=None, timeout=None, headers=None):
        assert params["secid"] == "177.005930"
        return FakeResp()

    monkeypatch.setattr("requests.get", fake_get)
    data = AkshareSource().get_global_intraday(
        [{"id": "samsung", "secid": "177.005930", "name": "三星电子"}]
    )
    assert data["samsung"]["code"] == "005930"
    assert data["samsung"]["points"][1]["price"] == 259000.0
    assert round(data["samsung"]["points"][1]["pct"], 2) == 1.77
