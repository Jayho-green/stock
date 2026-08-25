from datetime import datetime
import time

import pandas as pd

from quant.screener import filter_universe, resolve_universe, run_full_screen, screen_concurrent
from quant.signals.screen_rules import above_ma, volume_surge

RULES = [above_ma, volume_surge]  # 显式规则,不依赖注册表内容


class FixedDateTimeClock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self):
        return self.value


def _code_name():
    return pd.DataFrame(
        {
            "code": [
                "688001",
                "300750",
                "301234",
                "600519",
                "000001",
                "689009",
                "002594",
                "003816",
                "601398",
                "603259",
                "605499",
                "830799",
            ],
            "name": [
                "科创A",
                "宁德时代",
                "创业B",
                "茅台",
                "平安",
                "科创C",
                "比亚迪",
                "中国广核",
                "工商银行",
                "药明康德",
                "东鹏饮料",
                "北交所A",
            ],
        }
    )


def test_filter_universe_star_and_chinext():
    uni = filter_universe(_code_name(), ("688", "689", "300", "301"))
    codes = {u["code"] for u in uni}
    assert codes == {"688001", "300750", "301234", "689009"}  # 排除主板 600/000


def _daily(closes, vols=None):
    n = len(closes)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": vols if vols else [1000] * n,
        }
    )


class FakeSource:
    def __init__(self, data):
        self.data = data

    def get_daily_bars(self, code, start, end):
        if code == "BOOM":  # 模拟取数失败
            raise RuntimeError("network")
        return self.data[code]


class UniverseSource:
    def get_all_code_name(self):
        return _code_name()

    def get_index_constituents(self, symbol):
        assert symbol == "000300"
        return pd.DataFrame({"code": ["000001", "600519"], "name": ["平安", "茅台"]})


def test_screen_concurrent_selects_passers():
    hit = _daily(list(range(1, 25)), [1000] * 23 + [3000])  # 站上均线 + 放量
    miss = _daily(list(range(25, 1, -1)), [1000] * 24)       # 跌破均线
    src = FakeSource({"688001": hit, "300750": miss})
    uni = [{"code": "688001", "name": "A"}, {"code": "300750", "name": "B"}]
    cfg = {"ma_window": 20, "vol_lookback": 5, "vol_surge_mult": 1.5}
    out = screen_concurrent(uni, src, RULES, cfg, "20240101", "20240201", workers=4)
    assert [o["code"] for o in out] == ["688001"]


def test_resolve_universe_prefix_scope():
    uni, scope = resolve_universe(UniverseSource(), {"prefixes": ["300"]}, "star_chinext")
    assert scope == "star_chinext"
    assert [u["code"] for u in uni] == ["300750"]


def test_resolve_universe_main_board_excludes_star_chinext_and_bj():
    uni, scope = resolve_universe(UniverseSource(), {"prefixes": ["300"]}, "main_board")
    codes = {u["code"] for u in uni}
    assert scope == "main_board"
    assert codes == {"600519", "000001", "002594", "003816", "601398", "603259", "605499"}
    assert not codes & {"688001", "689009", "300750", "301234", "830799"}


def test_resolve_universe_index_scope():
    uni, scope = resolve_universe(UniverseSource(), {}, "hs300")
    assert scope == "hs300"
    assert uni == [{"code": "000001", "name": "平安"}, {"code": "600519", "name": "茅台"}]


def test_screen_concurrent_top_n_ranks_by_volume_ratio():
    # 三只都过规则,但末日放量倍数不同;top_n=2 取量比最高的两只,按降序
    def mk(mult):  # 末日放量 mult 倍
        return _daily(list(range(1, 25)), [1000] * 23 + [int(1000 * mult)])

    src = FakeSource({"688001": mk(2.0), "300750": mk(5.0), "301234": mk(3.0)})
    uni = [
        {"code": "688001", "name": "A"},
        {"code": "300750", "name": "B"},
        {"code": "301234", "name": "C"},
    ]
    cfg = {"ma_window": 20, "vol_lookback": 5, "vol_surge_mult": 1.5}
    out = screen_concurrent(uni, src, RULES, cfg, "20240101", "20240201", workers=4, top_n=2)
    assert [o["code"] for o in out] == ["300750", "301234"]  # 5倍、3倍 在前;2倍被截掉


def test_screen_concurrent_skips_failures():
    hit = _daily(list(range(1, 25)), [1000] * 23 + [3000])
    src = FakeSource({"688001": hit})
    uni = [{"code": "BOOM", "name": "X"}, {"code": "688001", "name": "A"}]
    cfg = {"ma_window": 20, "vol_lookback": 5, "vol_surge_mult": 1.5}
    out = screen_concurrent(uni, src, RULES, cfg, "20240101", "20240201", workers=4)
    assert [o["code"] for o in out] == ["688001"]  # 失败的被跳过


