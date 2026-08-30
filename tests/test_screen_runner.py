import time

from quant.web.screen_runner import ScreenRunner


def _wait_idle(runner, timeout=2.0):
    t0 = time.time()
    while runner.status()["running"] and time.time() - t0 < timeout:
        time.sleep(0.01)


def test_start_runs_and_records_result():
    runner = ScreenRunner(lambda s: {"count": 3, "strategy": s})
    assert runner.start("zhixing") is True
    _wait_idle(runner)
    st = runner.status()
    assert st["running"] is False
    assert st["last"]["ok"] is True
    assert st["last"]["count"] == 3
    assert st["last"]["strategy"] == "zhixing"


def test_no_double_start_while_running():
    started = {"n": 0}

    def slow(s):
        started["n"] += 1
        time.sleep(0.2)
        return {"count": 1}

    runner = ScreenRunner(slow)
    assert runner.start("default") is True
    assert runner.start("default") is False  # 正在运行,拒绝重复启动
    _wait_idle(runner)
    assert started["n"] == 1
    assert runner.start("zhixing") is True  # 跑完后可再次启动
    _wait_idle(runner)


def test_failure_recorded_and_unlocks():
    def boom(s):
        raise RuntimeError("数据源挂了")

    runner = ScreenRunner(boom)
    runner.start("zhixing")
    # 异常进入重试等待,手动取消后立即结束
    assert runner.cancel() is True
    _wait_idle(runner)
    st = runner.status()
    assert st["running"] is False
    assert st["last"]["ok"] is False
    assert st["last"]["cancelled"] is True
    assert "数据源挂了" in st["last"]["error"]


def test_cancel_returns_false_when_idle():
    runner = ScreenRunner(lambda s: {"count": 1})
    assert runner.cancel() is False


def test_stop_check_passed_to_four_arg_runner():
    seen = {}

    def run(strategy, scope, progress, stop_check):
        seen["strategy"] = strategy
        seen["scope"] = scope
        seen["stop_check"] = callable(stop_check)
        progress({"done": 1})
        return {"count": 1, "complete": True}

    runner = ScreenRunner(run)
    assert runner.start("zhixing", "hs300") is True
    _wait_idle(runner)
    st = runner.status()
    assert seen == {"strategy": "zhixing", "scope": "hs300", "stop_check": True}
    assert st["last"]["ok"] is True


def test_cancel_during_run_marks_cancelled():
    def run(strategy, scope, progress, stop_check):
        progress({"done": 1, "total": 10})
        while not stop_check():
            time.sleep(0.01)
        return {"count": 2, "done": 5, "total": 10, "stopped": True, "complete": False}

    runner = ScreenRunner(run)
    assert runner.start("zhixing") is True
    time.sleep(0.1)
    assert runner.cancel() is True
    _wait_idle(runner)
    st = runner.status()
    assert st["running"] is False
    assert st["last"]["ok"] is True
    assert st["last"]["cancelled"] is True
    assert st["last"]["count"] == 2


def test_retry_continues_until_complete():
    calls = {"n": 0}

    def run(strategy, scope, progress, stop_check):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"count": 0, "complete": False, "done": 1, "total": 10}
        return {"count": 4, "complete": True, "done": 10, "total": 10}

    runner = ScreenRunner(run)
    # 缩短重试等待,避免测试跑一分钟
    import quant.web.screen_runner as sr

    old_delay = sr.SCREEN_RETRY_DELAY
    sr.SCREEN_RETRY_DELAY = 0.01
    try:
        assert runner.start("zhixing") is True
        _wait_idle(runner, timeout=5)
    finally:
        sr.SCREEN_RETRY_DELAY = old_delay
    st = runner.status()
    assert st["running"] is False
    assert calls["n"] == 3
    assert st["last"]["ok"] is True
    assert st["last"]["count"] == 4


def test_progress_is_exposed_while_running():
    def slow(s, progress):
        progress({"done": 1, "total": 2})
        time.sleep(0.1)
        return {"count": 1}

    runner = ScreenRunner(slow)
    runner.start("zhixing")
    t0 = time.time()
    while runner.status()["progress"] is None and time.time() - t0 < 1:
        time.sleep(0.01)
    assert runner.status()["progress"] == {"done": 1, "total": 2}
    _wait_idle(runner)


def test_scope_is_passed_to_three_arg_runner():
    seen = {}

    def run(strategy, scope, progress):
        seen["strategy"] = strategy
        seen["scope"] = scope
        progress({"done": 1})
        return {"count": 1, "scope": scope}

    runner = ScreenRunner(run)
    assert runner.start("zhixing", "hs300") is True
    _wait_idle(runner)
    st = runner.status()
    assert seen == {"strategy": "zhixing", "scope": "hs300"}
    assert st["scope"] == "hs300"
    assert st["last"]["scope"] == "hs300"
