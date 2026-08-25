from datetime import datetime

import pandas as pd

from quant.news import NewsSourceSpec, _normalize_source_frame, enrich_news_items, get_market_news


def test_normalize_source_frame_splits_sina_headline():
    spec = NewsSourceSpec("sina", "新浪财经", "unused", None, "内容", "时间")
    raw = pd.DataFrame(
        [
            {
                "时间": "2026-06-30 09:31:00",
                "内容": "【A股开盘】沪指高开，AI方向活跃。",
            }
        ]
    )
    rows = _normalize_source_frame(spec, raw)
    assert rows[0]["title"] == "A股开盘"
    assert rows[0]["time"] == "09:31"
    assert "A股" in rows[0]["tags"]
    assert rows[0]["publications"] == [
        {
            "source": "新浪财经",
            "source_id": "sina",
            "published_at": "2026-06-30 09:31:00",
            "url": "",
        }
    ]


def test_get_market_news_falls_back_when_no_today_rows(monkeypatch):
    def fake_fetch(spec):
        return [
            {
                "id": "old",
                "title": "昨日消息",
                "summary": "摘要",
                "published_at": "2026-06-29 15:00:00",
                "time": "15:00",
                "_date": "2026-06-29",
                "source": spec.label,
                "source_id": spec.id,
                "sources": [spec.label],
                "url": "",
                "tags": [],
            }
        ]

    monkeypatch.setattr("quant.news._fetch_source", fake_fetch)
    data = get_market_news(limit=3, today_only=True, now=datetime(2026, 6, 30, 10, 0), overall_timeout=3)
    assert data["fallback_latest"] is True
    assert data["items"][0]["title"] == "昨日消息"


def test_enrich_news_items_adds_related_stocks_without_llm():
    universe = pd.DataFrame(
        [
            {"code": "002594", "name": "比亚迪"},
            {"code": "300750", "name": "宁德时代"},
        ]
    )
    items = [
        {
            "id": "n1",
            "title": "比亚迪签订海外电池订单",
            "summary": "公司订单快速增长，新能源需求扩张。",
            "tags": ["新能源"],
        }
    ]
    enrich_news_items(items, stock_universe=universe, use_llm=False)
    assert items[0]["sentiment"] == "利好"
    assert items[0]["related_stocks"][0]["code"] == "002594"
    assert items[0]["related_stocks"][0]["sentiment"] == "利好"


def test_enrich_news_items_uses_glm_and_rejects_non_candidates(monkeypatch):
    universe = pd.DataFrame(
        [
            {"code": "002594", "name": "比亚迪"},
            {"code": "300750", "name": "宁德时代"},
        ]
    )

    def fake_glm(messages, **kwargs):
        assert kwargs["api_key"] == "fake-key"
        assert "candidate_stocks" in messages[-1]["content"]
        return """
        {
          "items": [
            {
              "id": "n1",
              "sentiment": "利好",
              "sentiment_direction": "positive",
              "related_stocks": [
                {"code": "002594", "name": "比亚迪", "sentiment": "利好", "sentiment_direction": "positive", "confidence": 0.86, "impact_score": 72, "reason": "订单增长提升业绩预期"},
                {"code": "999999", "name": "幻觉股票", "sentiment": "利好", "sentiment_direction": "positive", "confidence": 0.9, "impact_score": 90, "reason": "不应接受"}
              ]
            }
          ]
        }
        """

    monkeypatch.setenv("ZAI_API_KEY", "fake-key")
    monkeypatch.setattr("quant.news._glm_chat_completion", fake_glm)
    items = [
        {
            "id": "n1",
            "title": "比亚迪签订海外电池订单",
            "summary": "公司订单快速增长，新能源需求扩张。",
            "tags": ["新能源"],
        }
    ]
    enrich_news_items(items, stock_universe=universe)
    assert items[0]["analysis_source"] == "glm-4.7"
    assert items[0]["sentiment"] == "利好"
    assert [s["code"] for s in items[0]["related_stocks"]] == ["002594"]
    assert items[0]["related_stocks"][0]["confidence"] == 0.86
