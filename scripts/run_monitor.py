"""盯盘入口:交易时段内循环监控自选股,触发信号去重后提醒并记日志。

用法:
    .venv/bin/python scripts/run_monitor.py [配置文件路径]
默认配置 config/config.toml,不存在则用 config/config.example.toml。
"""

from __future__ import annotations

import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant.calendar import is_in_session
from quant.config import load_config
from quant.datasource.akshare_source import AkshareSource
from quant.logstore import append as log_append
from quant.notify.dedup import Deduper
from quant.notify.desktop import DesktopNotifier
from quant.notify.terminal import TerminalNotifier
from quant.signals.engine import run_rules
from quant.signals.monitor_rules import MONITOR_RULES
from quant.watchlist import load_active_watchlist

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "data" / "triggers.jsonl"
GENERATED_WATCHLIST = ROOT / "config" / "watchlist.generated.toml"
MANUAL_WATCHLIST = ROOT / "config" / "watchlist.manual.toml"


def _resolve_config() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    real = ROOT / "config" / "config.toml"
    return real if real.exists() else ROOT / "config" / "config.example.toml"


def _build_notifiers(channels):
    notifiers = []
    if channels.get("terminal", True):
        notifiers.append(TerminalNotifier())
    if channels.get("desktop", False):
        notifiers.append(DesktopNotifier())
    return notifiers


def main() -> None:
    cfg = load_config(_resolve_config())
    source = AkshareSource()
    notifiers = _build_notifiers(cfg.channels)
    deduper = Deduper(cooldown_seconds=cfg.cooldown_seconds)

    print(f"盯盘启动,轮询 {cfg.poll_interval_seconds}s(手填 + 选股生成名单合并)")
    last_count = -1
    while True:
        now = datetime.now()
        if not is_in_session(now):
            time.sleep(cfg.poll_interval_seconds)
            continue
        # 每轮重新加载名单:选股 15:30 出新名单后,盯盘下一轮自动接入,无需重启
        watchlist = load_active_watchlist(cfg.watchlist, GENERATED_WATCHLIST, MANUAL_WATCHLIST)
        if len(watchlist) != last_count:
            print(f"[{now:%H:%M}] 当前盯盘 {len(watchlist)} 只")
            last_count = len(watchlist)
        for item in watchlist:
            code, name = item["code"], item.get("name", item["code"])
            try:
                bars = source.get_minute_bars(code)
                for sig in run_rules(bars, MONITOR_RULES, code, name, cfg.rules):
                    if deduper.should_notify(sig.code, sig.rule):
                        for n in notifiers:
                            n.send(sig)
                        log_append(sig, LOG_PATH)
            except Exception:
                print(f"[warn] {code} 处理失败:\n{traceback.format_exc()}")
        time.sleep(cfg.poll_interval_seconds)


if __name__ == "__main__":
    main()
