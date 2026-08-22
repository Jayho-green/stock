"""选股器:确定股票池 + 并发拉日线套用选股规则。

- ``filter_universe``:按代码前缀过滤股票池(纯函数,可单测)。
  科创板=688/689,创业板=300/301。
- ``screen_concurrent``:多线程对股票池逐只拉日线、套用 SCREEN_RULES,返回入选名单。
  并发把上千次请求从几十分钟压到几分钟;单只失败不影响整体。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from .kline_cache import PostCloseKlineCache, effective_market_date

DEFAULT_PREFIXES = ("688", "689", "300", "301")  # 科创板 + 创业板
MAIN_BOARD_PREFIXES = ("000", "001", "002", "003", "600", "601", "603", "605")

# 板块代码前缀
BOARD_PREFIXES = {
    "科创板": ("688", "689"),
    "创业板": ("300", "301"),
}

SCOPES: dict[str, dict] = {
    "star_chinext": {
        "label": "科创板+创业板",
        "type": "prefix",
        "prefixes": DEFAULT_PREFIXES,
        "allow_config_prefixes": True,
    },
    "main_board": {
        "label": "全部主板",
        "type": "prefix",
        "prefixes": MAIN_BOARD_PREFIXES,
    },
    "hs300": {"label": "沪深300", "type": "index", "symbol": "000300"},
    "star50": {"label": "科创50", "type": "index", "symbol": "000688"},
    "zz500": {"label": "中证500", "type": "index", "symbol": "000905"},
    "zz1000": {"label": "中证1000", "type": "index", "symbol": "000852"},
}

DEFAULT_SCOPE = "star_chinext"


def filter_universe(code_name: pd.DataFrame, prefixes: tuple[str, ...]) -> list[dict]:
    """从 code+name 表里过滤出代码以指定前缀开头的股票。"""
    out: list[dict] = []
    for r in code_name.itertuples():
        code = str(r.code)
        if code.startswith(tuple(prefixes)):
            out.append({"code": code, "name": str(r.name)})
    return out


def list_scopes() -> list[dict]:
    return [{"id": k, "label": v["label"]} for k, v in SCOPES.items()]


def resolve_universe(source, screen_cfg: dict, scope: str | None = None) -> tuple[list[dict], str]:
    """按选股范围返回股票池和实际 scope id。"""
    scope_id = scope or screen_cfg.get("scope", DEFAULT_SCOPE)
    if scope_id not in SCOPES:
        raise KeyError(f"未知选股范围: {scope_id};可选 {list(SCOPES)}")
    spec = SCOPES[scope_id]
    if spec["type"] == "index":
        rows = source.get_index_constituents(spec["symbol"])
        return [{"code": str(r.code), "name": str(r.name)} for r in rows.itertuples()], scope_id
    if spec.get("allow_config_prefixes"):
        prefixes = tuple(screen_cfg.get("prefixes", spec.get("prefixes", DEFAULT_PREFIXES)))
    else:
        prefixes = tuple(spec.get("prefixes", DEFAULT_PREFIXES))
    return filter_universe(source.get_all_code_name(), prefixes), scope_id


def _passes(daily: pd.DataFrame, rules: list[Callable], cfg: dict) -> bool:
    return bool(len(daily)) and all(rule(daily, cfg) for rule in rules)


def _score(daily: pd.DataFrame, cfg: dict) -> float:
    """排序分:当日量比(末日量 / 前 lookback 日均量),越高代表放量越猛。"""
    lookback = cfg.get("vol_lookback", 5)
    if len(daily) < lookback + 1:
        return 0.0
    base = daily["volume"].iloc[-(lookback + 1):-1].mean()
    return float(daily["volume"].iloc[-1] / base) if base else 0.0


def _screen_signature(strategy: str, scope: str, start: str, end: str) -> str:
    raw = json.dumps(
        {"strategy": strategy, "scope": scope, "start": start, "end": end},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _load_checkpoint(path: str | Path, signature: str) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if data.get("signature") == signature else None


def _write_checkpoint(path: str | Path, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def screen_concurrent(
    universe: list[dict],
    source,
    rules: list[Callable],
    cfg: dict,
    start: str,
    end: str,
    workers: int = 10,
    top_n: int | None = None,
    timeout_seconds: float | None = None,
    progress: Callable[[dict], None] | None = None,
    on_result: Callable[[dict, str, dict | None], None] | None = None,
    kline_cache: PostCloseKlineCache | None = None,
) -> list[dict]:
    """并发对 universe 逐只拉日线套用规则,按量比降序取前 top_n,返回 [{code,name}]。

    top_n=None 表示不限数量。
    timeout_seconds 设置整批选股最长等待时间;超时后返回已完成任务里的入选结果。
    """

    stats = {
        "total": len(universe),
        "done": 0,
        "passed": 0,
        "missed": 0,
        "failed": 0,
        "cancelled": 0,
        "timed_out": False,
        "aborted": False,
        "abort_reason": None,
    }
    max_initial_failures = cfg.get("max_initial_failures", 30)
    min_failure_check = cfg.get("min_failure_check", 50)
    max_failure_rate = cfg.get("max_failure_rate", 0.9)

    def emit() -> None:
        if progress is not None:
            progress(dict(stats))

    def work(item: dict) -> tuple[str, dict | None]:
        try:
            fetch = lambda: source.get_daily_bars(item["code"], start, end)
            if kline_cache is not None:
                daily = kline_cache.get_or_fetch(f"screen_daily_{start}_{end}", item["code"], fetch)
            else:
                daily = fetch()
            if _passes(daily, rules, cfg):
                return "passed", {**item, "score": _score(daily, cfg)}
            return "missed", None
        except Exception:
            return "failed", None  # 单只取数失败:跳过,不影响整体

    def should_abort() -> str | None:
        if stats["passed"] == 0 and stats["missed"] == 0 and stats["failed"] >= max_initial_failures:
            return f"数据源连续失败 {stats['failed']} 只,已停止选股"
        if stats["done"] >= min_failure_check:
            fail_rate = stats["failed"] / stats["done"]
            if fail_rate >= max_failure_rate:
                return f"数据源失败率 {fail_rate:.0%},已停止选股"
        return None

    passers: list[dict] = []
    deadline = time.monotonic() + timeout_seconds if timeout_seconds else None
    executor = ThreadPoolExecutor(max_workers=workers)
    items = iter(universe)
    pending: set[Future] = set()

    def submit_next() -> bool:
        try:
            item = next(items)
        except StopIteration:
            return False
        fut = executor.submit(work, item)
        fut._screen_item = item
        pending.add(fut)
        return True

    for _ in range(min(workers, len(universe))):
        submit_next()
    emit()
    try:
        while pending:
            wait_timeout = 1.0
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stats["timed_out"] = True
                    break
                wait_timeout = min(wait_timeout, remaining)

            done, pending = wait(
                pending,
                timeout=wait_timeout,
                return_when=FIRST_COMPLETED,
            )
            if not done:
                emit()
                continue

            for fut in done:
                status, item = fut.result()
                original = getattr(fut, "_screen_item", {})
                stats["done"] += 1
                stats[status] += 1
                if item is not None:
                    passers.append(item)
                if on_result is not None:
                    on_result(original, status, item)
                reason = should_abort()
                if reason:
                    stats["aborted"] = True
                    stats["abort_reason"] = reason
                    break
                submit_next()
            emit()
            if stats["aborted"]:
                break
    finally:
        if pending:
            stats["cancelled"] = sum(1 for fut in pending if fut.cancel())
            if not stats["aborted"]:
                stats["timed_out"] = True
        executor.shutdown(
            wait=not (stats["timed_out"] or stats["aborted"]),
            cancel_futures=stats["timed_out"] or stats["aborted"],
        )
        emit()

    passers.sort(key=lambda x: x["score"], reverse=True)
    if top_n is not None:
        passers = passers[:top_n]
    return [{"code": p["code"], "name": p["name"]} for p in passers]


def write_generated(selected: list[dict], path: str | Path) -> None:
    """把入选名单写成含 [[watchlist]] 的 TOML(供盯盘/面板读取)。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# run_screen 自动生成 @ {date.today()};入选 {len(selected)} 只\n\n"]
    for item in selected:
        lines.append(f'[[watchlist]]\ncode = "{item["code"]}"\nname = "{item["name"]}"\n\n')
    p.write_text("".join(lines), encoding="utf-8")