def test_screen_concurrent_timeout_returns_completed_work():
    class SlowSource:
        def get_daily_bars(self, code, start, end):
            time.sleep(0.2)
            return _daily(list(range(1, 25)), [1000] * 23 + [3000])

    progress = []
    uni = [{"code": "688001", "name": "A"}, {"code": "300750", "name": "B"}]
    cfg = {"ma_window": 20, "vol_lookback": 5, "vol_surge_mult": 1.5}
    t0 = time.monotonic()
    out = screen_concurrent(
        uni,
        SlowSource(),
        RULES,
        cfg,
        "20240101",
        "20240201",
        workers=1,
        timeout_seconds=0.05,
        progress=progress.append,
    )
    assert out == []
    assert time.monotonic() - t0 < 0.15
    assert progress[-1]["timed_out"] is True


def test_screen_concurrent_aborts_on_initial_failures():
    class FailingSource:
        def get_daily_bars(self, code, start, end):
            raise RuntimeError("network")

    progress = []
    uni = [{"code": f"688{i:03d}", "name": str(i)} for i in range(100)]
    cfg = {
        "ma_window": 20,
        "vol_lookback": 5,
        "vol_surge_mult": 1.5,
        "max_initial_failures": 5,
    }
    out = screen_concurrent(
        uni,
        FailingSource(),
        RULES,
        cfg,
        "20240101",
        "20240201",
        workers=2,
        progress=progress.append,
        cooldown_seconds=0.01,
        max_cooldowns=0,
    )
    assert out == []
    assert progress[-1]["aborted"] is True
    assert progress[-1]["done"] < len(uni)
    assert "连续失败" in progress[-1]["abort_reason"]


def test_screen_concurrent_cooldown_retries_on_rate_limit():
    call_count = {"n": 0}

    class RateLimitedSource:
        def get_daily_bars(self, code, start, end):
            call_count["n"] += 1
            if call_count["n"] <= 10:
                raise RuntimeError("rate limited")
            return _daily(list(range(1, 25)), [1000] * 23 + [3000])

    progress = []
    uni = [{"code": f"688{i:03d}", "name": str(i)} for i in range(20)]
    cfg = {
        "ma_window": 20,
        "vol_lookback": 5,
        "vol_surge_mult": 1.5,
        "max_initial_failures": 5,
    }
    out = screen_concurrent(
        uni,
        RateLimitedSource(),
        RULES,
        cfg,
        "20240101",
        "20240201",
        workers=2,
        progress=progress.append,
        cooldown_seconds=0.01,
        max_cooldowns=3,
    )
    assert len(out) > 0
    assert any(p.get("cooldowns", 0) > 0 for p in progress)
    assert any(p.get("cooling_down") for p in progress)
    assert progress[-1].get("aborted") is not True


def test_screen_concurrent_cooldown_does_not_consume_timeout():
    call_count = {"n": 0}

    class RateLimitedSource:
        def get_daily_bars(self, code, start, end):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                raise RuntimeError("rate limited")
            return _daily(list(range(1, 25)), [1000] * 23 + [3000])

    progress = []
    uni = [{"code": f"688{i:03d}", "name": str(i)} for i in range(4)]
    cfg = {
        "ma_window": 20,
        "vol_lookback": 5,
        "vol_surge_mult": 1.5,
        "max_initial_failures": 2,
    }
    out = screen_concurrent(
        uni,
        RateLimitedSource(),
        RULES,
        cfg,
        "20240101",
        "20240201",
        workers=2,
        timeout_seconds=0.3,
        progress=progress.append,
        cooldown_seconds=0.5,  # 大于 timeout;若冷却计入超时预算,恢复前就超时
        max_cooldowns=3,
    )
    assert progress[-1]["timed_out"] is not True
    assert progress[-1]["done"] == 4
    assert len(out) == 2  # 后2只放量通过


def test_screen_concurrent_stop_check_stops_early():
    class SlowSource:
        def get_daily_bars(self, code, start, end):
            time.sleep(0.02)
            return _daily(list(range(1, 25)), [1000] * 23 + [3000])

    stop = {"flag": False}
    progress = []

    def on_progress(st):
        progress.append(st)
        if len(progress) >= 3:
            stop["flag"] = True

    uni = [{"code": f"688{i:03d}", "name": str(i)} for i in range(50)]
    cfg = {"ma_window": 20, "vol_lookback": 5, "vol_surge_mult": 1.5}
    out = screen_concurrent(
        uni,
        SlowSource(),
        RULES,
        cfg,
        "20240101",
        "20240201",
        workers=2,
        progress=on_progress,
        stop_check=lambda: stop["flag"],
    )
    assert progress[-1]["stopped"] is True
    assert progress[-1]["timed_out"] is not True
    assert progress[-1]["done"] < len(uni)  # 未跑完全量即停


