import pandas as pd
from fastapi.testclient import TestClient

from quant.config import Config
from quant.web.app import create_app
from quant.web.service import DashboardService


def _bars(closes):
    n = len(closes)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-02 09:30", periods=n, freq="min"),
            "open": closes,
            "high": [c + 0.1 for c in closes],
            "low": [c - 0.1 for c in closes],
            "close": closes,
            "volume": [100] * n,
        }
    )


class FakeSource:
    def get_minute_bars(self, code, period="1"):
        return _bars(list(range(1, 40)))

    def get_daily_bars(self, code, start, end):
        return _bars(list(range(1, 260)))

    def get_all_code_name(self):
        return pd.DataFrame([{"code": "000001", "name": "平安银行"}, {"code": "600000", "name": "浦发银行"}])

    def get_global_quotes(self, targets):
        return [
            {
                **targets[0],
                "code": "000660",
                "price": 180000.0,
                "change_pct": -0.56,
                "change": -1000.0,
                "prev_close": 181000.0,
                "day_high": 182000.0,
                "day_low": 178000.0,
                "open": 179000.0,
                "volume": 1234567.0,
                "source_time": "2026-07-14 10:00:00",
            },
            {
                **targets[1],
                "code": "005930",
                "price": 70000.0,
                "change_pct": 1.23,
                "change": 850.0,
                "prev_close": 69150.0,
                "day_high": 71000.0,
                "day_low": 69000.0,
                "open": 69500.0,
                "volume": 987654.0,
                "source_time": "2026-07-14 10:00:00",
            },
        ]

    def get_global_intraday(self, targets):
        return {
            targets[0]["id"]: {
                "id": targets[0]["id"],
                "prev_close": 181000.0,
                "points": [
                    {"time": "2026-07-14 08:00", "price": 179000.0, "pct": -1.105},
                    {"time": "2026-07-14 08:01", "price": 180000.0, "pct": -0.5525},
                ],
            },
            targets[1]["id"]: {
                "id": targets[1]["id"],
                "prev_close": 69150.0,
                "points": [
                    {"time": "2026-07-14 08:00", "price": 69500.0, "pct": 0.5061},
                    {"time": "2026-07-14 08:01", "price": 70000.0, "pct": 1.2292},
                ],
            },
        }


def _client(tmp_path, manual_path=None, history_path=None):
    cfg = Config(watchlist=[{"code": "000001", "name": "平安银行"}], rules={"rsi_period": 14})

    def news_fetcher(limit=80, today_only=True):
        return {
            "date": "2026-06-30",
            "updated_at": "2026-06-30 10:00:00",
            "today_only": today_only,
            "fallback_latest": False,
            "sources": [{"id": "test", "label": "测试源", "ok": True, "count": 1}],
            "errors": [],
            "items": [
                {
                    "title": "测试市场消息",
                    "summary": "今日市场消息摘要",
                    "published_at": "2026-06-30 09:30:00",
                    "time": "09:30",
                    "source": "测试源",
                    "source_id": "test",
                    "sources": ["测试源"],
                    "url": "",
                    "tags": ["A股"],
                }
            ][:limit],
        }

    svc = DashboardService(FakeSource(), cfg, manual_path=manual_path, news_fetcher=news_fetcher)
    log = tmp_path / "triggers.jsonl"
    log.write_text(
        '{"code":"000001","name":"平安银行","rule":"rsi_extreme","direction":"long","time":"2024-01-02 10:00","price":11.0,"detail":{}}\n',
        encoding="utf-8",
    )
    return TestClient(create_app(svc, log_path=log, screen_history_path=history_path))


