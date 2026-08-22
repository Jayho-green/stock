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
    _wait_idle(runner)
    st = runner.status()
    assert st["running"] is False
    assert st["last"]["ok"] is False
    assert "数据源挂了" in st["last"]["error"]


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
