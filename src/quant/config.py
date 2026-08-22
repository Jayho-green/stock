"""配置加载:读取 TOML(stdlib tomllib),返回 Config。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Config:
    poll_interval_seconds: int = 30
    cooldown_seconds: int = 900
    channels: dict[str, bool] = field(default_factory=lambda: {"terminal": True})
    watchlist: list[dict[str, str]] = field(default_factory=list)
    rules: dict[str, Any] = field(default_factory=dict)
    screen: dict[str, Any] = field(default_factory=dict)

    @property
    def codes(self) -> list[str]:
        return [item["code"] for item in self.watchlist]


def load_config(path: str | Path) -> Config:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return Config(
        poll_interval_seconds=data.get("poll_interval_seconds", 30),
        cooldown_seconds=data.get("cooldown_seconds", 900),
        channels=data.get("channels", {"terminal": True}),
        watchlist=data.get("watchlist", []),
        rules=data.get("rules", {}),
        screen=data.get("screen", {}),
    )
