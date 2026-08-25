import json
from datetime import datetime, timedelta

import pandas as pd

from quant.news_backtest import NewsEventStore, _content_features, evaluate_news_event


def _minute_bars(start: str, closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": pd.date_range(start, periods=len(closes), freq="min"),
            "open": [100.0] * len(closes),
            "high": [max(100.0, close) + 0.05 for close in closes],
            "low": [min(100.0, close) - 0.05 for close in closes],
            "close": closes,
            "volume": [1000] * len(closes),
        }
    )


def _news_item(index: int = 0, direction: str = "positive") -> dict:
    published = datetime.now().replace(microsecond=0) - timedelta(hours=2, minutes=index)
    return {
        "id": f"news-{index}",
        "published_at": published.strftime("%Y-%m-%d %H:%M:%S"),
        "title": f"测试资讯 {index}",
        "summary": "用于验证消息事件研究",
        "source": "测试资讯源",
        "source_id": "test-source",
        "tags": ["半导体"],
        "sentiment": "利好" if direction == "positive" else "利空",
        "sentiment_direction": direction,
        "sentiment_score": 3,
        "analysis_source": "glm",
        "impact_level": "B",
        "impact_label": "较强",
        "impact_score_adjusted": 62,
        "related_stocks": [
            {
                "code": "000001",
                "name": "平安银行",
                "score": 105,
                "confidence": 0.88,
                "impact_score": 3,
                "sentiment": "利好" if direction == "positive" else "利空",
                "sentiment_direction": direction,
                "reason": "标题提及公司名称",
            }
        ],
    }


def test_evaluate_positive_news_uses_first_future_minute_open():
    bars = _minute_bars("2026-08-11 10:01:00", [100.1 + index * 0.1 for index in range(10)])
    result = evaluate_news_event(
        {"published_at": "2026-08-11 10:00:30", "sentiment_direction": "positive"},
        bars,
        now=datetime(2026, 8, 11, 10, 20),
    )

    assert result["status"] == "evaluated"
    assert result["anchor_at"] == "2026-08-11 10:01:00"
    assert result["entry_price"] == 100.0
    assert result["return_1m"] == 0.1
    assert result["return_10m"] == 1.0
    assert result["directional_return_10m"] == 1.0
    assert result["direction_correct"] == 1
    assert result["session_type"] == "盘中"


def test_evaluate_negative_news_normalizes_return_direction():
    bars = _minute_bars("2026-08-11 10:01:00", [99.95 - index * 0.1 for index in range(10)])
    result = evaluate_news_event(
        {"published_at": "2026-08-11 10:00:30", "sentiment_direction": "negative"},
        bars,
        now=datetime(2026, 8, 11, 10, 20),
    )

    assert result["return_10m"] == -0.95
    assert result["directional_return_10m"] == 0.95
    assert result["direction_correct"] == 1


def test_evaluate_after_hours_news_moves_to_next_trading_minute():
    bars = _minute_bars("2026-08-12 09:30:00", [100.0] * 10)
    result = evaluate_news_event(
        {"published_at": "2026-08-11 15:10:00", "sentiment_direction": "positive"},
        bars,
        now=datetime(2026, 8, 12, 10, 0),
    )

    assert result["status"] == "evaluated"
    assert result["anchor_at"] == "2026-08-12 09:30:00"
    assert result["session_type"] == "次日开盘"


def test_content_features_are_derived_from_news_text():
    features = _content_features(
        {"title": "公司中标重大合同并拟回购股份", "summary": "订单金额创历史新高"}
    )
    assert "订单中标" in features
    assert "增持回购" in features
    assert "业绩增长" in features


