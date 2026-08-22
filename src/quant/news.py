"""Market news aggregation for the web dashboard.

The functions here normalize several AkShare 7x24 news endpoints into a
stable JSON shape for the frontend. Individual sources are allowed to fail:
news is useful context, not a reason for the dashboard to stop responding.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from .datasource.akshare_source import _install_requests_defaults


@dataclass(frozen=True)
class NewsSourceSpec:
    id: str
    label: str
    function_name: str
    title_col: str | None
    summary_col: str
    time_col: str
    url_col: str | None = None


NEWS_SOURCES = (
    NewsSourceSpec("eastmoney", "东方财富", "stock_info_global_em", "标题", "摘要", "发布时间", "链接"),
    NewsSourceSpec("sina", "新浪财经", "stock_info_global_sina", None, "内容", "时间", None),
    NewsSourceSpec("ths", "同花顺", "stock_info_global_ths", "标题", "内容", "发布时间", "链接"),
    NewsSourceSpec("futu", "富途牛牛", "stock_info_global_futu", "标题", "内容", "发布时间", "链接"),
)

TAG_KEYWORDS = {
    "A股": ("A股", "沪指", "深成指", "创业板", "科创", "涨停", "跌停", "上市公司"),
    "政策": ("国务院", "发改委", "财政部", "央行", "证监会", "政策", "监管", "关税"),
    "AI": ("AI", "人工智能", "算力", "大模型", "智算", "机器人"),
    "芯片": ("芯片", "半导体", "存储", "晶圆", "光刻", "封测"),
    "新能源": ("新能源", "光伏", "储能", "锂电", "电池", "风电"),
    "消费": ("消费", "白酒", "食品", "医药", "旅游", "零售"),
    "海外": ("美股", "纳指", "道指", "美元", "美联储", "港股", "欧洲"),
}

CONCEPT_STOCKS = {
    "AI": {
        "keywords": ("AI", "人工智能", "大模型", "算力", "智算", "数据中心", "服务器"),
        "stocks": ("中际旭创", "新易盛", "浪潮信息", "中科曙光", "科大讯飞", "海光信息", "寒武纪"),
    },
    "芯片": {
        "keywords": ("芯片", "半导体", "存储", "晶圆", "光刻", "封测", "集成电路"),
        "stocks": ("中芯国际", "北方华创", "中微公司", "兆易创新", "韦尔股份", "寒武纪", "海光信息"),
    },
    "新能源": {
        "keywords": ("新能源", "光伏", "储能", "锂电", "电池", "组件", "风电", "充电桩"),
        "stocks": ("宁德时代", "比亚迪", "隆基绿能", "阳光电源", "通威股份", "晶科能源", "亿纬锂能"),
    },
    "汽车": {
        "keywords": ("汽车", "车企", "整车", "智能驾驶", "无人驾驶", "固态电池", "闪充"),
        "stocks": ("比亚迪", "赛力斯", "长安汽车", "长城汽车", "江淮汽车", "拓普集团"),
    },
    "机器人": {
        "keywords": ("机器人", "人形机器人", "减速器", "伺服", "工业自动化"),
        "stocks": ("机器人", "埃斯顿", "汇川技术", "绿的谐波", "拓斯达", "双环传动"),
    },
    "低空经济": {
        "keywords": ("低空经济", "无人机", "eVTOL", "通航", "飞行汽车"),
        "stocks": ("万丰奥威", "中信海直", "航天彩虹", "中无人机", "宗申动力"),
    },
    "金融": {
        "keywords": ("券商", "银行", "保险", "金融", "降准", "降息", "成交额", "牛市"),
        "stocks": ("东方财富", "中信证券", "同花顺", "招商银行", "中国平安", "中金公司"),
    },
    "地产": {
        "keywords": ("房地产", "地产", "房贷", "按揭", "商品房", "城中村"),
        "stocks": ("保利发展", "万科A", "招商蛇口", "金地集团", "华发股份"),
    },
    "医药": {
        "keywords": ("医药", "创新药", "医疗器械", "医保", "药品", "临床", "疫苗"),
        "stocks": ("恒瑞医药", "药明康德", "迈瑞医疗", "爱尔眼科", "智飞生物"),
    },
    "消费": {
        "keywords": ("消费", "白酒", "食品", "饮料", "旅游", "零售", "免税"),
        "stocks": ("贵州茅台", "五粮液", "伊利股份", "中国中免", "海天味业"),
    },
    "有色": {
        "keywords": ("有色", "铜", "铝", "黄金", "稀土", "锂矿", "金价"),
        "stocks": ("紫金矿业", "中国铝业", "北方稀土", "山东黄金", "赣锋锂业", "天齐锂业"),
    },
    "能源": {
        "keywords": ("原油", "石油", "煤炭", "天然气", "电力", "油价"),
        "stocks": ("中国石油", "中国石化", "中国海油", "中国神华", "陕西煤业", "长江电力"),
    },
    "军工": {
        "keywords": ("军工", "航空发动机", "航天", "军贸", "导弹", "卫星"),
        "stocks": ("中航沈飞", "航发动力", "中航西飞", "中国卫星", "内蒙一机"),
    },
}

POSITIVE_KEYWORDS = (
    "上涨",
    "涨停",
    "大涨",
    "高开",
    "增长",
    "预增",
    "扭亏",
    "中标",
    "签约",
    "订单",
    "合同",
    "获批",
    "回购",
    "增持",
    "分红",
    "释放产能",
    "产能释放",
    "正式启用",
    "政策支持",
    "降准",
    "降息",
    "减税",
    "突破",
)

NEGATIVE_KEYWORDS = (
    "下跌",
    "跌停",
    "大跌",
    "低开",
    "回落",
    "亏损",
    "预亏",
    "减持",
    "处罚",
    "立案",
    "调查",
    "退市",
    "风险警示",
    "停牌",
    "暂停",
    "终止",
    "取消",
    "诉讼",
    "债务",
    "破产",
    "违约",
    "解禁",
)

GLM_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
GLM_MODEL = "glm-4.7"
GLM_SYSTEM_PROMPT = """你是一名A股事件驱动研究员，擅长把实时财经新闻映射到A股标的并判断事件影响。

