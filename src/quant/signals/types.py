"""信号数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Signal:
    """一次规则触发的结果。

    code/name 由引擎在调用规则后回填(规则本身只关心行情与指标)。
    """

    rule: str
    direction: str  # 'long' | 'short'
    time: Any
    price: float
    detail: dict[str, Any] = field(default_factory=dict)
    code: str = ""
    name: str = ""

    def to_record(self) -> dict[str, Any]:
        """转为可 JSON 序列化的扁平记录(用于日志)。"""
        return {
            "code": self.code,
            "name": self.name,
            "rule": self.rule,
            "direction": self.direction,
            "time": str(self.time),
            "price": self.price,
            "detail": self.detail,
        }