def test_event_store_archives_reports_and_calibrates_weights(tmp_path):
    store = NewsEventStore(tmp_path / "news-events.sqlite3")
    payload = {"items": [_news_item(index) for index in range(12)]}
    assert store.archive_payload(payload) == 12

    candidates = store.list_candidates(days=30, limit=100)
    assert len(candidates) == 12
    for candidate in candidates:
        store.save_result(
            candidate,
            {
                "status": "evaluated",
                "anchor_at": candidate["published_at"],
                "session_type": "盘中",
                "entry_price": 10,
                "return_1m": 0.1,
                "return_3m": 0.2,
                "return_5m": 0.3,
                "return_10m": 0.6,
                "mfe_10m": 0.7,
                "mae_10m": -0.1,
                "directional_return_10m": 0.6,
                "direction_correct": 1,
                "effectiveness": "显著有效",
                "observed_level": "B",
            },
        )

    report = store.report(30)
    assert report["overview"]["archived_events"] == 12
    assert report["overview"]["evaluated_samples"] == 12
    assert report["overview"]["hit_rate"] == 100.0
    assert report["horizons"][-1]["avg_signed_return"] == 0.6
    assert not {"资讯源", "分析方式"} & {row["dimension"] for row in report["weights"]}
    assert {"内容事件", "内容标签", "影响链路", "判断方向"} <= {
        row["dimension"] for row in report["weights"]
    }

    current = [_news_item(99)]
    store.apply_impact_levels(current)
    assert current[0]["impact_basis"] == "内容回测校准"
    assert current[0]["impact_calibration_factor"] > 1
    assert current[0]["impact_level"] in {"A", "B", "C", "D"}

    export_path = store.write_export(30)
    exported = json.loads(export_path.read_text("utf-8"))
    assert exported["source_usage"] == "仅作溯源元数据，不参与影响权重计算"
    assert len(exported["records"]) == 12
    assert exported["records"][0]["return_10m"] == 0.6
    assert exported["records"][0]["content_features"] == ["其他事件"]


def test_event_store_freezes_first_seen_prediction(tmp_path):
    store = NewsEventStore(tmp_path / "news-events.sqlite3")
    original = _news_item()
    store.archive_payload({"items": [original]})

    revised = _news_item()
    revised["impact_level"] = "A"
    revised["impact_label"] = "重大"
    revised["impact_score_adjusted"] = 99
    revised["sentiment_direction"] = "negative"
    revised["related_stocks"][0]["sentiment_direction"] = "negative"
    revised["related_stocks"].append(
        {
            "code": "600000",
            "name": "浦发银行",
            "sentiment_direction": "negative",
            "reason": "后续新增关联",
        }
    )
    store.archive_payload({"items": [revised]})

    candidates = store.list_candidates(days=30, limit=10)
    assert len(candidates) == 1
    assert candidates[0]["code"] == "000001"
    assert candidates[0]["impact_level"] == "B"
    assert candidates[0]["sentiment_direction"] == "positive"