def test_run_full_screen_resume_keeps_results_and_progress(tmp_path):
    """复现限流中断后重试场景:进度不回退、已通过的不重选、旧入选保留。"""

    class FlakySource:
        def __init__(self, fail_codes=None):
            self.calls = []
            self.fail_codes = set(fail_codes or [])

        def get_all_code_name(self):
            return pd.DataFrame({"code": ["688001", "688002", "688003"], "name": ["A", "B", "C"]})

        def get_daily_bars(self, code, start, end):
            self.calls.append(code)
            if code in self.fail_codes:
                raise RuntimeError("rate limited")
            return _daily(list(range(1, 25)), [1000] * 23 + [3000])

    cfg = {
        "prefixes": ["688"],
        "workers": 1,
        "top_n": 10,
        "lookback_days": 30,
        "timeout_seconds": 0.3,
        "ma_window": 20,
        "vol_lookback": 5,
        "vol_surge_mult": 1.5,
        "cooldown_seconds": 0.01,
        "max_cooldowns": 2,
    }
    out_path = tmp_path / "watchlist.generated.toml"
    ckpt = tmp_path / "screen.checkpoint.json"

    # 第一轮:A 通过入选,B/C 限流失败 -> 未完成
    src1 = FlakySource(fail_codes={"688002", "688003"})
    first = run_full_screen(src1, cfg, out_path, strategy="default", checkpoint_path=ckpt)
    assert first["complete"] is False
    assert first["count"] == 1

    # 第二轮(自动重试):B/C 恢复,续跑不重选 A
    src2 = FlakySource()
    second = run_full_screen(src2, cfg, out_path, strategy="default", checkpoint_path=ckpt)
    assert second["complete"] is True
    assert second["resumed_done"] == first["done"]  # 进度含 failed,不回退
    assert "688001" not in src2.calls  # 已通过的不重复请求
    assert set(src2.calls) == {"688002", "688003"}
    assert second["count"] == 3  # 三只全入选,旧结果未被覆盖


def test_run_full_screen_resumes_from_checkpoint(tmp_path):
    class FullScreenSource:
        def __init__(self, slow_codes=None):
            self.daily_calls = []
            self.slow_codes = set(slow_codes or [])

        def get_all_code_name(self):
            return pd.DataFrame(
                {
                    "code": ["688001", "688002", "688003"],
                    "name": ["A", "B", "C"],
                }
            )

        def get_daily_bars(self, code, start, end):
            if code in self.slow_codes:
                time.sleep(0.2)
            self.daily_calls.append(code)
            return _daily(list(range(1, 25)), [1000] * 23 + [3000])

    cfg = {
        "prefixes": ["688"],
        "strategy": "default",
        "workers": 1,
        "top_n": 10,
        "lookback_days": 30,
        "timeout_seconds": 0.05,
        "ma_window": 20,
        "vol_lookback": 5,
        "vol_surge_mult": 1.5,
    }
    out_path = tmp_path / "watchlist.generated.toml"
    ckpt = tmp_path / "screen.checkpoint.json"
    history = tmp_path / "screen_history.jsonl"

    src1 = FullScreenSource(slow_codes={"688002", "688003"})
    first = run_full_screen(src1, cfg, out_path, strategy="default", checkpoint_path=ckpt, history_path=history)
    assert first["done"] < 3
    assert first["complete"] is False
    assert src1.daily_calls

    src2 = FullScreenSource()
    second = run_full_screen(src2, cfg, out_path, strategy="default", checkpoint_path=ckpt, history_path=history)
    assert second["resumed_done"] == first["done"]
    assert second["complete"] is True
    assert second["count"] == 3
    assert history.exists()
    assert len(history.read_text(encoding="utf-8").splitlines()) == 2


def test_run_full_screen_reuses_post_close_kline_cache(tmp_path):
    class FullScreenSource:
        def __init__(self):
            self.daily_calls = []

        def get_all_code_name(self):
            return pd.DataFrame({"code": ["688001", "688002"], "name": ["A", "B"]})

        def get_daily_bars(self, code, start, end):
            self.daily_calls.append(code)
            return _daily(list(range(1, 25)), [1000] * 23 + [3000])

    cfg = {
        "prefixes": ["688"],
        "workers": 1,
        "top_n": 10,
        "lookback_days": 30,
        "timeout_seconds": 30,
        "ma_window": 20,
        "vol_lookback": 5,
        "vol_surge_mult": 1.5,
    }
    cache_path = tmp_path / "kline_cache"
    out_path = tmp_path / "watchlist.generated.toml"
    post_close_clock = FixedDateTimeClock(datetime(2026, 7, 1, 16, 0))
    next_morning_clock = FixedDateTimeClock(datetime(2026, 7, 2, 7, 59))

    src1 = FullScreenSource()
    first = run_full_screen(
        src1,
        cfg,
        out_path,
        strategy="default",
        kline_cache_path=cache_path,
        clock=post_close_clock,
    )
    src2 = FullScreenSource()
    second = run_full_screen(
        src2,
        cfg,
        out_path,
        strategy="default",
        kline_cache_path=cache_path,
        clock=next_morning_clock,
    )

    assert first["count"] == 2
    assert second["count"] == 2
    assert src1.daily_calls == ["688001", "688002"]
    assert src2.daily_calls == []
