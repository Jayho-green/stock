"""macOS 桌面通知:用 osascript 弹系统通知。失败时静默降级,不影响主流程。"""

from __future__ import annotations

import subprocess

from ..signals.types import Signal
from .base import Notifier, format_signal


class DesktopNotifier(Notifier):
    def send(self, sig: Signal) -> None:
        title = f"{sig.name} {sig.rule}"
        body = format_signal(sig).replace('"', "'")
        script = f'display notification "{body}" with title "{title}"'
        try:
            subprocess.run(
                ["osascript", "-e", script],
                check=False,
                capture_output=True,
                timeout=5,
            )
        except Exception:
            # 通知失败不应中断盯盘
            pass
