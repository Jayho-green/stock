"""选股方案注册表:每套方案 = 一组选股规则 + 显示名。

加新方案 = 在 STRATEGIES 里加一行。定时任务和面板"立即选股"都按方案 id 选规则。
"""

from __future__ import annotations

from .signals.screen_rules import above_ma, volume_surge, zhixing_pick

STRATEGIES: dict[str, dict] = {
    "default": {
        "label": "默认(站上20日线 + 放量)",
        "rules": [above_ma, volume_surge],
    },
    "zhixing": {
        "label": "知行多空线方案",
        "rules": [zhixing_pick],
    },
}

DEFAULT_STRATEGY = "zhixing"  # 定时任务缺省用的方案(config 可覆盖)


def get_rules(strategy: str) -> list:
    if strategy not in STRATEGIES:
        raise KeyError(f"未知选股方案: {strategy};可选 {list(STRATEGIES)}")
    return STRATEGIES[strategy]["rules"]


def list_strategies() -> list[dict]:
    return [{"id": k, "label": v["label"]} for k, v in STRATEGIES.items()]
