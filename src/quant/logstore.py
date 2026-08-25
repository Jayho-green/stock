"""触发日志:以 jsonl 追加写入,便于事后复盘与回测取数。"""

from __future__ import annotations

import json
from pathlib import Path

from .signals.types import Signal


def append(sig: Signal, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(sig.to_record(), ensure_ascii=False) + "\n")


def read_all(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