def test_quotes_endpoint(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/quotes")
    assert r.status_code == 200
    data = r.json()
    assert data[0]["code"] == "000001"
    assert "price" in data[0]


def test_global_quotes_endpoint(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/global-quotes")
    assert r.status_code == 200
    data = r.json()
    assert data["quotes"][0]["name"] == "SK海力士"
    assert data["quotes"][0]["price"] == 180000.0
    assert data["quotes"][1]["name"] == "三星电子"
    assert data["quotes"][1]["change_pct"] == 1.23


def test_global_semiconductors_endpoint(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/global-semiconductors")
    assert r.status_code == 200
    data = r.json()
    assert data["quotes"][0]["trend"]["points"][0]["time"] == "2026-07-14 08:00"
    assert data["archive"][0]["name"] == "SK海力士"


def test_kline_endpoint(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/kline?code=000001")
    assert r.status_code == 200
    k = r.json()
    assert len(k["ohlc"]) == 39
    assert len(k["ma5"]) == 39


def test_daily_kline_endpoint(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/kline?code=000001&period=daily")
    assert r.status_code == 200
    k = r.json()
    assert k["period"] == "daily"
    assert len(k["ohlc"]) == 240
    assert len(k["zx_short"]) == 240


def test_kline_rejects_unknown_period(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/kline?code=000001&period=week")
    assert r.status_code == 400


def test_signals_endpoint(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/signals?limit=10")
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["rule"] == "rsi_extreme"


def test_index_served(tmp_path):
    c = _client(tmp_path)
    r = c.get("/")
    assert r.status_code == 200
    assert "盯盘面板" in r.text
    assert "/korea" in r.text


def test_korea_page_served(tmp_path):
    c = _client(tmp_path)
    r = c.get("/korea")
    assert r.status_code == 200
    assert "韩芯双雄实时看盘" in r.text


def test_screen_history_endpoint(tmp_path):
    history = tmp_path / "screen_history.jsonl"
    history.write_text(
        '{"time":"2026-06-26 10:00:00","strategy":"zhixing","scope":"hs300","count":1,"selected":[{"code":"000001","name":"平安银行"}]}\n',
        encoding="utf-8",
    )
    c = _client(tmp_path, history_path=history)
    r = c.get("/api/screen/history?limit=5")
    assert r.status_code == 200
    rows = r.json()["history"]
    assert rows[0]["strategy"] == "zhixing"
    assert rows[0]["selected"][0]["code"] == "000001"


def test_news_endpoint(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/news?limit=5")
    assert r.status_code == 200
    data = r.json()
    assert data["items"][0]["title"] == "测试市场消息"
    assert data["sources"][0]["ok"] is True


def test_news_cache_endpoint(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/news/cache?limit=5")
    assert r.status_code == 200
    data = r.json()
    assert data["from_disk_cache"] is True
    assert data["items"] == []


def test_news_backtest_endpoints_without_event_store(tmp_path):
    c = _client(tmp_path)

    report = c.get("/api/news/backtest?days=30")
    assert report.status_code == 200
    assert report.json()["overview"]["evaluated_samples"] == 0

    status = c.get("/api/news/backtest/status")
    assert status.status_code == 200
    assert status.json()["running"] is False

    started = c.post("/api/news/backtest/run", json={"days": 30, "limit": 100})
    assert started.status_code == 200
    assert started.json()["started"] is False


def test_news_window_page(tmp_path):
    c = _client(tmp_path)
    r = c.get("/news-window")
    assert r.status_code == 200
    assert "市场雷达" in r.text


def test_add_watchlist_endpoint(tmp_path):
    manual = tmp_path / "watchlist.manual.toml"
    c = _client(tmp_path, manual_path=manual)
    r = c.post("/api/watchlist/add?code=600000")
    assert r.status_code == 200
    assert r.json()["item"] == {"code": "600000", "name": "浦发银行"}

    w = c.get("/api/watchlist").json()
    assert [row["code"] for row in w["watchlist"]] == ["000001", "600000"]

    q = c.get("/api/quotes").json()
    assert [row["code"] for row in q] == ["000001", "600000"]


def test_add_watchlist_rejects_invalid_code(tmp_path):
    c = _client(tmp_path, manual_path=tmp_path / "watchlist.manual.toml")
    r = c.post("/api/watchlist/add?code=abc")
    assert r.status_code == 400


def test_search_endpoint(tmp_path):
    c = _client(tmp_path, manual_path=tmp_path / "watchlist.manual.toml")
    r = c.get("/api/search?q=银行")
    assert r.status_code == 200
    assert r.json()["results"] == [
        {"code": "000001", "name": "平安银行"},
        {"code": "600000", "name": "浦发银行"},
    ]

    r2 = c.get("/api/search?q=6000")
    assert r2.status_code == 200
    assert r2.json()["results"] == [{"code": "600000", "name": "浦发银行"}]

    r3 = c.get("/api/search?q=")
    assert r3.status_code == 200
    assert r3.json()["results"] == []


def test_band_market_and_stock_endpoints(tmp_path):
    c = _client(tmp_path, manual_path=tmp_path / "watchlist.manual.toml")
    r = c.get("/api/band/market")
    assert r.status_code == 200
    body = r.json()
    assert body["temperature"] is None  # FakeSource 无涨跌家数接口,优雅降级
    assert body["index"] is None

    r2 = c.get("/api/band/stock?code=000001")
    assert r2.status_code == 200
    data = r2.json()
    assert data["code"] == "000001"
    assert data["name"] == "平安银行"
    assert isinstance(data["checks"], list) and len(data["checks"]) == 5
    assert data["verdict"]["title"]

    r3 = c.get("/api/band/stock?code=abc")
    assert r3.status_code == 400


class FakeRunner:
    def __init__(self):
        self._running = False
        self.started = []

    def start(self, strategy, scope=None):
        self.started.append((strategy, scope))
        return True

    def status(self):
        return {"running": self._running, "strategy": None, "scope": None, "last": {"ok": True, "count": 5}}


def _runner_client(tmp_path, runner):
    cfg = Config(watchlist=[{"code": "000001", "name": "平安银行"}], rules={"rsi_period": 14})
    svc = DashboardService(FakeSource(), cfg)
    log = tmp_path / "triggers.jsonl"
    log.write_text("", encoding="utf-8")
    return TestClient(create_app(svc, log_path=log, screen_runner=runner))


def test_strategies_endpoint(tmp_path):
    c = _runner_client(tmp_path, FakeRunner())
    r = c.get("/api/strategies")
    assert r.status_code == 200
    ids = {s["id"] for s in r.json()["strategies"]}
    assert {"default", "zhixing"} <= ids
    assert r.json()["default"] == "zhixing"


def test_scopes_endpoint(tmp_path):
    c = _runner_client(tmp_path, FakeRunner())
    r = c.get("/api/scopes")
    assert r.status_code == 200
    ids = {s["id"] for s in r.json()["scopes"]}
    assert {"star_chinext", "main_board", "hs300", "star50", "zz500"} <= ids


def test_backtest_rules_endpoint(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/backtest/rules")
    assert r.status_code == 200
    ids = {rule["id"] for rule in r.json()["rules"]}
    assert {"ma_cross", "macd_cross", "rsi_extreme", "zhixing_pick_close"} <= ids
    assert r.json()["default"] == "ma_cross"


def test_backtest_endpoint(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/backtest?code=000001&rule=rsi_extreme&days=365&forward=5&cost=0")
    assert r.status_code == 200
    data = r.json()
    assert data["code"] == "000001"
    assert data["name"] == "平安银行"
    assert data["rule"] == "rsi_extreme"
    assert data["bars"] == 259
    assert "win_rate" in data["stats"]
    assert "trades" in data


def test_backtest_batch_endpoint(tmp_path):
    c = _client(tmp_path)
    r = c.post(
        "/api/backtest/batch",
        json={
            "stocks": [{"code": "000001", "name": "平安银行"}, {"code": "600000", "name": "浦发银行"}],
            "rule": "rsi_extreme",
            "days": 365,
            "forward": 5,
            "cost": 0,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "batch"
    assert data["summary"]["stocks"] == 2
    assert data["summary"]["ok"] == 2
    assert {row["code"] for row in data["results"]} == {"000001", "600000"}
    assert "trades" in data["results"][0]


def test_backtest_rejects_bad_params(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/backtest?code=abc").status_code == 400
    assert c.get("/api/backtest?code=000001&rule=nope").status_code == 400


def test_screen_run_passes_strategy_and_scope(tmp_path):
    runner = FakeRunner()
    c = _runner_client(tmp_path, runner)
    r = c.post("/api/screen/run?strategy=default&scope=hs300")
    assert r.status_code == 200 and r.json()["started"] is True
    assert runner.started == [("default", "hs300")]

    s = c.get("/api/screen/status")
    assert s.json()["last"]["count"] == 5


def test_screen_run_rejects_unknown_strategy(tmp_path):
    runner = FakeRunner()
    c = _runner_client(tmp_path, runner)
    r = c.post("/api/screen/run?strategy=nope")
    assert r.json()["started"] is False
    assert runner.started == []  # 未知方案不触发


def test_screen_run_rejects_unknown_scope(tmp_path):
    runner = FakeRunner()
    c = _runner_client(tmp_path, runner)
    r = c.post("/api/screen/run?scope=nope")
    assert r.json()["started"] is False
    assert runner.started == []


def test_screen_run_without_runner_returns_error(tmp_path):
    c = _client(tmp_path)  # 未配置 runner
    r = c.post("/api/screen/run")
    assert r.json()["started"] is False
