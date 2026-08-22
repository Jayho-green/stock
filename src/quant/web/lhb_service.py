"""龙虎榜面板服务:按日期取数、注入行业、组装 payload,并做两级缓存。

缓存策略:
- 历史日期(非今日)的榜单收盘后不变,落盘到 data/lhb_cache/{YYYYMMDD}.json 永久复用;
- 今日榜单约 17:30 后才完整,内存 TTL 缓存限频,不落盘;
- 个股行业基本不变,持久化在 data/lhb_cache/industry_map.json,只增量补缺。

行业补缺是逐只请求、可能很慢,因此**绝不阻塞接口**:请求立即返回榜单
(缺行业的先记"未分类"),后台线程补齐并落盘,payload 带 industry_pending
数量,前端据此自动刷新。
"""

from __future__ import annotations

import json
import inspect
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from ..lhb_ai import GlmLhbIndustryClassifier
from ..lhb import build_payload, group_by_industry
from .service import TTLCache

log = logging.getLogger("quant.lhb")

_DATE_RE = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})$")
MAX_FALLBACK_DAYS = 7  # 自动模式下向前回溯找最近一个有数据的交易日
MAX_SINGLE_INDUSTRY_FILL = 20  # 批量行业源失败时,逐只补缺最多处理的小缺口
UNKNOWN_INDUSTRIES = {"", "-", "None", "nan", "未分类"}


def parse_date_param(value: str | None) -> date | None:
    """解析 YYYY-MM-DD / YYYYMMDD;None 表示自动取最近榜单;非法抛 ValueError。"""
    if value is None or str(value).strip() == "":
        return None
    m = _DATE_RE.match(str(value).strip())
    if not m:
        raise ValueError("日期格式应为 YYYY-MM-DD")
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError as e:
        raise ValueError("无效日期") from e
    if d > date.today() or d < date(2010, 1, 1):
        raise ValueError("日期超出可查询范围")
    return d


def _valid_industry(value: str | None) -> str | None:
    """清洗行业名;空值和历史兜底值都视作未归类。"""
    if value is None:
        return None
    text = str(value).strip()
    return None if text in UNKNOWN_INDUSTRIES else text


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _looks_like_empty_lhb_error(exc: Exception) -> bool:
    """AkShare 在无龙虎榜数据时偶尔会把东财空响应抛成 NoneType 异常。"""
    text = str(exc)
    return (
        ("NoneType" in text and "subscriptable" in text)
        or "No data" in text
        or "无数据" in text
        or "没有数据" in text
    )