你必须按以下专业框架分析：
1. 先判断新闻事实类型：公司公告、订单/合同、政策监管、行业景气、宏观海外、风险事件、市场价格波动。
2. 只在系统给出的候选股票中选择相关标的；候选股票由系统从全A股票池召回，并可能带有用户自选标记，不要自行编造股票代码或名称。
3. 区分直接影响与间接映射：
   - 公司自身公告、订单、处罚、减持、业绩等为直接影响，相关度最高。
   - 产业链、同行、题材映射为间接影响，必须降低置信度。
   - 纯海外市场、宏观消息若无法明确传导到候选股票，应判为中性或低置信度。
4. 对每个保留股票判断未来1到5个交易日的事件情绪：
   - 利好：订单/合同/中标、业绩超预期、回购增持、政策支持、价格上涨利好上游、需求扩张、产能释放、产品突破。
   - 利空：处罚/立案/退市风险、减持、业绩亏损或低于预期、需求收缩、价格下跌伤害利润、禁令/制裁、停牌、债务违约。
   - 中性：影响方向不清楚、传导链太长、或消息已偏宏观且缺少明确受益/受损主体。
5. 宁可少选，不要为了凑数强行关联。若候选与新闻没有明确传导关系，返回空数组。每条新闻最多保留3只股票。

