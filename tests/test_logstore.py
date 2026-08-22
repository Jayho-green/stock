from quant import logstore
from quant.signals.types import Signal


def test_append_and_read(tmp_path):
    path = tmp_path / "log" / "triggers.jsonl"
    s1 = Signal("ma_cross", "long", "2024-01-01 10:00", 12.3, {"ma_short": 5}, "000001", "平安银行")
    s2 = Signal("rsi_extreme", "short", "2024-01-01 10:01", 99.0, {"rsi": 72.0}, "600519", "贵州茅台")
    logstore.append(s1, path)
    logstore.append(s2, path)
    rows = logstore.read_all(path)
    assert len(rows) == 2
    assert rows[0]["code"] == "000001"
    assert rows[1]["rule"] == "rsi_extreme"
    assert rows[0]["detail"]["ma_short"] == 5


def test_read_missing_returns_empty(tmp_path):
    assert logstore.read_all(tmp_path / "nope.jsonl") == []
