import pytest

from quant.strategies import STRATEGIES, DEFAULT_STRATEGY, get_rules, list_strategies
from quant.signals.screen_rules import zhixing_pick, above_ma, volume_surge


def test_registry_has_two_schemes():
    assert "default" in STRATEGIES and "zhixing" in STRATEGIES


def test_get_rules_returns_correct_set():
    assert get_rules("zhixing") == [zhixing_pick]
    assert get_rules("default") == [above_ma, volume_surge]


def test_get_rules_unknown_raises():
    with pytest.raises(KeyError):
        get_rules("nope")


def test_list_strategies_has_id_and_label():
    items = list_strategies()
    assert all("id" in it and "label" in it for it in items)
    assert DEFAULT_STRATEGY in {it["id"] for it in items}
