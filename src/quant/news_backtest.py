"""资讯事件归档、十分钟事件研究与经验权重校准。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import statistics
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd

HORIZONS = (1, 3, 5, 10)
MIN_CALIBRATION_SAMPLES = 12
STORY_EXACT_WINDOW = timedelta(hours=24)
STORY_SIMILAR_WINDOW = timedelta(hours=6)
CONTENT_EVENT_PATTERNS = (
    ("业绩增长", ("预增", "扭亏", "同比增长", "同比增", "利润增长", "营收增长", "创新高", "历史新高")),
    ("业绩承压", ("预减", "亏损", "同比下降", "同比降", "利润下降", "营收下降", "业绩下滑")),
    ("增持回购", ("增持", "回购")),
    ("减持解禁", ("减持", "解禁")),
    ("订单中标", ("中标", "订单", "重大合同", "签订合同", "签约")),
    ("并购重组", ("并购", "重组", "收购", "资产注入", "控制权变更")),
    ("融资事项", ("定增", "可转债", "配股", "发行股份", "融资")),
    ("政策支持", ("政策支持", "补贴", "扶持", "专项资金", "纳入医保", "税收优惠")),
    ("监管处罚", ("处罚", "立案", "调查", "监管函", "警示函", "行政监管")),
    ("诉讼风险", ("诉讼", "仲裁", "违约", "冻结", "被执行", "破产")),
    ("产品技术", ("新产品", "研发", "专利", "获批", "认证", "量产", "技术突破")),
    ("产能供需", ("产能", "扩产", "停产", "复产", "供需", "库存")),
    ("价格变化", ("涨价", "降价", "提价", "价格上调", "价格下调")),
    ("资本市场", ("停牌", "复牌", "大宗交易", "涨停", "跌停")),
    ("人事变动", ("辞职", "离任", "任命", "董事长", "总经理变更")),
    ("宏观地缘", ("央行", "利率", "汇率", "关税", "制裁", "冲突", "战争", "原油", "贸易")),
)


def _event_key(item: dict) -> str:
    raw = "|".join(
        [
            str(item.get("published_at") or ""),
            str(item.get("source_id") or ""),
            str(item.get("id") or ""),
            str(item.get("title") or ""),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _relation_type(reason: str) -> str:
    text = str(reason or "")
    if any(key in text for key in ("代码命中", "标题提及")):
        return "直接"
    if "正文提及" in text:
        return "正文"
    return "题材"


def _content_features(item: dict) -> list[str]:
    """从新闻正文中提取可回测的事件类型，不使用资讯来源。"""

    text = " ".join(
        str(item.get(field) or "")
        for field in ("title", "summary")
    )
    features = [label for label, keywords in CONTENT_EVENT_PATTERNS if any(keyword in text for keyword in keywords)]
    return features or ["其他事件"]


def _tags(item: dict) -> list[str]:
    tags = item.get("tags")
    if tags is None:
        try:
            tags = json.loads(item.get("tags_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            tags = []
    return [str(tag) for tag in (tags or []) if str(tag).strip()]


def _direction_key(value: Any) -> str:
    return {"positive": "利好判断", "negative": "利空判断", "neutral": "中性判断"}.get(str(value), "中性判断")


def _strip_news_prefix(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(
        r"^(?:(?:预告|快讯|外媒|媒体|报道称)\s*[：:]?\s*|"
        r"韩媒(?:《[^》]{1,24}》)?\s*[：:]?\s*|"
        r"据[^：:]{1,18}(?:报道|消息)\s*[：:]?\s*)+",
        "",
        text,
    )


def _story_text(value: Any) -> str:
    text = _strip_news_prefix(value).lower().replace("...", "")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff%+-]+", "", text)


def _story_codes(item: dict) -> set[str]:
    if item.get("codes") is not None:
        return {str(code).zfill(6) for code in (item.get("codes") or []) if str(code).strip()}
    return {
        str(stock.get("code") or "").zfill(6)
        for stock in (item.get("related_stocks") or [])
        if str(stock.get("code") or "").strip()
    }


def _char_bigrams(text: str) -> set[str]:
    return {text[index : index + 2] for index in range(max(0, len(text) - 1))}


def _story_entity(value: Any) -> str:
    title = _strip_news_prefix(value)
    generic = {"市场消息", "最新消息", "数据显示", "据悉", "公告", "预告", "公司", "机构"}
    if "：" in title or ":" in title:
        entity = _story_text(re.split(r"[：:]", title, maxsplit=1)[0])
        if 2 <= len(entity) <= 22 and entity not in generic:
            return entity
    match = re.match(
        r"^(.{2,22}?)(?:今日|股票交易|将于|将|计划|拟|宣布|发布|推出|重启|"
        r"获|签署|中标|取得|完成|上半年|前三季度|预计)",
        title,
    )
    entity = _story_text(match.group(1)) if match else ""
    return "" if entity in generic else entity


def _story_numbers(text: str) -> set[str]:
    out = set()
    for number, unit in re.findall(
        r"(\d+(?:\.\d+)?)(%|亿元|万美元|万元|万股|万辆|万台|美元|港元|欧元|吨|股|元|台|人|点|年|月|日)?",
        text,
    ):
        if unit in {"年", "月", "日"}:
            continue
        try:
            value = float(number)
        except ValueError:
            continue
        if not unit and 1900 <= value <= 2100 and value.is_integer():
            continue
        if unit or "." in number or value > 12:
            out.add(f"{value:g}{unit}")
    return out


def _story_numeric_values(text: str) -> list[tuple[float, str]]:
    values = []
    for number, unit in re.findall(
        r"(\d+(?:\.\d+)?)(%|亿元|万美元|万元|美元|港元|欧元|元)", text
    ):
        try:
            values.append((float(number), unit))
        except ValueError:
            continue
    return values


def _numbers_compatible(left: str, right: str) -> bool:
    left_values = _story_numeric_values(left)
    right_values = _story_numeric_values(right)
    for left_value, left_unit in left_values:
        for right_value, right_unit in right_values:
            if left_unit == right_unit:
                scale = max(abs(left_value), abs(right_value), 1e-9)
                if abs(left_value - right_value) / scale <= 0.05:
                    return True
            currency_scale = {
                "亿元": 100_000_000.0,
                "万元": 10_000.0,
                "元": 1.0,
                "万美元": 68_000.0,
                "美元": 6.8,
            }
            if left_unit in currency_scale and right_unit in currency_scale:
                left_amount = left_value * currency_scale[left_unit]
                right_amount = right_value * currency_scale[right_unit]
                scale = max(abs(left_amount), abs(right_amount), 1e-9)
                if abs(left_amount - right_amount) / scale <= 0.15:
                    return True
    return False


def _has_story_conflict(left: str, right: str) -> bool:
    opposites = (
        ("增长", "下降"),
        ("同比增", "同比降"),
        ("扭亏", "亏损"),
        ("增持", "减持"),
        ("上涨", "下跌"),
        ("突破", "转跌"),
        ("涨超", "转跌"),
        ("继续发布", "解除"),
        ("获批", "撤回"),
        ("中标", "终止"),
    )
    return any((a in left and b in right) or (b in left and a in right) for a, b in opposites)


def _story_similarity(left: dict, right: dict) -> tuple[float, str]:
    """计算跨来源标题的故事相似度，来源名称本身不参与。"""

    left_text = _story_text(left.get("title") or left.get("summary"))
    right_text = _story_text(right.get("title") or right.get("summary"))
    if min(len(left_text), len(right_text)) < 8 or _has_story_conflict(left_text, right_text):
        return 0.0, "不同事件"
    left_entity = _story_entity(left.get("title"))
    right_entity = _story_entity(right.get("title"))
    if left_entity and right_entity and left_entity != right_entity:
        return 0.0, "主体不同"
    if left_text == right_text:
        return 1.0, "标题一致"
    if min(len(left_text), len(right_text)) >= 14 and (left_text in right_text or right_text in left_text):
        return 0.96, "标题包含"

    number_pattern = r"\d+(?:\.\d+)?(?:%|亿元|万美元|万元|万股|万辆|万台|美元|港元|欧元|吨|股|元|台|人|点|年|月|日)?"
    left_skeleton = re.sub(number_pattern, "", left_text)
    right_skeleton = re.sub(number_pattern, "", right_text)
    skeleton_similarity = SequenceMatcher(None, left_skeleton, right_skeleton).ratio()
    left_numbers = _story_numbers(left_text)
    right_numbers = _story_numbers(right_text)
    compatible_numbers = bool(left_numbers & right_numbers) or _numbers_compatible(left_text, right_text)
    dynamic_levels = ("突破", "跌破", "升至", "降至", "涨超", "跌超", "触及")
    if (
        any(term in left_text and term in right_text for term in dynamic_levels)
        and left_numbers
        and right_numbers
        and not (left_numbers & right_numbers)
    ):
        return 0.0, "动态价位变化"
    if (
        left_entity
        and left_entity == right_entity
        and skeleton_similarity >= 0.88
        and (compatible_numbers or not left_numbers or not right_numbers)
    ):
        return 0.90, "主体+语义骨架"

    sequence = SequenceMatcher(None, left_text, right_text).ratio()
    left_grams = _char_bigrams(left_text)
    right_grams = _char_bigrams(right_text)
    union = left_grams | right_grams
    jaccard = len(left_grams & right_grams) / len(union) if union else 0.0
    score = max(sequence, jaccard)

    left_codes = _story_codes(left)
    right_codes = _story_codes(right)
    code_overlap = bool(left_codes & right_codes)
    if code_overlap:
        score += 0.05
    if left_entity and left_entity == right_entity:
        score += 0.12
    feature_overlap = bool((set(_content_features(left)) & set(_content_features(right))) - {"其他事件"})
    if feature_overlap:
        score += 0.03
    if left_numbers and right_numbers:
        score += 0.04 if compatible_numbers else -0.18
    score = max(0.0, min(1.0, score))
    method = "关联股票+内容相似" if code_overlap else "内容相似"
    return score, method


def _publication_rows(item: dict) -> list[dict]:
    rows = list(item.get("publications") or [])
    if not rows:
        rows = [
            {
                "source": item.get("source") or "",
                "source_id": item.get("source_id") or "",
                "published_at": item.get("published_at") or "",
                "url": item.get("url") or "",
            }
        ]
    unique = {}
    for row in rows:
        normalized = {
            "source": str(row.get("source") or ""),
            "source_id": str(row.get("source_id") or ""),
            "published_at": str(row.get("published_at") or ""),
            "url": str(row.get("url") or ""),
        }
        key = (normalized["source_id"], normalized["published_at"], normalized["url"])
        unique[key] = normalized
    return sorted(unique.values(), key=lambda row: row["published_at"])


def _impact_grade(score: float) -> tuple[str, str]:
    if score >= 75:
        return "A", "重大"
    if score >= 55:
        return "B", "较强"
    if score >= 35:
        return "C", "一般"
    return "D", "弱"


def evaluate_news_event(event: dict, bars: pd.DataFrame, now: datetime | None = None) -> dict:
    """按消息后的首个可交易分钟开盘价计算未来 1/3/5/10 根分钟线。"""

    now = now or datetime.now()
    published = pd.to_datetime(event.get("published_at"), errors="coerce")
    if pd.isna(published):
        return {"status": "unavailable", "error": "消息时间无效"}
    if bars is None or bars.empty or "datetime" not in bars.columns:
        return {"status": "pending", "error": "暂无分钟数据"}

    frame = bars.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["datetime", "open", "high", "low", "close"])
    frame = frame[(frame["open"] > 0) & (frame["high"] > 0) & (frame["low"] > 0) & (frame["close"] > 0)]
    frame = frame.sort_values("datetime").reset_index(drop=True)
    if frame.empty:
        return {"status": "pending", "error": "分钟数据为空"}

    future = frame[frame["datetime"] > published].head(max(HORIZONS)).reset_index(drop=True)
    if future.empty:
        if now - published.to_pydatetime() > timedelta(days=10):
            return {"status": "unavailable", "error": "消息已超出分钟数据可回溯范围"}
        return {"status": "pending", "error": "等待消息后的分钟数据"}
    anchor = future.iloc[0]["datetime"]
    if anchor.to_pydatetime() - published.to_pydatetime() > timedelta(days=10):
        return {"status": "unavailable", "error": "消息与首个可交易分钟间隔过长"}
    if len(future) < max(HORIZONS):
        return {"status": "pending", "error": f"仅有 {len(future)} 根后续分钟线"}

    entry_price = float(future.iloc[0]["open"])
    returns = {
        horizon: (float(future.iloc[horizon - 1]["close"]) / entry_price - 1.0) * 100
        for horizon in HORIZONS
    }
    window = future.iloc[: max(HORIZONS)]
    mfe = (float(window["high"].max()) / entry_price - 1.0) * 100
    mae = (float(window["low"].min()) / entry_price - 1.0) * 100
    direction = str(event.get("sentiment_direction") or "neutral")
    expected_sign = 1 if direction == "positive" else -1 if direction == "negative" else 0
    directional_return = returns[10] * expected_sign
    correct = directional_return >= 0.05 if expected_sign else abs(returns[10]) <= 0.10
    if directional_return >= 0.50:
        effectiveness = "显著有效"
    elif directional_return >= 0.15:
        effectiveness = "有效"
    elif directional_return > -0.15:
        effectiveness = "无明显"
    else:
        effectiveness = "反向"

    observed_move = max(abs(mfe), abs(mae))
    observed_level = "A" if observed_move >= 1.0 else "B" if observed_move >= 0.5 else "C" if observed_move >= 0.2 else "D"
    published_time = published.time()
    same_day = anchor.date() == published.date()
    if not same_day:
        session_type = "次日开盘"
    elif published_time < datetime.strptime("09:30", "%H:%M").time():
        session_type = "盘前"
    elif datetime.strptime("11:30", "%H:%M").time() <= published_time < datetime.strptime("13:00", "%H:%M").time():
        session_type = "午间"
    elif published_time >= datetime.strptime("15:00", "%H:%M").time():
        session_type = "盘后"
    else:
        session_type = "盘中"

    return {
        "status": "evaluated",
        "anchor_at": anchor.strftime("%Y-%m-%d %H:%M:%S"),
        "session_type": session_type,
        "entry_price": round(entry_price, 4),
        "return_1m": round(returns[1], 4),
        "return_3m": round(returns[3], 4),
        "return_5m": round(returns[5], 4),
        "return_10m": round(returns[10], 4),
        "mfe_10m": round(mfe, 4),
        "mae_10m": round(mae, 4),
        "directional_return_10m": round(directional_return, 4),
        "direction_correct": int(correct),
        "effectiveness": effectiveness,
        "observed_level": observed_level,
        "error": "",
    }


class NewsEventStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS news_events (
                    event_id TEXT PRIMARY KEY,
                    original_id TEXT,
                    published_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT,
                    source TEXT,
                    source_id TEXT,
                    url TEXT,
                    tags_json TEXT,
                    sentiment TEXT,
                    sentiment_direction TEXT,
                    sentiment_score REAL,
                    analysis_source TEXT,
                    impact_level TEXT,
                    impact_label TEXT,
                    impact_score_adjusted REAL,
                    archived_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_news_events_published ON news_events(published_at);
                CREATE TABLE IF NOT EXISTS news_relations (
                    event_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT,
                    relation_rank INTEGER,
                    rule_score REAL,
                    confidence REAL,
                    model_impact_score REAL,
                    sentiment TEXT,
                    sentiment_direction TEXT,
                    reason TEXT,
                    relation_type TEXT,
                    in_watchlist INTEGER,
                    PRIMARY KEY(event_id, code),
                    FOREIGN KEY(event_id) REFERENCES news_events(event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_news_relations_code ON news_relations(code);
                CREATE TABLE IF NOT EXISTS news_event_results (
                    event_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    anchor_at TEXT,
                    session_type TEXT,
                    entry_price REAL,
                    return_1m REAL,
                    return_3m REAL,
                    return_5m REAL,
                    return_10m REAL,
                    mfe_10m REAL,
                    mae_10m REAL,
                    directional_return_10m REAL,
                    direction_correct INTEGER,
                    effectiveness TEXT,
                    observed_level TEXT,
                    error TEXT,
                    PRIMARY KEY(event_id, code)
                );
                CREATE INDEX IF NOT EXISTS idx_news_results_status ON news_event_results(status);
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(news_events)")}
            additions = {
                "story_id": "TEXT",
                "first_published_at": "TEXT",
                "story_similarity": "REAL",
                "story_match_method": "TEXT",
                "publications_json": "TEXT",
            }
            for name, column_type in additions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE news_events ADD COLUMN {name} {column_type}")
            connection.execute("UPDATE news_events SET story_id=event_id WHERE story_id IS NULL OR story_id='' ")
            connection.execute(
                "UPDATE news_events SET first_published_at=published_at "
                "WHERE first_published_at IS NULL OR first_published_at=''"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_news_events_story ON news_events(story_id, first_published_at)"
            )

    @staticmethod
    def _load_story_candidates(connection: sqlite3.Connection, cutoff: str) -> list[dict]:
        rows = connection.execute(
            """
            SELECT e.event_id, COALESCE(e.story_id,e.event_id) AS story_id,
                   e.published_at, COALESCE(e.first_published_at,e.published_at) AS first_published_at,
                   e.title, e.summary, GROUP_CONCAT(r.code) AS codes_csv
            FROM news_events e
            LEFT JOIN news_relations r ON r.event_id=e.event_id
            WHERE e.published_at >= ?
            GROUP BY e.event_id
            ORDER BY e.published_at ASC
            """,
            (cutoff,),
        ).fetchall()
        return [
            {
                **dict(row),
                "codes": [code for code in str(row["codes_csv"] or "").split(",") if code],
            }
            for row in rows
        ]

    @staticmethod
    def _find_story(item: dict, candidates: list[dict]) -> tuple[dict | None, float, str]:
        published = pd.to_datetime(item.get("published_at"), errors="coerce")
        if pd.isna(published):
            return None, 0.0, "无有效时间"
        best: dict | None = None
        best_score = 0.0
        best_method = "新事件"
        for candidate in reversed(candidates):
            candidate_time = pd.to_datetime(candidate.get("published_at"), errors="coerce")
            if pd.isna(candidate_time):
                continue
            age = abs(published.to_pydatetime() - candidate_time.to_pydatetime())
            if age > STORY_EXACT_WINDOW:
                continue
            score, method = _story_similarity(item, candidate)
            exact_match = method in {"标题一致", "标题包含"}
            code_overlap = bool(_story_codes(item) & _story_codes(candidate))
            same_entity = bool(_story_entity(item.get("title"))) and (
                _story_entity(item.get("title")) == _story_entity(candidate.get("title"))
            )
            threshold = 0.80 if same_entity else 0.72 if code_overlap else 0.76
            if not exact_match and (age > STORY_SIMILAR_WINDOW or score < threshold):
                continue
            if score > best_score:
                best = candidate
                best_score = score
                best_method = method
        return best, round(best_score, 4), best_method

    @staticmethod
    def _merge_publications(existing_json: str | None, item: dict) -> str:
        try:
            existing = json.loads(existing_json or "[]")
        except (json.JSONDecodeError, TypeError):
            existing = []
        return _json(_publication_rows({"publications": [*existing, *_publication_rows(item)]}))

    def archive_payload(self, data: dict) -> int:
        items = sorted(
            list(data.get("items") or []),
            key=lambda item: str(item.get("published_at") or ""),
        )
        if not items:
            return 0
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        valid_times = [pd.to_datetime(item.get("published_at"), errors="coerce") for item in items]
        valid_times = [value for value in valid_times if not pd.isna(value)]
        oldest = min(valid_times).to_pydatetime() if valid_times else datetime.now()
        candidate_cutoff = (oldest - STORY_EXACT_WINDOW).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as connection:
            story_candidates = self._load_story_candidates(connection, candidate_cutoff)
            for item in items:
                published_at = str(item.get("published_at") or "")
                if not published_at:
                    continue
                event_id = _event_key(item)
                existing = connection.execute(
                    "SELECT publications_json FROM news_events WHERE event_id=?", (event_id,)
                ).fetchone()
                if existing is not None:
                    connection.execute(
                        "UPDATE news_events SET publications_json=? WHERE event_id=?",
                        (self._merge_publications(existing["publications_json"], item), event_id),
                    )
                    continue

                publication_times = [
                    row["published_at"] for row in _publication_rows(item) if row.get("published_at")
                ]
                if publication_times:
                    published_at = min([published_at, *publication_times])
                item_for_match = {**item, "published_at": published_at}
                matched, similarity, match_method = self._find_story(item_for_match, story_candidates)
                matched_story_id = str(matched.get("story_id")) if matched else ""
                matched_first_at = (
                    str(matched.get("first_published_at") or matched.get("published_at")) if matched else ""
                )
                promote_to_primary = bool(matched and published_at < matched_first_at)
                story_id = event_id if promote_to_primary else matched_story_id or event_id
                first_published_at = min(
                    published_at,
                    matched_first_at if matched else published_at,
                )
                cursor = connection.execute(
                    """
                    INSERT INTO news_events (
                        event_id, original_id, published_at, title, summary, source, source_id, url,
                        tags_json, sentiment, sentiment_direction, sentiment_score, analysis_source,
                        impact_level, impact_label, impact_score_adjusted, archived_at,
                        story_id, first_published_at, story_similarity, story_match_method,
                        publications_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO NOTHING
                    """,
                    (
                        event_id,
                        str(item.get("id") or ""),
                        published_at,
                        str(item.get("title") or ""),
                        str(item.get("summary") or ""),
                        str(item.get("source") or ""),
                        str(item.get("source_id") or ""),
                        str(item.get("url") or ""),
                        _json(item.get("tags") or []),
                        str(item.get("sentiment") or "中性"),
                        str(item.get("sentiment_direction") or "neutral"),
                        _float(item.get("sentiment_score")),
                        str(item.get("analysis_source") or "rules"),
                        str(item.get("impact_level") or ""),
                        str(item.get("impact_label") or ""),
                        _float(item.get("impact_score_adjusted")),
                        now_text,
                        story_id,
                        first_published_at,
                        similarity if matched else 1.0,
                        match_method if matched else "首发",
                        _json(_publication_rows(item)),
                    ),
                )
                # 首次展示时的判断必须冻结，否则后续校准会回写历史预测，造成前视偏差。
                if cursor.rowcount == 0:
                    continue
                if promote_to_primary:
                    connection.execute(
                        "UPDATE news_events SET story_id=?, first_published_at=? WHERE story_id=? AND event_id<>?",
                        (story_id, first_published_at, matched_story_id, event_id),
                    )
                    connection.execute(
                        """
                        DELETE FROM news_event_results
                        WHERE event_id IN (SELECT event_id FROM news_events WHERE story_id=?)
                        """,
                        (story_id,),
                    )
                    for candidate in story_candidates:
                        if candidate.get("story_id") == matched_story_id:
                            candidate["story_id"] = story_id
                            candidate["first_published_at"] = first_published_at
                for rank, stock in enumerate(item.get("related_stocks") or [], start=1):
                    code = str(stock.get("code") or "").zfill(6)
                    if not (len(code) == 6 and code.isdigit()):
                        continue
                    reason = str(stock.get("reason") or "")
                    connection.execute(
                        """
                        INSERT INTO news_relations (
                            event_id, code, name, relation_rank, rule_score, confidence,
                            model_impact_score, sentiment, sentiment_direction, reason,
                            relation_type, in_watchlist
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(event_id, code) DO NOTHING
                        """,
                        (
                            event_id,
                            code,
                            str(stock.get("name") or code),
                            rank,
                            _float(stock.get("score")),
                            _float(stock.get("confidence"), -1.0),
                            _float(stock.get("impact_score")),
                            str(stock.get("sentiment") or item.get("sentiment") or "中性"),
                            str(stock.get("sentiment_direction") or item.get("sentiment_direction") or "neutral"),
                            reason,
                            _relation_type(reason),
                            int(bool(stock.get("in_watchlist"))),
                        ),
                    )
                story_candidates.append(
                    {
                        "event_id": event_id,
                        "story_id": story_id,
                        "published_at": published_at,
                        "first_published_at": first_published_at,
                        "title": item.get("title") or "",
                        "summary": item.get("summary") or "",
                        "codes": list(_story_codes(item)),
                    }
                )
        return len(items)

    def rebuild_story_clusters(self, days: int = 120) -> dict:
        """按发布时间重建故事簇，并移除非首发条目的旧回测结果。"""

        days = max(1, min(int(days), 3650))
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT e.event_id, e.published_at, e.title, e.summary,
                       GROUP_CONCAT(r.code) AS codes_csv
                FROM news_events e
                LEFT JOIN news_relations r ON r.event_id=e.event_id
                WHERE e.published_at >= ?
                GROUP BY e.event_id
                ORDER BY e.published_at ASC, e.event_id ASC
                """,
                (cutoff,),
            ).fetchall()
            candidates: list[dict] = []
            assignments = []
            for raw in rows:
                item = {
                    **dict(raw),
                    "codes": [code for code in str(raw["codes_csv"] or "").split(",") if code],
                }
                matched, similarity, match_method = self._find_story(item, candidates)
                story_id = str(matched.get("story_id")) if matched else str(item["event_id"])
                first_published_at = (
                    str(matched.get("first_published_at") or matched.get("published_at"))
                    if matched
                    else str(item["published_at"])
                )
                assignments.append(
                    (
                        story_id,
                        first_published_at,
                        similarity if matched else 1.0,
                        match_method if matched else "首发",
                        item["event_id"],
                    )
                )
                candidates.append(
                    {
                        **item,
                        "story_id": story_id,
                        "first_published_at": first_published_at,
                    }
                )
            connection.executemany(
                """
                UPDATE news_events
                SET story_id=?, first_published_at=?, story_similarity=?, story_match_method=?
                WHERE event_id=?
                """,
                assignments,
            )
            removed = connection.execute(
                """
                DELETE FROM news_event_results
                WHERE event_id IN (
                    SELECT event_id FROM news_events
                    WHERE event_id <> COALESCE(story_id,event_id)
                )
                """
            ).rowcount
            story_count = connection.execute(
                "SELECT COUNT(DISTINCT story_id) FROM news_events WHERE published_at >= ?", (cutoff,)
            ).fetchone()[0]
        self.write_export(days=days)
        return {
            "publications": len(rows),
            "stories": int(story_count),
            "duplicates": len(rows) - int(story_count),
            "removed_secondary_results": max(0, int(removed)),
        }

    def list_candidates(self, days: int = 30, limit: int = 500) -> list[dict]:
        cutoff = (datetime.now() - timedelta(days=max(1, days))).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT e.*, r.code, r.name, r.relation_rank, r.rule_score, r.confidence,
                       r.model_impact_score, r.reason, r.relation_type,
                       r.sentiment AS relation_sentiment,
                       r.sentiment_direction AS relation_direction
                FROM news_events e
                JOIN news_relations r ON r.event_id = e.event_id
                LEFT JOIN news_event_results x ON x.event_id = e.event_id AND x.code = r.code
                WHERE COALESCE(e.first_published_at,e.published_at) >= ?
                  AND e.event_id = COALESCE(e.story_id,e.event_id)
                  AND COALESCE(r.sentiment_direction, e.sentiment_direction) IN ('positive', 'negative')
                  AND x.event_id IS NULL
                ORDER BY COALESCE(e.first_published_at,e.published_at) ASC, r.relation_rank ASC
                LIMIT ?
                """,
                (cutoff, max(1, min(int(limit), 3000))),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["published_at"] = item.get("first_published_at") or item.get("published_at")
            item["sentiment_direction"] = item.get("relation_direction") or item.get("sentiment_direction")
            item["sentiment"] = item.get("relation_sentiment") or item.get("sentiment")
            out.append(item)
        return out

    def save_result(self, event: dict, result: dict) -> None:
        if result.get("status") == "pending":
            return
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO news_event_results (
                    event_id, code, status, evaluated_at, anchor_at, session_type, entry_price,
                    return_1m, return_3m, return_5m, return_10m, mfe_10m, mae_10m,
                    directional_return_10m, direction_correct, effectiveness, observed_level, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event["code"],
                    result.get("status", "unavailable"),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    result.get("anchor_at"),
                    result.get("session_type"),
                    result.get("entry_price"),
                    result.get("return_1m"),
                    result.get("return_3m"),
                    result.get("return_5m"),
                    result.get("return_10m"),
                    result.get("mfe_10m"),
                    result.get("mae_10m"),
                    result.get("directional_return_10m"),
                    result.get("direction_correct"),
                    result.get("effectiveness"),
                    result.get("observed_level"),
                    result.get("error", ""),
                ),
            )

    def _evaluated_rows(self, days: int) -> list[dict]:
        cutoff = (datetime.now() - timedelta(days=max(1, days))).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT e.event_id, COALESCE(e.first_published_at,e.published_at) AS published_at,
                       e.story_id, e.title, e.summary, e.source, e.source_id, e.tags_json,
                       e.analysis_source, e.sentiment_direction AS item_direction,
                       e.impact_level, r.code, r.name, r.reason, r.relation_type,
                       r.sentiment_direction, r.model_impact_score, r.confidence, r.rule_score,
                       x.*
                FROM news_event_results x
                JOIN news_events e ON e.event_id = x.event_id
                JOIN news_relations r ON r.event_id = x.event_id AND r.code = x.code
                WHERE COALESCE(e.first_published_at,e.published_at) >= ?
                  AND e.event_id = COALESCE(e.story_id,e.event_id)
                  AND x.status = 'evaluated'
                ORDER BY COALESCE(e.first_published_at,e.published_at) DESC
                """,
                (cutoff,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _calibration_groups(rows: list[dict]) -> list[dict]:
        groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for row in rows:
            for feature in _content_features(row):
                groups[("内容事件", feature)].append(row)
            groups[("影响链路", row.get("relation_type") or "题材")].append(row)
            direction = row.get("sentiment_direction") or row.get("item_direction") or "neutral"
            groups[("判断方向", _direction_key(direction))].append(row)
            for tag in _tags(row)[:6]:
                groups[("内容标签", tag)].append(row)

        output = []
        for (dimension, key), samples in groups.items():
            n = len(samples)
            hits = sum(int(sample.get("direction_correct") or 0) for sample in samples)
            signed = [_float(sample.get("directional_return_10m")) for sample in samples]
            raw_hit_rate = hits / n if n else 0.0
            bayes_hit_rate = (hits + 5) / (n + 10)
            avg_signed = statistics.fmean(signed) if signed else 0.0
            move_adjustment = max(-0.15, min(avg_signed / 1.0, 0.15))
            factor = max(0.65, min(1.35, 1.0 + (bayes_hit_rate - 0.5) * 1.2 + move_adjustment))
            output.append(
                {
                    "dimension": dimension,
                    "key": key,
                    "samples": n,
                    "hit_rate": round(raw_hit_rate * 100, 2),
                    "bayes_hit_rate": round(bayes_hit_rate * 100, 2),
                    "avg_signed_return_10m": round(avg_signed, 4),
                    "factor": round(factor, 3),
                    "status": "可采用" if n >= 30 else "观察中" if n >= MIN_CALIBRATION_SAMPLES else "样本不足",
                }
            )
        return sorted(output, key=lambda row: (row["samples"], abs(row["factor"] - 1)), reverse=True)

    def calibration(self, days: int = 30) -> list[dict]:
        return self._calibration_groups(self._evaluated_rows(days))

    def apply_impact_levels(self, items: list[dict], days: int = 30) -> list[dict]:
        calibrations = self.calibration(days)
        usable = {
            (row["dimension"], row["key"]): row
            for row in calibrations
            if row["samples"] >= MIN_CALIBRATION_SAMPLES
        }
        for item in items:
            related = list(item.get("related_stocks") or [])
            strongest = related[0] if related else {}
            raw_model_score = abs(_float(strongest.get("impact_score"), _float(item.get("sentiment_score"))))
            if raw_model_score <= 8:
                raw_model_score = min(65.0, raw_model_score * 22.0)
            raw_model_score = min(100.0, raw_model_score)
            rule_score = min(120.0, max(0.0, _float(strongest.get("score"))))
            confidence = _float(strongest.get("confidence"), -1.0)
            if confidence < 0:
                confidence = min(1.0, rule_score / 120.0)
            relation_type = _relation_type(strongest.get("reason", ""))
            directness = {"直接": 1.0, "正文": 0.78, "题材": 0.52}[relation_type]
            base_score = raw_model_score * 0.55 + confidence * 25.0 + directness * 20.0

            factors = []
            for dimension, key in [
                ("影响链路", relation_type),
                ("判断方向", _direction_key(item.get("sentiment_direction") or "neutral")),
            ]:
                if (dimension, key) in usable:
                    factors.append(usable[(dimension, key)]["factor"])
            content_features = _content_features(item)
            for feature in content_features:
                if ("内容事件", feature) in usable:
                    factors.append(usable[("内容事件", feature)]["factor"])
            for tag in _tags(item)[:6]:
                if ("内容标签", tag) in usable:
                    factors.append(usable[("内容标签", tag)]["factor"])
            calibration_factor = statistics.fmean(factors) if factors else 1.0
            adjusted = max(0.0, min(100.0, base_score * calibration_factor))
            level, label = _impact_grade(adjusted)
            item["impact_level"] = level
            item["impact_label"] = label
            item["impact_score_adjusted"] = round(adjusted, 1)
            item["impact_calibration_factor"] = round(calibration_factor, 3)
            item["impact_basis"] = "内容回测校准" if factors else "模型预估"
            item["content_features"] = content_features
        return items

    def export_dataset(self, days: int = 120) -> dict:
        """导出可直接交给分析模型的完整事件-股票-价格反应数据集。"""

        days = max(1, min(int(days), 3650))
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT e.event_id, COALESCE(e.story_id,e.event_id) AS story_id,
                       COALESCE(e.first_published_at,e.published_at) AS first_published_at,
                       e.published_at, e.story_similarity, e.story_match_method,
                       e.publications_json, e.original_id, e.title, e.summary,
                       e.source, e.source_id, e.url, e.tags_json, e.sentiment AS event_sentiment,
                       e.sentiment_direction AS event_direction, e.sentiment_score,
                       e.analysis_source, e.impact_level AS initial_impact_level,
                       e.impact_label AS initial_impact_label,
                       e.impact_score_adjusted AS initial_impact_score,
                       r.code, r.name, r.relation_rank, r.rule_score, r.confidence,
                       r.model_impact_score, r.sentiment AS relation_sentiment,
                       r.sentiment_direction AS relation_direction, r.reason, r.relation_type,
                       r.in_watchlist, x.status, x.evaluated_at, x.anchor_at, x.session_type,
                       x.entry_price, x.return_1m, x.return_3m, x.return_5m, x.return_10m,
                       x.mfe_10m, x.mae_10m, x.directional_return_10m,
                       x.direction_correct, x.effectiveness, x.observed_level, x.error
                FROM news_events e
                LEFT JOIN news_relations r ON r.event_id=e.event_id
                LEFT JOIN news_event_results x ON x.event_id=e.event_id AND x.code=r.code
                WHERE COALESCE(e.first_published_at,e.published_at) >= ?
                ORDER BY COALESCE(e.first_published_at,e.published_at) ASC,
                         e.published_at ASC, r.relation_rank ASC
                """,
                (cutoff,),
            ).fetchall()
        records = []
        for raw in rows:
            record = dict(raw)
            record["tags"] = _tags(record)
            record.pop("tags_json", None)
            try:
                publications = json.loads(record.pop("publications_json", None) or "[]")
            except (json.JSONDecodeError, TypeError):
                publications = []
            record["publications"] = publications or _publication_rows(record)
            record["content_features"] = _content_features(record)
            record["is_primary_publication"] = record.get("event_id") == record.get("story_id")
            if not record["is_primary_publication"]:
                record["status"] = "duplicate_publication"
            else:
                record["status"] = record.get("status") or "pending"
            records.append(record)
        story_count = len({record["story_id"] for record in records})
        return {
            "schema_version": 3,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "days": days,
            "story_count": story_count,
            "publication_record_count": len({record["event_id"] for record in records}),
            "source_publication_count": sum(
                len(record["publications"]) if record["publications"] else 1
                for record in records
                if record.get("code") is None or record.get("relation_rank") in (None, 1)
            ),
            "calibration_dimensions": ["内容事件", "内容标签", "影响链路", "判断方向"],
            "source_usage": "仅作溯源元数据，不参与影响权重计算",
            "story_methodology": "相似内容归为同一story_id，仅以first_published_at回测；后续来源只记录传播序列",
            "records": records,
        }

    def write_export(self, days: int = 120) -> Path:
        path = self.path.with_name("news_backtest_dataset.json")
        payload = self.export_dataset(days)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
        return path

    def report(self, days: int = 30) -> dict:
        days = max(1, min(int(days), 120))
        rows = self._evaluated_rows(days)
        calibrations = self._calibration_groups(rows)
        with self._connect() as connection:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            event_count = connection.execute(
                """
                SELECT COUNT(DISTINCT COALESCE(story_id,event_id)) FROM news_events
                WHERE COALESCE(first_published_at,published_at) >= ?
                """,
                (cutoff,),
            ).fetchone()[0]
            publication_payloads = connection.execute(
                """
                SELECT publications_json FROM news_events
                WHERE COALESCE(first_published_at,published_at) >= ?
                """,
                (cutoff,),
            ).fetchall()
            publication_count = 0
            for publication_row in publication_payloads:
                try:
                    publications = json.loads(publication_row["publications_json"] or "[]")
                except (json.JSONDecodeError, TypeError):
                    publications = []
                publication_count += max(1, len(publications))
            relation_count = connection.execute(
                """
                SELECT COUNT(*) FROM news_relations r JOIN news_events e ON e.event_id=r.event_id
                WHERE COALESCE(e.first_published_at,e.published_at) >= ?
                  AND e.event_id = COALESCE(e.story_id,e.event_id)
                  AND r.sentiment_direction IN ('positive','negative')
                """,
                (cutoff,),
            ).fetchone()[0]
            pending_count = connection.execute(
                """
                SELECT COUNT(*) FROM news_relations r
                JOIN news_events e ON e.event_id=r.event_id
                LEFT JOIN news_event_results x ON x.event_id=r.event_id AND x.code=r.code
                WHERE COALESCE(e.first_published_at,e.published_at) >= ?
                  AND e.event_id = COALESCE(e.story_id,e.event_id)
                  AND r.sentiment_direction IN ('positive','negative')
                  AND x.event_id IS NULL
                """,
                (cutoff,),
            ).fetchone()[0]

        samples = len(rows)
        hits = sum(int(row.get("direction_correct") or 0) for row in rows)
        signed = [_float(row.get("directional_return_10m")) for row in rows]
        overview = {
            "archived_events": int(event_count),
            "archived_publications": int(publication_count),
            "duplicate_publications": max(0, int(publication_count) - int(event_count)),
            "directional_relations": int(relation_count),
            "evaluated_samples": samples,
            "pending_samples": int(pending_count),
            "hit_rate": round(hits / samples * 100, 2) if samples else None,
            "avg_signed_return_10m": round(statistics.fmean(signed), 4) if signed else None,
            "median_abs_return_10m": round(statistics.median(abs(_float(row.get("return_10m"))) for row in rows), 4) if rows else None,
        }
        horizons = []
        for horizon in HORIZONS:
            values = []
            for row in rows:
                sign = 1 if row.get("sentiment_direction") == "positive" else -1
                values.append(_float(row.get(f"return_{horizon}m")) * sign)
            horizons.append(
                {
                    "minutes": horizon,
                    "avg_signed_return": round(statistics.fmean(values), 4) if values else None,
                    "hit_rate": round(sum(value >= 0.05 for value in values) / len(values) * 100, 2) if values else None,
                }
            )
        levels = []
        for level in ("A", "B", "C", "D"):
            subset = [row for row in rows if row.get("impact_level") == level]
            levels.append(
                {
                    "level": level,
                    "samples": len(subset),
                    "hit_rate": round(sum(int(row.get("direction_correct") or 0) for row in subset) / len(subset) * 100, 2) if subset else None,
                    "avg_signed_return_10m": round(statistics.fmean(_float(row.get("directional_return_10m")) for row in subset), 4) if subset else None,
                }
            )
        directions = []
        for direction, label in (("positive", "利好"), ("negative", "利空")):
            subset = [row for row in rows if row.get("sentiment_direction") == direction]
            directions.append(
                {
                    "direction": direction,
                    "label": label,
                    "samples": len(subset),
                    "hit_rate": round(sum(int(row.get("direction_correct") or 0) for row in subset) / len(subset) * 100, 2) if subset else None,
                    "avg_signed_return_10m": round(statistics.fmean(_float(row.get("directional_return_10m")) for row in subset), 4) if subset else None,
                }
            )
        recent = [
            {
                "published_at": row.get("published_at"),
                "title": row.get("title"),
                "code": row.get("code"),
                "name": row.get("name"),
                "direction": row.get("sentiment_direction"),
                "return_10m": row.get("return_10m"),
                "directional_return_10m": row.get("directional_return_10m"),
                "effectiveness": row.get("effectiveness"),
            }
            for row in rows[:20]
        ]
        return {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "days": days,
            "overview": overview,
            "horizons": horizons,
            "levels": levels,
            "directions": directions,
            "weights": calibrations[:30],
            "recent": recent,
            "methodology": {
                "entry": "消息后的首个可交易分钟开盘价",
                "horizons": list(HORIZONS),
                "hit_threshold_pct": 0.05,
                "minimum_calibration_samples": MIN_CALIBRATION_SAMPLES,
            },
        }


class NewsBacktestRunner:
    def __init__(self, store: NewsEventStore, bars_fetcher: Callable[[str], pd.DataFrame]):
        self.store = store
        self.bars_fetcher = bars_fetcher
        self._lock = threading.Lock()
        self._state = {
            "running": False,
            "days": 30,
            "total": 0,
            "processed": 0,
            "evaluated": 0,
            "pending": 0,
            "unavailable": 0,
            "failures": 0,
            "message": "尚未运行",
            "started_at": None,
            "finished_at": None,
        }

    def start(self, days: int = 30, limit: int = 500) -> dict:
        days = max(1, min(int(days), 120))
        limit = max(1, min(int(limit), 3000))
        with self._lock:
            if self._state["running"]:
                return {"started": False, **self._state}
            self._state = {
                "running": True,
                "days": days,
                "total": 0,
                "processed": 0,
                "evaluated": 0,
                "pending": 0,
                "unavailable": 0,
                "failures": 0,
                "message": "准备回测样本",
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "finished_at": None,
            }
        thread = threading.Thread(target=self._run, args=(days, limit), name="news-event-backtest", daemon=True)
        thread.start()
        return {"started": True, **self.status(include_report=False)}

    def _update(self, **values) -> None:
        with self._lock:
            self._state.update(values)

    def _run(self, days: int, limit: int) -> None:
        counts = {"evaluated": 0, "pending": 0, "unavailable": 0, "failures": 0}
        try:
            candidates = self.store.list_candidates(days=days, limit=limit)
            self._update(total=len(candidates), message=f"待验证 {len(candidates)} 个消息-股票样本")
            bars_by_code: dict[str, pd.DataFrame | Exception] = {}
            for index, event in enumerate(candidates, start=1):
                code = event["code"]
                if code not in bars_by_code:
                    try:
                        bars_by_code[code] = self.bars_fetcher(code)
                    except Exception as exc:  # data source failures should not stop the full batch
                        bars_by_code[code] = exc
                bars = bars_by_code[code]
                if isinstance(bars, Exception):
                    counts["failures"] += 1
                    self._update(
                        processed=index,
                        **counts,
                        message=f"{code} 分钟数据获取失败，继续下一项",
                    )
                    continue
                result = evaluate_news_event(event, bars)
                status = result.get("status", "unavailable")
                counts[status if status in counts else "unavailable"] += 1
                self.store.save_result(event, result)
                self._update(
                    processed=index,
                    **counts,
                    message=f"验证 {event.get('name') or code} · {index}/{len(candidates)}",
                )
            self._update(
                running=False,
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                message=f"完成：有效样本 {counts['evaluated']}，待数据 {counts['pending']}，失败 {counts['failures']}",
            )
            self.store.write_export(days=120)
        except Exception as exc:
            self._update(
                running=False,
                failures=counts["failures"] + 1,
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                message=f"回测中断：{exc}",
                error=str(exc),
            )

    def status(self, include_report: bool = True) -> dict:
        with self._lock:
            state = dict(self._state)
        if include_report:
            state["report"] = self.store.report(state.get("days") or 30)
        return state