输出要求：
- 只能输出合法JSON，不要Markdown，不要解释过程。
- sentiment 只能是“利好”“利空”“中性”。
- sentiment_direction 只能是 positive、negative、neutral。
- confidence 为0到1的小数。
- impact_score 为 -100 到 100，利空为负，利好为正，中性接近0。
- reason 用中文，40字以内，说明核心因果。"""


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    return re.sub(r"\s+", " ", str(value)).strip()


def _parse_time(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime().replace(tzinfo=None)


def _clip(text: str, length: int) -> str:
    text = _text(text)
    if len(text) <= length:
        return text
    return text[:length].rstrip() + "..."


def _split_headline(content: str) -> tuple[str, str]:
    content = _text(content)
    match = re.match(r"^[【\[]([^】\]]{2,80})[】\]]\s*(.*)$", content)
    if match:
        return _text(match.group(1)), _text(match.group(2)) or content
    title = re.split(r"[。；;!！?？]", content, maxsplit=1)[0]
    return _clip(title, 42), content


def _tags(title: str, summary: str) -> list[str]:
    haystack = f"{title} {summary}".upper()
    out: list[str] = []
    for tag, words in TAG_KEYWORDS.items():
        if any(word.upper() in haystack for word in words):
            out.append(tag)
    return out[:4]


def _dedupe_key(title: str, summary: str) -> str:
    base = title or summary
    return re.sub(r"[\W_]+", "", base.lower())[:90]


def _normalize_stock_universe(stock_universe: Any) -> list[dict]:
    if stock_universe is None:
        return []
    if isinstance(stock_universe, pd.DataFrame):
        records = stock_universe.to_dict("records")
    else:
        records = list(stock_universe)
    out: list[dict] = []
    seen = set()
    for rec in records:
        code = _text(rec.get("code") if isinstance(rec, dict) else "")
        name = _text(rec.get("name") if isinstance(rec, dict) else "")
        code = re.sub(r"^(sh|sz|bj)", "", code, flags=re.I).zfill(6)
        if not (code.isdigit() and len(code) == 6 and name):
            continue
        key = (code, name)
        if key in seen:
            continue
        seen.add(key)
        out.append({"code": code, "name": name, "in_watchlist": bool(rec.get("in_watchlist")) if isinstance(rec, dict) else False})
    return out


def _classify_sentiment(title: str, summary: str) -> dict:
    text = f"{title} {summary}"
    pos = sum(2 if word in title else 1 for word in POSITIVE_KEYWORDS if word in text)
    neg = sum(2 if word in title else 1 for word in NEGATIVE_KEYWORDS if word in text)
    score = pos - neg
    if score > 0:
        return {"label": "利好", "direction": "positive", "score": score}
    if score < 0:
        return {"label": "利空", "direction": "negative", "score": score}
    return {"label": "中性", "direction": "neutral", "score": 0}


def _concept_matches(title: str, summary: str, tags: list[str]) -> list[tuple[str, int]]:
    haystack = f"{title} {summary}"
    upper = haystack.upper()
    out: list[tuple[str, int]] = []
    for concept, spec in CONCEPT_STOCKS.items():
        score = 0
        for word in spec["keywords"]:
            needle = word.upper()
            if needle in upper:
                score += 14 if word in title else 8
        if concept in tags:
            score += 8
        if score:
            out.append((concept, score))
    return sorted(out, key=lambda row: row[1], reverse=True)


def _stock_alias(name: str) -> str:
    return re.sub(r"^(?:\*?ST|N|C)", "", name, flags=re.I).strip()


def _related_stocks(
    title: str,
    summary: str,
    tags: list[str],
    stock_universe: Any,
    max_stocks: int = 3,
) -> list[dict]:
    stocks = _normalize_stock_universe(stock_universe)
    if not stocks:
        return []
    haystack = f"{title} {summary}"
    upper = haystack.upper()
    by_name = {s["name"]: s for s in stocks}
    by_code = {s["code"]: s for s in stocks}
    scores: dict[str, dict] = {}

    def add(stock: dict, points: int, reason: str) -> None:
        code = stock["code"]
        row = scores.setdefault(
            code,
            {
                "code": code,
                "name": stock["name"],
                "score": 0,
                "reasons": [],
                "in_watchlist": bool(stock.get("in_watchlist")),
            },
        )
        row["score"] += points
        if stock.get("in_watchlist"):
            row["score"] += 12
            reason = f"自选/{reason}"
        if reason not in row["reasons"]:
            row["reasons"].append(reason)

    for code in re.findall(r"(?<!\d)([0368]\d{5})(?!\d)", haystack):
        stock = by_code.get(code)
        if stock:
            add(stock, 120, "代码命中")

    for stock in stocks:
        name = stock["name"]
        alias = _stock_alias(name)
        candidates = [name]
        if alias and alias != name:
            candidates.append(alias)
        if len(alias) >= 4 and alias.endswith(("股份", "科技", "集团")):
            candidates.append(alias[:-2])
        for candidate in set(candidates):
            if len(candidate) < 3:
                continue
            if candidate in title:
                add(stock, 100, "标题提及")
                break
            if candidate in summary:
                add(stock, 70, "正文提及")
                break

    for concept, concept_score in _concept_matches(title, summary, tags):
        for idx, name in enumerate(CONCEPT_STOCKS[concept]["stocks"]):
            stock = by_name.get(name)
            if stock:
                add(stock, max(10, concept_score - idx * 2), concept)

    ranked = sorted(scores.values(), key=lambda row: (row["score"], len(row["reasons"])), reverse=True)
    sentiment = _classify_sentiment(title, summary)
    return [
        {
            "code": row["code"],
            "name": row["name"],
            "score": int(row["score"]),
            "sentiment": sentiment["label"],
            "sentiment_direction": sentiment["direction"],
            "reason": " / ".join(row["reasons"][:3]),
            "in_watchlist": bool(row.get("in_watchlist")),
        }
        for row in ranked[: max(1, min(max_stocks, 6))]
    ]


def _extract_json_object(text: str) -> dict | None:
    text = _text(text)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _glm_chat_completion(
    messages: list[dict],
    *,
    api_key: str,
    model: str,
    endpoint: str,
    timeout: float,
    max_tokens: int = 8192,
) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"GLM HTTP {e.code}: {detail}") from e
    data = json.loads(body)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("GLM 返回为空")
    return _text((choices[0].get("message") or {}).get("content"))


def _build_glm_user_prompt(batch: list[dict]) -> str:
    payload = []
    for item in batch:
        payload.append(
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "summary": item.get("summary"),
                "tags": item.get("tags") or [],
                "candidate_stocks": [
                    {
                        "code": s.get("code"),
                        "name": s.get("name"),
                        "reason": s.get("reason"),
                        "rule_score": s.get("score"),
                        "in_watchlist": bool(s.get("in_watchlist")),
                    }
                    for s in item.get("related_stocks", [])
                ],
            }
        )
    return (
        "请对以下实时资讯进行A股相关标的筛选与事件情绪判断。\n"
        "返回JSON格式必须为："
        '{"items":[{"id":"原id","sentiment":"利好/利空/中性",'
        '"sentiment_direction":"positive/negative/neutral","related_stocks":['
        '{"code":"000001","name":"股票名","sentiment":"利好/利空/中性",'
        '"sentiment_direction":"positive/negative/neutral","confidence":0.0,'
        '"impact_score":0,"reason":"40字以内原因"}]}]}\n'
        "candidate_stocks 是系统从全A股票池中召回的候选；只允许从 candidate_stocks 中选择股票。"
        "没有明确相关股票时 related_stocks 返回空数组，整条 sentiment 返回中性。\n"
        f"待分析数据：{json.dumps({'items': payload}, ensure_ascii=False)}"
    )


def _validate_glm_sentiment(value: str, fallback: str = "中性") -> str:
    return value if value in {"利好", "利空", "中性"} else fallback


def _sentiment_direction(label: str, fallback: str = "neutral") -> str:
    return {"利好": "positive", "利空": "negative", "中性": "neutral"}.get(label, fallback)


def _merge_glm_analysis(items: list[dict], analysis: dict, model: str) -> None:
    by_id = {str(item.get("id")): item for item in items}
    for result in analysis.get("items") or []:
        item = by_id.get(str(result.get("id")))
        if not item:
            continue
        allowed = {s["code"]: s for s in item.get("related_stocks", [])}
        related: list[dict] = []
        for raw in result.get("related_stocks") or []:
            code = _text(raw.get("code")).zfill(6)
            original = allowed.get(code)
            if not original:
                continue
            label = _validate_glm_sentiment(_text(raw.get("sentiment")), original.get("sentiment", "中性"))
            direction = _sentiment_direction(label, original.get("sentiment_direction", "neutral"))
            try:
                confidence = max(0.0, min(float(raw.get("confidence", 0.5)), 1.0))
            except (TypeError, ValueError):
                confidence = 0.5
            try:
                impact_score = max(-100, min(int(raw.get("impact_score", 0)), 100))
            except (TypeError, ValueError):
                impact_score = 0
            related.append(
                {
                    "code": code,
                    "name": original["name"],
                    "score": original.get("score", 0),
                    "sentiment": label,
                    "sentiment_direction": direction,
                    "confidence": confidence,
                    "impact_score": impact_score,
                    "reason": _clip(raw.get("reason") or original.get("reason", ""), 44),
                    "in_watchlist": bool(original.get("in_watchlist")),
                }
            )
        item_label = _validate_glm_sentiment(_text(result.get("sentiment")), item.get("sentiment", "中性"))
        if related:
            related.sort(key=lambda row: (abs(row["impact_score"]), row["confidence"], row["score"]), reverse=True)
            strongest = related[0]
            item_label = strongest["sentiment"]
            item["sentiment_direction"] = strongest["sentiment_direction"]
            item["sentiment_score"] = strongest["impact_score"]
        else:
            item["sentiment_direction"] = _sentiment_direction(item_label)
            item["sentiment_score"] = 0
        item["sentiment"] = item_label
        item["related_stocks"] = related
        item["analysis_source"] = model


def analyze_news_with_glm(items: list[dict], max_related: int = 3) -> list[dict]:
    api_key = (
        os.environ.get("ZAI_API_KEY")
        or os.environ.get("BIGMODEL_API_KEY")
        or os.environ.get("GLM_API_KEY")
    )
    if not api_key:
        return items
    if os.environ.get("GLM_NEWS_ENABLED", "1").lower() in {"0", "false", "no", "off"}:
        return items

    model = os.environ.get("GLM_NEWS_MODEL", GLM_MODEL)
    endpoint = os.environ.get("GLM_NEWS_ENDPOINT", GLM_ENDPOINT)
    timeout = float(os.environ.get("GLM_NEWS_TIMEOUT", "10"))
    budget = max(1.0, float(os.environ.get("GLM_NEWS_BUDGET", "18")))
    batch_size = max(1, min(int(os.environ.get("GLM_NEWS_BATCH_SIZE", "3")), 10))
    max_items = max(1, min(int(os.environ.get("GLM_NEWS_MAX_ITEMS", "9")), 120))
    pending = [item for item in items if item.get("related_stocks")][:max_items]
    deadline = time.monotonic() + budget

    for start in range(0, len(pending), batch_size):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            for item in pending[start:]:
                item["analysis_error"] = "GLM 分析超过总耗时预算，保留规则结果"
            break
        batch = pending[start : start + batch_size]
        messages = [
            {"role": "system", "content": GLM_SYSTEM_PROMPT},
            {"role": "user", "content": _build_glm_user_prompt(batch)},
        ]
        try:
            content = _glm_chat_completion(
                messages,
                api_key=api_key,
                model=model,
                endpoint=endpoint,
                timeout=min(timeout, max(1.0, remaining)),
            )
            parsed = _extract_json_object(content)
            if parsed:
                _merge_glm_analysis(batch, parsed, model)
        except Exception as e:
            for item in batch:
                item["analysis_error"] = str(e)[:160]
            break
        for item in batch:
            item["related_stocks"] = item.get("related_stocks", [])[:max_related]
    return items


def enrich_news_items(
    items: list[dict],
    stock_universe: Any = None,
    max_related: int = 3,
    use_llm: bool | None = None,
) -> list[dict]:
    """Attach sentiment and related A-share candidates to normalized news items."""

    for item in items:
        title = _text(item.get("title"))
        summary = _text(item.get("summary"))
        tags = list(item.get("tags") or [])
        sentiment = _classify_sentiment(title, summary)
        item["sentiment"] = sentiment["label"]
        item["sentiment_direction"] = sentiment["direction"]
        item["sentiment_score"] = sentiment["score"]
        item["related_stocks"] = _related_stocks(title, summary, tags, stock_universe, max_related)
        item["analysis_source"] = "rules"
    if use_llm is not False:
        analyze_news_with_glm(items, max_related=max_related)
    return items


def _normalize_source_frame(spec: NewsSourceSpec, raw: pd.DataFrame) -> list[dict]:
    if raw is None or raw.empty:
        return []
    rows: list[dict] = []
    for rec in raw.to_dict("records"):
        summary = _text(rec.get(spec.summary_col))
        title = _text(rec.get(spec.title_col)) if spec.title_col else ""
        if not title:
            title, summary = _split_headline(summary)
        if not title and not summary:
            continue
        published = _parse_time(rec.get(spec.time_col))
        published_at = published.strftime("%Y-%m-%d %H:%M:%S") if published else ""
        url = _text(rec.get(spec.url_col)) if spec.url_col else ""
        rows.append(
            {
                "id": _dedupe_key(title, summary),
                "title": _clip(title, 80),
                "summary": _clip(summary, 260),
                "published_at": published_at,
                "time": published.strftime("%H:%M") if published else "",
                "_date": published.date().isoformat() if published else "",
                "source": spec.label,
                "source_id": spec.id,
                "sources": [spec.label],
                "url": url,
                "tags": _tags(title, summary),
                "publications": [
                    {
                        "source": spec.label,
                        "source_id": spec.id,
                        "published_at": published_at,
                        "url": url,
                    }
                ],
            }
        )
    return rows


def _fetch_source(spec: NewsSourceSpec) -> list[dict]:
    _install_requests_defaults()
    import akshare as ak

    fn = getattr(ak, spec.function_name)
    return _normalize_source_frame(spec, fn())


def get_market_news(
    limit: int = 80,
    today_only: bool = True,
    now: datetime | None = None,
    overall_timeout: float = 12.0,
    stock_universe: Any = None,
    max_related: int = 3,
) -> dict:
    """Fetch and normalize latest market news.

    A source can fail or time out without failing the endpoint. If today's
    filter leaves no rows, the response falls back to the latest available rows.
    """

    # Web responses still cap at 120; the service requests a larger internal
    # batch so the event archive also retains intraday news that has scrolled
    # out of the compact desktop window.
    limit = max(1, min(int(limit), 500))
    now = now or datetime.now()
    today = now.date().isoformat()
    items: list[dict] = []
    errors: list[dict] = []
    source_status = {
        spec.id: {"id": spec.id, "label": spec.label, "ok": False, "count": 0}
        for spec in NEWS_SOURCES
    }

    executor = ThreadPoolExecutor(max_workers=len(NEWS_SOURCES), thread_name_prefix="market-news")
    futures = {executor.submit(_fetch_source, spec): spec for spec in NEWS_SOURCES}
    completed = set()
    try:
        for fut in as_completed(futures, timeout=overall_timeout):
            completed.add(fut)
            spec = futures[fut]
            try:
                rows = fut.result()
            except Exception as e:  # network/source errors should be visible but non-fatal
                errors.append({"source": spec.label, "error": str(e)})
                continue
            source_status[spec.id]["ok"] = True
            source_status[spec.id]["count"] = len(rows)
            items.extend(rows)
    except FuturesTimeout:
        pass
    finally:
        for fut, spec in futures.items():
            if fut not in completed:
                errors.append({"source": spec.label, "error": "请求超时"})
        executor.shutdown(wait=False, cancel_futures=True)

    deduped: dict[str, dict] = {}
    for item in items:
        key = item["id"] or _dedupe_key(item["title"], item["summary"])
        if key in deduped:
            existing = deduped[key]
            existing.setdefault("publications", []).extend(item.get("publications") or [])
            existing["publications"] = sorted(
                {
                    (
                        str(row.get("source_id") or ""),
                        str(row.get("published_at") or ""),
                        str(row.get("url") or ""),
                    ): row
                    for row in existing["publications"]
                }.values(),
                key=lambda row: row.get("published_at") or "",
            )
            publication_times = [row.get("published_at") for row in existing["publications"] if row.get("published_at")]
            if publication_times:
                first_published = min(publication_times)
                existing["published_at"] = first_published
                existing["time"] = first_published[11:16]
                existing["_date"] = first_published[:10]
            if item["source"] not in existing["sources"]:
                existing["sources"].append(item["source"])
                existing["source"] = " / ".join(existing["sources"])
            if not existing.get("url") and item.get("url"):
                existing["url"] = item["url"]
            continue
        deduped[key] = item

    rows = sorted(
        deduped.values(),
        key=lambda item: item.get("published_at") or "",
        reverse=True,
    )
    today_rows = [item for item in rows if item.get("_date") == today]
    fallback_latest = bool(today_only and not today_rows and rows)
    if today_only and today_rows:
        rows = today_rows
    rows = rows[:limit]
    for item in rows:
        item.pop("_date", None)
    enrich_news_items(rows, stock_universe=stock_universe, max_related=max_related)

    return {
        "date": today,
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "today_only": today_only,
        "fallback_latest": fallback_latest,
        "items": rows,
        "sources": list(source_status.values()),
        "errors": errors,
    }
