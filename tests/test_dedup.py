from quant.notify.dedup import Deduper
from quant.notify.base import format_signal
from quant.signals.types import Signal


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_first_call_allowed():
    d = Deduper(cooldown_seconds=900, now=FakeClock())
    assert d.should_notify("000001", "ma_cross") is True


def test_second_call_within_cooldown_blocked():
    clk = FakeClock()
    d = Deduper(cooldown_seconds=900, now=clk)
    d.should_notify("000001", "ma_cross")
    clk.t += 100  # 仍在冷却期
    assert d.should_notify("000001", "ma_cross") is False


def test_call_after_cooldown_allowed():
    clk = FakeClock()
    d = Deduper(cooldown_seconds=900, now=clk)
    d.should_notify("000001", "ma_cross")
    clk.t += 900  # 冷却到期
    assert d.should_notify("000001", "ma_cross") is True


def test_different_rule_independent():
    d = Deduper(cooldown_seconds=900, now=FakeClock())
    d.should_notify("000001", "ma_cross")
    assert d.should_notify("000001", "rsi_extreme") is True


def test_format_signal_contains_key_fields():
    sig = Signal("ma_cross", "long", "2024-01-01 10:00", 12.3, {"ma_short": 5}, "000001", "平安银行")
    s = format_signal(sig)
    assert "000001" in s and "ma_cross" in s and "12.3" in s
