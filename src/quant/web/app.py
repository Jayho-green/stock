"""FastAPI 应用:对外暴露面板所需的 JSON 接口,并托管静态前端。"""

from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import logstore
from ..config import load_config
from ..datasource.akshare_source import AkshareSource
from .service import DashboardService

STATIC_DIR = Path(__file__).parent / "static"
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LOG = ROOT / "data" / "triggers.jsonl"
DEFAULT_SCREEN_HISTORY = ROOT / "data" / "screen_history.jsonl"
DEFAULT_KLINE_CACHE = ROOT / "data" / "kline_cache"
DEFAULT_GLOBAL_ARCHIVE = ROOT / "data" / "korea_close_archive.jsonl"
DEFAULT_NEWS_CACHE = ROOT / "data" / "news_cache.json"
DEFAULT_NEWS_EVENTS = ROOT / "data" / "news_events.sqlite3"


def create_app(
    service: DashboardService,
    log_path: Path = DEFAULT_LOG,
    screen_runner=None,
    screen_history_path: Path | None = None,
    lhb_service=None,
) -> FastAPI:
    app = FastAPI(title="A股盯盘面板")

    @app.middleware("http")
    async def no_cache(request, call_next):
        resp = await call_next(request)
        if request.url.path in ("/", "/lhb", "/korea", "/news-window") or request.url.path.startswith("/static"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/api/quotes")
    def quotes():
        return service.get_quotes()

    @app.get("/api/global-quotes")
    def global_quotes():
        return service.get_global_quotes()

    @app.get("/api/global-semiconductors")
    def global_semiconductors():
        return service.get_global_semiconductor_watch()

    @app.get("/api/strategies")
    def strategies():
        from ..strategies import DEFAULT_STRATEGY, list_strategies

        return {"strategies": list_strategies(), "default": DEFAULT_STRATEGY}

    @app.get("/api/scopes")
    def scopes():
        from ..screener import DEFAULT_SCOPE, list_scopes

        return {"scopes": list_scopes(), "default": DEFAULT_SCOPE}

    @app.get("/api/backtest/rules")
    def backtest_rules():
        return {"rules": service.get_backtest_rules(), "default": "ma_cross"}

    @app.get("/api/backtest")
    def backtest_run(
        code: str,
        rule: str = "ma_cross",
        days: int = 365,
        forward: int = 5,
        cost: float = 0.0013,
    ):
        code = str(code).strip()
        if not (code.isdigit() and len(code) == 6):
            raise HTTPException(status_code=400, detail="股票代码必须是 6 位数字")
        if forward < 1 or forward > 60:
            raise HTTPException(status_code=400, detail="持有天数必须在 1-60 之间")
        if days < forward + 30 or days > 2500:
            raise HTTPException(status_code=400, detail="回看天数必须大于持有天数且不超过 2500")
        if cost < 0 or cost > 0.1:
            raise HTTPException(status_code=400, detail="交易成本必须在 0-10% 之间")
        try:
            return service.run_backtest(code, rule, days=days, forward=forward, cost=cost)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/api/backtest/batch")
    def backtest_batch(payload: dict = Body(...)):
        stocks = payload.get("stocks") or []
        rule = str(payload.get("rule") or "ma_cross")
        try:
            days = int(payload.get("days", 365))
            forward = int(payload.get("forward", 5))
            cost = float(payload.get("cost", 0.0013))
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail="回测参数格式错误") from e
        if not isinstance(stocks, list):
            raise HTTPException(status_code=400, detail="stocks 必须是数组")
        if forward < 1 or forward > 60:
            raise HTTPException(status_code=400, detail="持有天数必须在 1-60 之间")
        if days < forward + 30 or days > 2500:
            raise HTTPException(status_code=400, detail="回看天数必须大于持有天数且不超过 2500")
        if cost < 0 or cost > 0.1:
            raise HTTPException(status_code=400, detail="交易成本必须在 0-10% 之间")
        try:
            return service.run_backtest_batch(stocks, rule, days=days, forward=forward, cost=cost)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/api/screen/run")
    def screen_run(strategy: str = "zhixing", scope: str | None = None):
        from ..screener import SCOPES
        from ..strategies import STRATEGIES

        if screen_runner is None:
            return {"started": False, "error": "未配置选股任务"}
        if strategy not in STRATEGIES:
            return {"started": False, "error": f"未知方案: {strategy}"}
        if scope is not None and scope not in SCOPES:
            return {"started": False, "error": f"未知范围: {scope}"}
        return {"started": screen_runner.start(strategy, scope), "strategy": strategy, "scope": scope}

    @app.get("/api/screen/status")
    def screen_status():
        if screen_runner is None:
            return {"running": False, "strategy": None, "last": None}
        return screen_runner.status()

    @app.post("/api/screen/cancel")
    def screen_cancel():
        if screen_runner is None:
            return {"cancelled": False, "error": "选股功能未启用"}
        return {"cancelled": screen_runner.cancel()}

    @app.get("/api/screen/history")
    def screen_history(limit: int = 20):
        from ..screen_history import read_history

        path = screen_history_path or DEFAULT_SCREEN_HISTORY
        return {"history": read_history(path, max(1, min(limit, 100)))}

    @app.get("/api/news")
    def news(limit: int = 80, today: int = 1):
        return service.get_news(max(1, min(limit, 120)), bool(today))

    @app.get("/api/news/cache")
    def news_cache(limit: int = 80):
        return service.get_news_cache(max(1, min(limit, 120)))

    @app.get("/api/news/backtest")
    def news_backtest(days: int = 30):
        return service.get_news_backtest_report(max(1, min(days, 120)))

    @app.get("/api/news/backtest/status")
    def news_backtest_status():
        return service.get_news_backtest_status()

    @app.post("/api/news/backtest/run")
    def news_backtest_run(payload: dict = Body(default={})):
        try:
            days = max(1, min(int(payload.get("days", 30)), 120))
            limit = max(1, min(int(payload.get("limit", 500)), 3000))
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail="回测参数格式错误") from e
        return service.start_news_backtest(days=days, limit=limit)

    @app.get("/api/news/backtest/export")
    def news_backtest_export(days: int = 120):
        path = service.export_news_backtest_dataset(max(1, min(days, 3650)))
        if path is None:
            raise HTTPException(status_code=404, detail="未配置资讯事件库")
        return FileResponse(path, media_type="application/json", filename="news_backtest_dataset.json")

    @app.get("/api/kline")
    def kline(code: str, period: str = "minute"):
        if period not in {"minute", "daily"}:
            raise HTTPException(status_code=400, detail="period 仅支持 minute/daily")
        return service.get_kline(code, period)

    @app.post("/api/watchlist/add")
    def watchlist_add(code: str, name: str | None = None):
        try:
            return service.add_watchlist(code, name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/watchlist")
    def watchlist():
        return {"watchlist": service.get_watchlist()}

    @app.get("/api/search")
    def search_stocks(q: str, limit: int = 10):
        return {"results": service.search_stocks(q, max(1, min(limit, 20)))}

    @app.get("/api/band/market")
    def band_market():
        return service.get_band_market()

    @app.get("/api/band/stock")
    def band_stock(code: str, name: str | None = None):
        if not code.isdigit():
            raise HTTPException(status_code=400, detail="股票代码必须是6位数字")
        return service.get_band_stock(code, name)

    @app.get("/api/signals")
    def signals(limit: int = 50):
        rows = logstore.read_all(log_path)
        return list(reversed(rows))[:limit]

    @app.get("/api/lhb")
    def lhb_board(date: str | None = None):
        if lhb_service is None:
            raise HTTPException(status_code=503, detail="未配置龙虎榜服务")
        try:
            return lhb_service.get_board(date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"龙虎榜数据获取失败: {e}") from e

    @app.get("/api/lhb/trends")
    def lhb_trends(days: int = 30, end: str | None = None, period: str = "daily", top: int = 8):
        if lhb_service is None:
            raise HTTPException(status_code=503, detail="未配置龙虎榜服务")
        try:
            return lhb_service.get_sector_trends(days=days, end_date=end, period=period, top=top)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"龙虎榜趋势获取失败: {e}") from e

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/lhb")
    def lhb_page():
        return FileResponse(STATIC_DIR / "lhb.html")

    @app.get("/korea")
    def korea_page():
        return FileResponse(STATIC_DIR / "korea.html")

    @app.get("/news-window")
    def news_window_page():
        return FileResponse(STATIC_DIR / "news_window.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def build_default_app() -> FastAPI:
    """用真实数据源 + 配置构建应用(供 uvicorn 启动)。"""
    from ..screener import run_full_screen
    from .screen_runner import ScreenRunner

    cfg_path = ROOT / "config" / "config.toml"
    cfg_path = cfg_path if cfg_path.exists() else ROOT / "config" / "config.example.toml"
    cfg = load_config(cfg_path)
    generated = ROOT / "config" / "watchlist.generated.toml"
    manual = ROOT / "config" / "watchlist.manual.toml"
    checkpoint = ROOT / "data" / "screen.checkpoint.json"
    history = ROOT / "data" / "screen_history.jsonl"
    source = AkshareSource()
    # 面板名单随选股结果实时更新(get_quotes 每次合并手填+生成名单)
    service = DashboardService(
        source,
        cfg,
        generated_path=generated,
        manual_path=manual,
        kline_cache_path=DEFAULT_KLINE_CACHE,
        global_archive_path=DEFAULT_GLOBAL_ARCHIVE,
        news_cache_path=DEFAULT_NEWS_CACHE,
        news_events_path=DEFAULT_NEWS_EVENTS,
    )
    runner = ScreenRunner(
        lambda strategy, scope, progress, stop_check: run_full_screen(
            source,
            cfg.screen,
            generated,
            strategy=strategy,
            scope=scope,
            progress=progress,
            checkpoint_path=checkpoint,
            history_path=history,
            kline_cache_path=DEFAULT_KLINE_CACHE,
            stop_check=stop_check,
        )
    )
    from .lhb_service import LhbService

    lhb = LhbService(source, cache_dir=ROOT / "data" / "lhb_cache")
    return create_app(service, screen_runner=runner, screen_history_path=history, lhb_service=lhb)
