"""自选名单加载与合并:手填名单(config/manual) + 选股生成名单。

合并规则:手填优先(排在前、名称以手填为准),按代码去重。
盯盘进程每轮调用,实现"选股出新名单 -> 盯盘自动接入",无需重启。
"""

from __future__ import annotations

import tomllib
from pathlib import Path


def read_watchlist_file(path: str | Path) -> list[dict]:
    """读取一个含 [[watchlist]] 的 TOML 文件;不存在或无条目返回 []。"""
    p = Path(path)
    if not p.exists():
        return []
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    return data.get("watchlist", [])


def _normalize_code(code: str) -> str:
    raw = str(code).strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    if raw.isdigit() and len(raw) <= 6:
        raw = raw.zfill(6)
    if not (raw.isdigit() and len(raw) == 6):
        raise ValueError("股票代码必须是 6 位数字")
    return raw


def _toml_quote(value: str) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def write_watchlist_file(items: list[dict], path: str | Path) -> None:
    """写入含 [[watchlist]] 的 TOML 文件。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for item in items:
        code = _normalize_code(item["code"])
        name = str(item.get("name") or code).strip() or code
        lines.append("[[watchlist]]")
        lines.append(f"code = {_toml_quote(code)}")
        lines.append(f"name = {_toml_quote(name)}")
        lines.append("")
    p.write_text("\n".join(lines), encoding="utf-8")


def add_watchlist_item(path: str | Path, code: str, name: str | None = None) -> tuple[dict, bool]:
    """把股票加入手动自选文件;已存在则不重复写入。"""
    norm_code = _normalize_code(code)
    rows = read_watchlist_file(path)
    for row in rows:
        if _normalize_code(row["code"]) == norm_code:
            return {"code": norm_code, "name": row.get("name", norm_code)}, False
    item = {"code": norm_code, "name": (name or norm_code).strip() or norm_code}
    rows.append(item)
    write_watchlist_file(rows, path)
    return item, True


def merge_watchlists(*lists: list[dict]) -> list[dict]:
    """合并多份名单,按 code 去重;越靠前优先(排前、名称优先)。"""
    out: list[dict] = []
    seen: set[str] = set()
    for items in lists:
        for item in items:
            code = _normalize_code(item["code"])
            if code in seen:
                continue
            seen.add(code)
            out.append({"code": code, "name": item.get("name", code)})
    return out


def load_active_watchlist(
    cfg_watchlist: list[dict],
    generated_path: str | Path,
    manual_path: str | Path | None = None,
) -> list[dict]:
    """盯盘实际使用的名单 = 配置手填 + 页面手动添加 + 生成名单。"""
    manual = read_watchlist_file(manual_path) if manual_path is not None else []
    return merge_watchlists(cfg_watchlist, manual, read_watchlist_file(generated_path))
