"""选股历史记录:用 JSONL 简单持久化每次选股结果。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _iso_now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _safe_record(record: dict[str, Any]) -> dict[str, Any]:
    selected = record.get("selected") or []
    return {
        "time": record.get("finished_at") or _iso_now(),
        "strategy": record.get("strategy"),
        "scope": record.get("scope"),
        "count": record.get("count", len(selected)),
        "universe": record.get("universe"),
        "done": record.get("done"),
        "failed": record.get("failed"),
        "timed_out": bool(record.get("timed_out", False)),
        "aborted": bool(record.get("aborted", False)),
        "abort_reason": record.get("abort_reason"),
        "elapsed": record.get("elapsed"),
        "complete": bool(record.get("complete", False)),
        "selected": [
            {"code": str(item.get("code", "")), "name": str(item.get("name", item.get("code", "")))}
            for item in selected
        ],
    }


def append_history(path: str | Path, record: dict[str, Any]) -> dict[str, Any]:
    """追加一条选股历史记录,返回实际写入的记录。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _safe_record(record)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return row


def read_history(path: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    """读取最近 limit 条历史记录,新记录在前。"""
    p = Path(path)
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(rows[-limit:]))
