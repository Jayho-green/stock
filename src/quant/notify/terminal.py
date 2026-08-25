"""终端提醒:直接打印到控制台。"""

from __future__ import annotations

from ..signals.types import Signal
from .base import Notifier, format_signal


class TerminalNotifier(Notifier):
    def send(self, sig: Signal) -> None:
        print("🔔 " + format_signal(sig))