def test_story_cluster_uses_first_cross_source_publication(tmp_path):
    store = NewsEventStore(tmp_path / "news-events.sqlite3")
    first = _news_item(0)
    first.update(
        {
            "id": "source-a",
            "published_at": (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
            "title": "华星科技中标5亿元人工智能数据中心项目",
            "summary": "公司获得重大项目订单",
            "source": "来源A",
            "source_id": "a",
            "publications": [
                {
                    "source": "来源A",
                    "source_id": "a",
                    "published_at": (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
                    "url": "https://a.test/1",
                }
            ],
        }
    )
    later = _news_item(1)
    later.update(
        {
            "id": "source-b",
            "published_at": (datetime.now() - timedelta(hours=1, minutes=55)).strftime("%Y-%m-%d %H:%M:%S"),
            "title": "华星科技：签署5亿元AI数据中心重大项目合同",
            "summary": "公司确认获得数据中心订单",
            "source": "来源B",
            "source_id": "b",
            "publications": [
                {
                    "source": "来源B",
                    "source_id": "b",
                    "published_at": (datetime.now() - timedelta(hours=1, minutes=55)).strftime("%Y-%m-%d %H:%M:%S"),
                    "url": "https://b.test/1",
                }
            ],
        }
    )

    store.archive_payload({"items": [later, first]})
    rebuilt = store.rebuild_story_clusters(30)
    candidates = store.list_candidates(30, 20)
    report = store.report(30)
    exported = store.export_dataset(30)

    assert rebuilt["stories"] == 1
    assert rebuilt["duplicates"] == 1
    assert len(candidates) == 1
    assert candidates[0]["published_at"] == first["published_at"]
    assert report["overview"]["archived_events"] == 1
    assert report["overview"]["archived_publications"] == 2
    assert report["overview"]["duplicate_publications"] == 1
    assert {record["story_id"] for record in exported["records"]} == {candidates[0]["story_id"]}
    assert {record["status"] for record in exported["records"]} == {"pending", "duplicate_publication"}


def test_late_arriving_earlier_publication_becomes_primary_and_invalidates_result(tmp_path):
    store = NewsEventStore(tmp_path / "news-events.sqlite3")
    later = _news_item(0)
    later.update(
        {
            "id": "source-b-later",
            "published_at": (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
            "title": "华星科技签署5亿元数据中心项目合同",
            "source": "来源B",
            "source_id": "b",
        }
    )
    earlier = _news_item(1)
    earlier.update(
        {
            "id": "source-a-earlier",
            "published_at": (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
            "title": "华星科技签署5亿元数据中心项目合同",
            "source": "来源A",
            "source_id": "a",
        }
    )

    store.archive_payload({"items": [later]})
    initial = store.list_candidates(30, 10)[0]
    store.save_result(
        initial,
        {
            "status": "evaluated",
            "anchor_at": later["published_at"],
            "entry_price": 10,
            "return_10m": 0.5,
            "directional_return_10m": 0.5,
            "direction_correct": 1,
            "effectiveness": "有效",
        },
    )

    store.archive_payload({"items": [earlier]})
    candidates = store.list_candidates(30, 10)
    exported = store.export_dataset(30)

    assert len(candidates) == 1
    assert candidates[0]["title"] == earlier["title"]
    assert candidates[0]["published_at"] == earlier["published_at"]
    assert store.report(30)["overview"]["evaluated_samples"] == 0
    assert {record["status"] for record in exported["records"]} == {"pending", "duplicate_publication"}


def test_story_cluster_keeps_distinct_company_events_separate(tmp_path):
    store = NewsEventStore(tmp_path / "news-events.sqlite3")
    increase = _news_item(0)
    increase.update({"id": "increase", "title": "平安银行股东增持500万股"})
    decrease = _news_item(1)
    decrease.update({"id": "decrease", "title": "平安银行股东减持500万股"})

    store.archive_payload({"items": [increase, decrease]})
    rebuilt = store.rebuild_story_clusters(30)

    assert rebuilt["stories"] == 2


def test_story_cluster_does_not_merge_similar_earnings_from_different_companies(tmp_path):
    store = NewsEventStore(tmp_path / "news-events.sqlite3")
    first = _news_item(0)
    first.update({"id": "company-a", "title": "甲公司：上半年净利润1.2亿元，同比增长20%"})
    second = _news_item(1)
    second.update({"id": "company-b", "title": "乙公司：上半年净利润1.3亿元，同比增长22%"})

    store.archive_payload({"items": [first, second]})
    rebuilt = store.rebuild_story_clusters(30)

    assert rebuilt["stories"] == 2


def test_story_cluster_does_not_merge_different_metrics_from_same_institution(tmp_path):
    store = NewsEventStore(tmp_path / "news-events.sqlite3")
    production = _news_item(0)
    production.update({"id": "production", "title": "乘联分会：7月乘用车生产222.2万辆 同比下降1.6%"})
    retail = _news_item(1)
    retail.update({"id": "retail", "title": "乘联分会：7月新能源乘用车市场零售95.1万辆 同比下降3.9%"})

    store.archive_payload({"items": [production, retail]})
    rebuilt = store.rebuild_story_clusters(30)

    assert rebuilt["stories"] == 2


def test_story_cluster_keeps_different_company_actions_separate(tmp_path):
    store = NewsEventStore(tmp_path / "news-events.sqlite3")
    dividend = _news_item(0)
    dividend.update({"id": "dividend", "title": "富祥股份：2026年半年度拟每10股派发现金红利0.5元"})
    earnings = _news_item(1)
    earnings.update({"id": "earnings", "title": "富祥股份：上半年净利润1.77亿元，同比扭亏为盈"})

    store.archive_payload({"items": [dividend, earnings]})
    rebuilt = store.rebuild_story_clusters(30)

    assert rebuilt["stories"] == 2


def test_story_cluster_merges_currency_converted_republication(tmp_path):
    store = NewsEventStore(tmp_path / "news-events.sqlite3")
    usd = _news_item(0)
    usd.update({"id": "usd", "title": "众望布艺：全资子公司收到美国关税退税约142.61万美元"})
    cny = _news_item(1)
    cny.update({"id": "cny", "title": "众望布艺：全资子公司收到美国关税退税约968万元"})

    store.archive_payload({"items": [usd, cny]})
    rebuilt = store.rebuild_story_clusters(30)

    assert rebuilt["stories"] == 1


def test_story_cluster_keeps_changed_market_price_levels_separate(tmp_path):
    store = NewsEventStore(tmp_path / "news-events.sqlite3")
    first_level = _news_item(0)
    first_level.update({"id": "level-89", "title": "布伦特原油期货突破89美元"})
    next_level = _news_item(1)
    next_level.update({"id": "level-90", "title": "布伦特原油期货突破90美元/桶 日内涨超2.6%"})

    store.archive_payload({"items": [first_level, next_level]})
    rebuilt = store.rebuild_story_clusters(30)

    assert rebuilt["stories"] == 2
