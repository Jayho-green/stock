"""信号执行引擎:对一只股票的 bars 跑一批规则,收集 Signal。"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from .types import Signal

Rule = Callable[[pd.DataFrame, dict], "Signal | None"]


def run_rules(
    df: pd.DataFrame,
    rules: list[Rule],
    code: str,
    name: str,
    cfg: dict,
) -> list[Signal]:
    """逐条跑规则,触发的回填 code/name 后返回。"""
    out: list[Signal] = []
    for rule in rules:
        sig = rule(df, cfg)
        if sig is not None:
            sig.code = code
            sig.name = name
            out.append(sig)
    return out
