import pandas as pd

from quant.signals.engine import run_rules
from quant.signals.monitor_rules import MONITOR_RULES, rsi_extreme


def _flat(closes, vols=None):
    n = len(closes)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01 09:30", periods=n, freq="min"),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": vols if vols else [100] * n,
        }
    )


def test_run_rules_fills_code_and_name():
    closes = list(range(15, 0, -1))  # 触发 rsi 超卖
    sigs = run_rules(_flat(closes), [rsi_extreme], "000001", "平安银行", {})
    assert len(sigs) == 1
    assert sigs[0].code == "000001"
    assert sigs[0].name == "平安银行"


def test_run_rules_empty_when_no_trigger():
    closes = [10.0] * 30  # 横盘,无信号
    sigs = run_rules(_flat(closes), MONITOR_RULES, "000001", "平安银行", {})
    assert sigs == []


def test_monitor_rules_registry_nonempty():
    assert len(MONITOR_RULES) >= 5
