"""提醒通道抽象接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..signals.types import Signal

_ARROW = {"long": "↑看多", "short": "↓看空"}


def format_signal(sig: Signal) -> str:
    """统一的人类可读提醒文案。"""
    arrow = _ARROW.get(sig.direction, sig.direction)
    detail = " ".join(f"{k}={v}" for k, v in sig.detail.items())
    return (
        f"[{sig.time}] {sig.name}({sig.code}) {sig.rule} {arrow} "
        f"价={sig.price} {detail}".rstrip()
    )


class Notifier(ABC):
    @abstractmethod
    def send(self, sig: Signal) -> None: ...