class LhbService:
    def __init__(
        self,
        source,
        cache_dir: Path | None = None,
        ttl: float = 600,
        now: Callable[[], float] = time.time,
        clock: Callable[[], datetime] = datetime.now,
        async_fill: bool = True,
        ai_classifier=None,
    ):
        self.source = source
        self.clock = clock
        self.async_fill = async_fill
        self.ai_classifier = ai_classifier
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem = TTLCache(ttl, now)
        self._industry: dict[str, str] | None = None
        self._fill_lock = threading.Lock()
        self._filling = False
        self._bulk_attempted = False
        self._targeted_industry_attempted: set[str] = set()
        self._ai_attempted: set[str] = set()

    # ---------- 行业映射 ----------

    def _industry_path(self) -> Path | None:
        return self.cache_dir / "industry_map.json" if self.cache_dir is not None else None

    def _load_industry(self) -> dict[str, str]:
        if self._industry is not None:
            return self._industry
        path = self._industry_path()
        data: dict[str, str] = {}
        if path is not None and path.exists():
            try:
                data = {str(k): str(v) for k, v in json.loads(path.read_text("utf-8")).items()}
            except Exception:
                data = {}
        self._industry = data
        return data

    def _save_industry(self, mapping: dict[str, str]) -> None:
        path = self._industry_path()
        if path is None:
            return
        try:
            path.write_text(json.dumps(mapping, ensure_ascii=False, indent=0), "utf-8")
        except Exception:
            pass

    def _start_fill(self, missing: list[str]) -> None:
        """启动行业补缺;async 模式起后台线程(已在跑则跳过),否则同步补齐。"""
        has_bulk = hasattr(self.source, "get_industry_map")
        has_single = hasattr(self.source, "get_stock_industry")
        if not missing or not (has_bulk or has_single):
            return
        if not self.async_fill:
            self._fill_worker(missing)
            return
        with self._fill_lock:
            if self._filling:
                return
            self._filling = True
        threading.Thread(target=self._fill_worker_guarded, args=(list(missing),), daemon=True).start()

    def _fill_worker_guarded(self, missing: list[str]) -> None:
        try:
            self._fill_worker(missing)
        finally:
            with self._fill_lock:
                self._filling = False

    def _fill_worker(self, missing: list[str]) -> None:
        mapping = self._load_industry()
        # 优先一次性批量拉全市场行业(几秒),失败或仍有缺口再逐只兜底
        bulk_ok = self._fill_bulk_once(mapping, missing, allow_targeted=True)
        still = [c for c in missing if not _valid_industry(mapping.get(c))]
        # 真实环境里 get_stock_industry 也是东财单股接口。批量源不可用且缺口很大时,
        # 逐只请求会把页面拖慢并放大限流,因此只处理小缺口。
        if still and hasattr(self.source, "get_stock_industry") and len(still) <= MAX_SINGLE_INDUSTRY_FILL:
            done = 0
            for code in still:
                if code in mapping:
                    continue
                try:
                    ind = self.source.get_stock_industry(code)
                except Exception:
                    ind = None
                ind = _valid_industry(ind)
                if ind:
                    mapping[code] = ind
                done += 1
                if done % 10 == 0:
                    self._save_industry(mapping)
                    log.info("行业逐只补缺进度 %d/%d", done, len(still))
            self._save_industry(mapping)
        still = [c for c in missing if not _valid_industry(mapping.get(c))]
        self._fill_ai_once(mapping, still)

    def _fill_ai_once(self, mapping: dict[str, str], missing: list[str]) -> bool:
        targets = [str(c).zfill(6) for c in missing if str(c).isdigit()]
        targets = [c for c in targets if c not in self._ai_attempted]
        if not targets:
            return False
        classifier = self.ai_classifier
        if classifier is None:
            classifier = GlmLhbIndustryClassifier.from_env()
            self.ai_classifier = classifier
        if not getattr(classifier, "enabled", False):
            self._ai_attempted.update(targets)
            return False
        stock_map = getattr(self, "_ai_stock_context", {})
        stocks = [stock_map.get(code, {"code": code}) for code in targets]
        try:
            classified = classifier.classify(stocks)
        except Exception as e:
            log.warning("GLM 龙虎榜行业归类失败:%s", e)
            classified = {}
        self._ai_attempted.update(targets)
        cleaned = {
            str(code).zfill(6): ind
            for code, raw in (classified or {}).items()
            if (ind := _valid_industry(raw))
        }
        if not cleaned:
            return False
        mapping.update(cleaned)
        self._save_industry(mapping)
        log.info("GLM 龙虎榜行业归类完成:%d 只", len(cleaned))
        return True

    def _call_industry_map(self, target_codes: list[str] | None = None) -> dict[str, str]:
        method = getattr(self.source, "get_industry_map")
        try:
            accepts_targets = bool(inspect.signature(method).parameters)
        except (TypeError, ValueError):
            accepts_targets = False
        if target_codes and accepts_targets:
            return method(target_codes)
        return method()

    def _fill_bulk_once(
        self,
        mapping: dict[str, str],
        target_codes: list[str] | None = None,
        allow_targeted: bool = False,
    ) -> bool:
        """同步尝试一次全市场行业映射;成功后写入持久缓存。"""
        if not hasattr(self.source, "get_industry_map"):
            return False
        cleaned: dict[str, str] = {}
        if allow_targeted and target_codes:
            pending_targets = [
                str(c).zfill(6)
                for c in target_codes
                if str(c).isdigit() and str(c).zfill(6) not in self._targeted_industry_attempted
            ]
            if pending_targets:
                try:
                    bulk = self._call_industry_map(pending_targets)
                except Exception:
                    bulk = {}
                self._targeted_industry_attempted.update(pending_targets)
                cleaned.update(
                    {
                        str(code).zfill(6): ind
                        for code, raw in (bulk or {}).items()
                        if (ind := _valid_industry(raw))
                    }
                )
        if not cleaned and not self._bulk_attempted:
            self._bulk_attempted = True
            try:
                bulk = self._call_industry_map()
            except Exception:
                bulk = {}
            cleaned.update(
                {
                    str(code).zfill(6): ind
                    for code, raw in (bulk or {}).items()
                    if (ind := _valid_industry(raw))
                }
            )
        if not cleaned:
            return False
        mapping.update(cleaned)
        self._save_industry(mapping)
        log.info("批量行业映射完成:%d 只", len(cleaned))
        return True

    def _apply_industries(self, payload: dict) -> dict:
        """用当前行业映射(可能比取数时更全)重注入行业并重算板块聚合。"""
        stocks = payload.get("stocks") or []
        mapping = self._load_industry()
        missing = [
            s["code"]
            for s in stocks
            if not _valid_industry(mapping.get(s["code"])) and not _valid_industry(s.get("industry"))
        ]
        if missing:
            self._fill_bulk_once(mapping, allow_targeted=False)
            missing = [
                s["code"]
                for s in stocks
                if not _valid_industry(mapping.get(s["code"])) and not _valid_industry(s.get("industry"))
            ]
            self._ai_stock_context = {str(s["code"]).zfill(6): dict(s) for s in stocks}
            self._start_fill(missing)  # 同步模式下这里会阻塞补齐,故重读
            mapping = self._load_industry()
        pending = 0
        for s in stocks:
            ind = _valid_industry(mapping.get(s["code"])) or _valid_industry(s.get("industry"))
            s["industry"] = ind or "未分类"
            if not ind:
                pending += 1
        payload["sectors"] = group_by_industry(stocks)
        payload["industry_pending"] = pending
        return payload

    def _apply_and_maybe_persist(self, d: date, payload: dict) -> dict:
        """重算行业/板块后,把已归档历史缓存同步修正。"""
        before_pending = payload.get("industry_pending")
        payload = self._apply_industries(payload)
        path = self._disk_path(d)
        has_disk = path is not None and path.exists()
        if has_disk and payload.get("summary", {}).get("stocks", 0) > 0:
            # 旧缓存可能没有 industry_pending,或行业映射后来才补齐。
            if before_pending != payload.get("industry_pending") or payload.get("industry_pending", 0) == 0:
                self._write_disk(d, payload)
        return payload

    # ---------- 榜单 ----------

    def _disk_path(self, d: date) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / f"{d.strftime('%Y%m%d')}.json"

    def _read_disk(self, d: date) -> dict | None:
        path = self._disk_path(d)
        if path is None or not path.exists():
            return None
        try:
            return json.loads(path.read_text("utf-8"))
        except Exception:
            return None

    def _write_disk(self, d: date, payload: dict) -> None:
        path = self._disk_path(d)
        if path is None:
            return
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
        except Exception:
            pass

    def _empty_payload(self, d: date) -> dict:
        return {
            "date": d.isoformat(),
            "industry_pending": 0,
            "summary": {
                "stocks": 0,
                "org_stocks": 0,
                "org_buy": 0.0,
                "org_sell": 0.0,
                "org_net": 0.0,
                "net_buy_count": 0,
                "net_sell_count": 0,
            },
            "sectors": [],
            "stocks": [],
        }

    def _cached_days(self, start: date | None = None, end: date | None = None) -> list[date]:
        if self.cache_dir is None or not self.cache_dir.exists():
            return []
        days: list[date] = []
        for path in self.cache_dir.glob("20??????.json"):
            try:
                d = datetime.strptime(path.stem, "%Y%m%d").date()
            except ValueError:
                continue
            if start is not None and d < start:
                continue
            if end is not None and d > end:
                continue
            days.append(d)
        return sorted(days)

    def _fetch_day(self, d: date) -> dict:
        """拉取并组装某一天的榜单(不做行业补缺,注入交给 _apply_industries)。"""
        if d.weekday() >= 5:
            return self._empty_payload(d)
        ymd = d.strftime("%Y%m%d")
        try:
            detail = self.source.get_lhb_detail(ymd, ymd)
        except Exception as e:
            if _looks_like_empty_lhb_error(e):
                return self._empty_payload(d)
            raise
        try:
            org = self.source.get_lhb_org(ymd, ymd)
        except Exception as e:
            if _looks_like_empty_lhb_error(e):
                org = None
            else:
                raise
        try:
            return build_payload(detail, org, self._load_industry(), d.isoformat())
        except Exception as e:
            if _looks_like_empty_lhb_error(e):
                return self._empty_payload(d)
            raise

    def _get_day(self, d: date) -> dict:
        """带缓存取某一天:先读磁盘(历史必存;当日仅归档任务会写),再内存 TTL。"""
        today = self.clock().date()
        cached = self._read_disk(d)
        if cached is not None:
            return cached
        key = ("lhb", d.isoformat())
        mem = self._mem.get(key)
        if mem is not None:
            return mem
        payload = self._fetch_day(d)
        self._mem.set(key, payload)
        if d != today and payload["summary"]["stocks"] > 0:
            self._write_disk(d, payload)
        return payload

    def archive_day(self, d: date | None = None) -> dict:
        """收盘披露后(约 17:30 起)调用:强制取数、补齐行业并落盘(含当日)。

        无数据(非交易日/未披露)不落盘;重复调用会覆盖,方便披露修正后重跑。
        """
        d = d or self.clock().date()
        payload = self._apply_industries(self._fetch_day(d))
        if payload["summary"]["stocks"] > 0:
            self._write_disk(d, payload)
        return payload

    def get_board(self, date_param: str | None = None) -> dict:
        """入口:指定日期返回当日榜单;不指定则回溯到最近一个有数据的日期。

        今日数据披露前(约 17:30)为空属正常,自动模式会回退到上一交易日。
        """
        d = parse_date_param(date_param)
        if d is not None:
            payload = self._apply_and_maybe_persist(d, self._get_day(d))
            payload["requested_date"] = d.isoformat()
            return payload
        probe = self.clock().date()
        last_err: Exception | None = None
        for _ in range(MAX_FALLBACK_DAYS):
            try:
                payload = self._get_day(probe)
            except Exception as e:  # 网络抖动:继续向前找,最后仍失败才抛
                last_err = e
                payload = None
            if payload is not None and payload["summary"]["stocks"] > 0:
                payload = self._apply_and_maybe_persist(probe, payload)
                payload["requested_date"] = None
                return payload
            probe -= timedelta(days=1)
        if last_err is not None:
            raise last_err
        payload = self._empty_payload(self.clock().date())
        payload["requested_date"] = None
        return payload

    def get_sector_trends(
        self,
        days: int = 30,
        end_date: str | None = None,
        period: str = "daily",
        top: int = 8,
    ) -> dict:
        """按已归档龙虎榜缓存聚合板块机构资金趋势。

        口径:只统计龙虎榜上出现机构专用席位的成交,未上榜个股不纳入。
        趋势接口默认只读归档文件,避免一次页面打开触发多日外部请求。
        """
        if period not in {"daily", "weekly"}:
            raise ValueError("period 仅支持 daily/weekly")
        days = max(5, min(int(days), 120))
        top = max(1, min(int(top), 20))
        end = parse_date_param(end_date) or self.clock().date()
        start = end - timedelta(days=days - 1)
        cached_days = self._cached_days(start, end)
        day_payloads: list[tuple[date, dict]] = []
        for d in cached_days:
            payload = self._read_disk(d)
            if not payload or payload.get("summary", {}).get("stocks", 0) <= 0:
                continue
            day_payloads.append((d, self._apply_and_maybe_persist(d, payload)))

        def empty_point(key: str, label: str, start_day: date, end_day: date | None = None) -> dict:
            return {
                "date": key,
                "label": label,
                "start": start_day.isoformat(),
                "end": (end_day or start_day).isoformat(),
                "org_buy": 0.0,
                "org_sell": 0.0,
                "org_net": 0.0,
                "org_count": 0,
                "stock_count": 0,
                "sectors": {},
            }

        buckets: dict[str, dict] = {}
        for d, payload in day_payloads:
            if period == "weekly":
                ws = _week_start(d)
                key = ws.isoformat()
                label = f"{ws.strftime('%m/%d')}周"
                point = buckets.setdefault(key, empty_point(key, label, ws, d))
                point["end"] = max(point["end"], d.isoformat())
            else:
                key = d.isoformat()
                point = buckets.setdefault(key, empty_point(key, d.strftime("%m/%d"), d))

            for sec in payload.get("sectors") or []:
                industry = _valid_industry(sec.get("industry")) or "未分类"
                target = point["sectors"].setdefault(
                    industry,
                    {
                        "industry": industry,
                        "org_buy": 0.0,
                        "org_sell": 0.0,
                        "org_net": 0.0,
                        "org_count": 0,
                        "stock_count": 0,
                    },
                )
                target["org_buy"] += float(sec.get("org_buy") or 0.0)
                target["org_sell"] += float(sec.get("org_sell") or 0.0)
                target["org_net"] += float(sec.get("org_net") or 0.0)
                target["org_count"] += int(sec.get("org_count") or 0)
                target["stock_count"] += int(sec.get("count") or 0)
                point["org_buy"] += float(sec.get("org_buy") or 0.0)
                point["org_sell"] += float(sec.get("org_sell") or 0.0)
                point["org_net"] += float(sec.get("org_net") or 0.0)
                point["org_count"] += int(sec.get("org_count") or 0)
                point["stock_count"] += int(sec.get("count") or 0)

        points = [buckets[k] for k in sorted(buckets)]
        score: dict[str, float] = {}
        for p in points:
            for industry, sec in p["sectors"].items():
                if industry == "未分类" and len(p["sectors"]) > 1:
                    continue
                score[industry] = score.get(industry, 0.0) + abs(sec["org_net"])
        industries = [k for k, _ in sorted(score.items(), key=lambda kv: kv[1], reverse=True)[:top]]
        return {
            "period": period,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": days,
            "top": top,
            "industries": industries,
            "points": points,
            "cached_days": len(day_payloads),
            "latest_date": day_payloads[-1][0].isoformat() if day_payloads else None,
            "source": "cache",
            "note": "仅统计已归档龙虎榜日期;机构金额为机构专用席位成交,不代表全市场机构资金。",
        }