def run_full_screen(
    source,
    screen_cfg: dict,
    generated_path: str | Path,
    strategy: str = "zhixing",
    scope: str | None = None,
    progress: Callable[[dict], None] | None = None,
    checkpoint_path: str | Path | None = None,
    resume: bool = True,
    history_path: str | Path | None = None,
    kline_cache_path: str | Path | None = None,
    clock: Callable[[], datetime] = datetime.now,
) -> dict:
    """完整选股流程(定时任务与面板按钮共用):

    取全A代码 -> 按前缀过滤股票池 -> 并发拉日线套用所选方案的规则 -> 量比排序取 top_n
    -> 写入 generated_path。返回汇总 dict。
    """
    from .strategies import get_rules

    rules = get_rules(strategy)
    workers = screen_cfg.get("workers", 10)
    days = screen_cfg.get("lookback_days", 250)
    top_n = screen_cfg.get("top_n", 20)
    timeout_seconds = screen_cfg.get("timeout_seconds", 900)
    latest_progress: dict = {}
    kline_cache = PostCloseKlineCache(kline_cache_path, clock=clock) if kline_cache_path is not None else None

    def update_progress(stats: dict) -> None:
        latest_progress.clear()
        latest_progress.update(stats)
        if progress is not None:
            progress(stats)

    t0 = time.time()
    end = effective_market_date(clock)
    start = end - timedelta(days=days)
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")
    universe, scope_id = resolve_universe(source, screen_cfg, scope)
    signature = _screen_signature(strategy, scope_id, start_s, end_s)
    ckpt_path = Path(checkpoint_path) if checkpoint_path is not None else None
    checkpoint = None
    if resume and ckpt_path is not None:
        checkpoint = _load_checkpoint(ckpt_path, signature)
    if checkpoint is None:
        checkpoint = {
            "signature": signature,
            "strategy": strategy,
            "scope": scope_id,
            "date": end.isoformat(),
            "start": start_s,
            "end": end_s,
            "processed": {},
            "selected": [],
        }

    processed = checkpoint.get("processed", {})
    previous_selected = checkpoint.get("selected", [])
    remaining = [item for item in universe if item["code"] not in processed]
    resumed_done = len(processed)
    resumed_selected = len(previous_selected)

    def save_result(item: dict, status: str, selected_item: dict | None) -> None:
        processed[item["code"]] = status
        if selected_item is not None and not any(s["code"] == selected_item["code"] for s in previous_selected):
            previous_selected.append(selected_item)
        checkpoint["processed"] = processed
        checkpoint["selected"] = previous_selected
        checkpoint["updated_at"] = time.time()
        if ckpt_path is not None:
            _write_checkpoint(ckpt_path, checkpoint)

    def update_progress_with_resume(stats: dict) -> None:
        merged = {
            **stats,
            "total": len(universe),
            "done": resumed_done + stats.get("done", 0),
            "resumed_done": resumed_done,
            "resumed_selected": resumed_selected,
            "remaining": max(0, len(universe) - resumed_done - stats.get("done", 0)),
        }
        update_progress(merged)

    selected = screen_concurrent(
        remaining,
        source,
        rules,
        screen_cfg,
        start_s,
        end_s,
        workers=workers,
        top_n=top_n,
        timeout_seconds=timeout_seconds,
        progress=update_progress_with_resume,
        on_result=save_result,
        kline_cache=kline_cache,
    )
    all_selected = previous_selected
    for item in selected:
        if not any(s["code"] == item["code"] for s in all_selected):
            all_selected.append(item)
    all_selected = all_selected[:top_n] if top_n is not None else all_selected
    write_generated(all_selected, generated_path)
    if ckpt_path is not None:
        checkpoint["selected"] = all_selected
        checkpoint["complete"] = len(processed) >= len(universe)
        checkpoint["updated_at"] = time.time()
        _write_checkpoint(ckpt_path, checkpoint)
    result = {
        "selected": all_selected,
        "count": len(all_selected),
        "universe": len(universe),
        "top_n": top_n,
        "strategy": strategy,
        "scope": scope_id,
        "elapsed": round(time.time() - t0, 1),
        "time": date.today().isoformat(),
        "resumed_done": resumed_done,
        "resumed_selected": resumed_selected,
        "remaining": max(0, len(universe) - len(processed)),
        "complete": len(processed) >= len(universe),
        **latest_progress,
    }
    if history_path is not None:
        from .screen_history import append_history

        result["history"] = append_history(history_path, result)
    return result
