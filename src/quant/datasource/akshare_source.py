"""akshare 数据源实现。

网络方法是薄封装;核心的列名归一化 ``_normalize_bars`` 为纯函数,可离线单测。
akshare 接口返回中文列名,这里统一映射为标准英文 schema。
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.parse
from collections.abc import Callable
from datetime import date, datetime
from io import StringIO
from typing import TypeVar

import pandas as pd

from .base import DataSource

# 东方财富等行情接口会拒绝默认的 python-requests UA、并间歇性限流断连。
# 全局给 requests 设浏览器 UA(akshare 默认不带),并对网络调用做重试退避。
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DEFAULT_REQUEST_TIMEOUT = 8.0
DEFAULT_RETRY_TRIES = 3
DEFAULT_RETRY_BASE = 0.5
_AKSHARE_LOCK = threading.RLock()


def _install_requests_defaults() -> None:
    import requests.sessions as _sessions
    import requests.utils as _u

    _u.default_user_agent = lambda *a, **k: _BROWSER_UA

    orig = _sessions.Session.request
    if getattr(orig, "_quant_defaults_installed", False):
        return

    def request_with_defaults(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = DEFAULT_REQUEST_TIMEOUT
        return orig(self, method, url, **kwargs)

    request_with_defaults._quant_defaults_installed = True
    _sessions.Session.request = request_with_defaults


_install_requests_defaults()

_T = TypeVar("_T")


def _with_retry(
    fn: Callable[[], _T],
    tries: int = DEFAULT_RETRY_TRIES,
    base: float = DEFAULT_RETRY_BASE,
) -> _T:
    """对间歇性断连/限流做指数退避重试。"""
    last: Exception | None = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # akshare 抛 requests.ConnectionError 等
            last = e
            time.sleep(base * (2**i))
    assert last is not None
    raise last


def _get_json_with_curl_fallback(
    url: str,
    params: dict,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
    tries: int = DEFAULT_RETRY_TRIES,
    base: float = DEFAULT_RETRY_BASE,
) -> dict:
    """先用 requests,失败后用 curl 兜底;仅给少数海外东财接口使用。"""
    import requests

    try:
        resp = _with_retry(
            lambda: requests.get(
                url,
                params=params,
                timeout=timeout,
                headers={"User-Agent": _BROWSER_UA},
            ),
            tries=tries,
            base=base,
        )
        return resp.json() or {}
    except Exception as first_error:
        query = urllib.parse.urlencode(params)
        full_url = f"{url}?{query}" if query else url
        proc = subprocess.run(
            [
                "curl",
                "-fsSL",
                "--max-time",
                str(max(1, int(timeout) + 2)),
                "-H",
                f"User-Agent: {_BROWSER_UA}",
                full_url,
            ],
            capture_output=True,
            text=True,
            timeout=max(1, timeout + 3),
        )
        if proc.returncode != 0:
            raise first_error
        return json.loads(proc.stdout or "{}")

_COL_MAP = {
    "时间": "datetime",
    "日期": "datetime",
    "date": "datetime",
    "day": "datetime",
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume",
}

_BARS_COLS = ["datetime", "open", "high", "low", "close", "volume"]


def _market_symbol(code: str) -> str:
    """把 6 位 A 股代码转为新浪/腾讯常用的 sh/sz 前缀代码。"""
    prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
    return f"{prefix}{code}"


def _normalize_bars(raw: pd.DataFrame) -> pd.DataFrame:
    """把 akshare 的中文列原始行情归一化为标准 Bars schema(按时间升序)。"""
    df = raw.rename(columns=_COL_MAP)
    missing = [c for c in _BARS_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"行情缺少列: {missing}; 实际列: {list(df.columns)}")
    df = df[_BARS_COLS].copy()
    df["datetime"] = pd.to_datetime(df["datetime"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c])
    # 丢弃价格非正的占位/异常行(会把 K 线 y 轴拉到 0,破坏显示)
    valid = (df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)
    df = df[valid]
    return df.sort_values("datetime").reset_index(drop=True)


def _normalize_tx_daily(raw: pd.DataFrame) -> pd.DataFrame:
    """腾讯日线归一化。旧版接口 amount 列才是成交量;新版已带 volume 列,rename 会产生重复列名。"""
    if "volume" in raw.columns:
        df = raw.drop(columns=["amount"], errors="ignore")
    else:
        df = raw.rename(columns={"amount": "volume"})
    return _normalize_bars(df)


class AkshareSource(DataSource):
    def __init__(
        self,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        tries: int = DEFAULT_RETRY_TRIES,
        retry_base: float = DEFAULT_RETRY_BASE,
    ):
        self.timeout = timeout
        self.tries = tries
        self.retry_base = retry_base

    def get_minute_bars(self, code: str, period: str = "1") -> pd.DataFrame:
        with _AKSHARE_LOCK:
            import akshare as ak

            try:
                raw = _with_retry(
                    lambda: ak.stock_zh_a_minute(
                        symbol=_market_symbol(code),
                        period=period,
                        adjust="",
                    ),
                    tries=self.tries,
                    base=self.retry_base,
                )
            except Exception:
                raw = _with_retry(
                    lambda: ak.stock_zh_a_hist_min_em(symbol=code, period=period, adjust=""),
                    tries=max(1, self.tries),
                    base=self.retry_base,
                )
        return _normalize_bars(raw)

    def get_daily_bars(self, code: str, start: str, end: str) -> pd.DataFrame:
        with _AKSHARE_LOCK:
            import akshare as ak

            try:
                raw = _with_retry(
                    lambda: ak.stock_zh_a_hist(
                        symbol=code,
                        period="daily",
                        start_date=start,
                        end_date=end,
                        adjust="qfq",
                        timeout=self.timeout,
                    ),
                    tries=self.tries,
                    base=self.retry_base,
                )
            except Exception:
                raw = _with_retry(
                    lambda: ak.stock_zh_a_hist_tx(
                        symbol=_market_symbol(code),
                        start_date=start,
                        end_date=end,
                        adjust="qfq",
                        timeout=self.timeout,
                    ),
                    tries=max(1, self.tries),
                    base=self.retry_base,
                )
                return _normalize_tx_daily(raw)
        return _normalize_bars(raw)

    def get_market_activity(self) -> dict:
        """乐咕赚钱效应:全市场涨跌家数分布,用于计算波段战法市场温度。"""
        with _AKSHARE_LOCK:
            import akshare as ak

            raw = _with_retry(
                ak.stock_market_activity_legu,
                tries=self.tries,
                base=self.retry_base,
            )
        m = {str(k): v for k, v in zip(raw["item"], raw["value"])}

        def _num(key: str) -> int:
            try:
                return int(float(m.get(key) or 0))
            except (TypeError, ValueError):
                return 0

        return {
            "up": _num("上涨"),
            "down": _num("下跌"),
            "flat": _num("平盘"),
            "limit_up": _num("涨停"),
            "limit_down": _num("跌停"),
            "stat_time": str(m.get("统计日期") or ""),
        }

    def get_index_daily(self, symbol: str = "sh000001", start: str = "", end: str = "") -> pd.DataFrame:
        """指数日线(腾讯接口,sh000001=上证)。"""
        with _AKSHARE_LOCK:
            import akshare as ak

            raw = _with_retry(
                lambda: ak.stock_zh_a_hist_tx(
                    symbol=symbol,
                    start_date=start,
                    end_date=end,
                    adjust="",
                    timeout=self.timeout,
                ),
                tries=self.tries,
                base=self.retry_base,
            )
        # _normalize_tx_daily 已兼容新版自带 volume 列的结构
        return _normalize_tx_daily(raw)

    def get_realtime_board(self, codes: list[str] | None = None) -> pd.DataFrame:
        """全A实时快照(含涨跌幅),按 codes 过滤;codes 为空返回全部。"""
        with _AKSHARE_LOCK:
            import akshare as ak

            try:
                raw = _with_retry(ak.stock_zh_a_spot_em, tries=self.tries, base=self.retry_base)
            except Exception:
                raw = _with_retry(ak.stock_zh_a_spot, tries=1, base=self.retry_base)
        df = raw.rename(columns={"代码": "code", "名称": "name", "最新价": "price", "涨跌幅": "pct"})
        df = df[["code", "name", "price", "pct"]].copy()
        df["code"] = df["code"].astype(str).str.replace(r"^(sh|sz|bj)", "", regex=True).str.zfill(6)
        if codes is not None:
            df = df[df["code"].isin([str(c).zfill(6) for c in codes])]
        return df.reset_index(drop=True)

    def get_industry_board_cons(self, board: str = "电力行业") -> pd.DataFrame:
        """东财行业板块成分股,含成交额/换手率,用于板块活跃股筛选。"""
        with _AKSHARE_LOCK:
            import akshare as ak

            raw = _with_retry(
                lambda: ak.stock_board_industry_cons_em(symbol=board, timeout=self.timeout),
                tries=self.tries,
                base=self.retry_base,
            )
        df = raw.rename(
            columns={"代码": "code", "名称": "name", "最新价": "price", "涨跌幅": "pct", "成交额": "amount", "换手率": "turnover"}
        )
        cols = ["code", "name", "price", "pct", "amount", "turnover"]
        for c in ("price", "pct", "amount", "turnover"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df[[c for c in cols if c in df.columns]].copy()

    def get_realtime(self, codes: list[str]) -> pd.DataFrame:
        with _AKSHARE_LOCK:
            import akshare as ak

            try:
                raw = _with_retry(ak.stock_zh_a_spot_em, tries=self.tries, base=self.retry_base)
            except Exception:
                raw = _with_retry(ak.stock_zh_a_spot, tries=1, base=self.retry_base)
        df = raw.rename(columns={"代码": "code", "名称": "name", "最新价": "price"})
        df["code"] = df["code"].astype(str).str.replace(r"^(sh|sz|bj)", "", regex=True)
        df = df[df["code"].isin(codes)][["code", "name", "price"]].copy()
        return df.reset_index(drop=True)

    def get_global_quotes(self, targets: list[dict]) -> list[dict]:
        """东财海外实时快照;targets 需包含 secid,如 177.005930。"""
        clean_targets = [
            t
            for t in targets
            if isinstance(t, dict) and str(t.get("secid") or "").strip()
        ]
        if not clean_targets:
            return []
        target_by_secid = {str(t["secid"]): t for t in clean_targets}
        params = {
            "secids": ",".join(target_by_secid),
            "fields": "f12,f13,f14,f2,f3,f4,f18,f44,f45,f46,f47,f60,f124,f152,f292",
            "fltt": "2",
            "invt": "2",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        payload = _get_json_with_curl_fallback(
            url,
            params,
            timeout=self.timeout,
            tries=self.tries,
            base=self.retry_base,
        )
        rows = ((payload.get("data") or {}).get("diff") or [])
        if isinstance(rows, dict):
            rows = list(rows.values())
        if not rows:
            rows = [row for t in clean_targets if (row := self._get_global_quote_one(str(t["secid"])))]

        def num(value):
            if value in (None, "", "-"):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def pct(value):
            parsed = num(value)
            if parsed is None:
                return None
            return parsed / 100 if abs(parsed) > 100 else parsed

        out: list[dict] = []
        for row in rows:
            code = str(row.get("f12") or "").zfill(6)
            market = str(row.get("f13") or "")
            secid = f"{market}.{code}"
            target = target_by_secid.get(secid, {})
            source_ts = row.get("f124")
            try:
                source_time = datetime.fromtimestamp(int(source_ts)).strftime("%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError, OSError):
                source_time = None
            out.append(
                {
                    "id": target.get("id") or secid,
                    "secid": secid,
                    "code": code,
                    "name": row.get("f14") or target.get("name") or code,
                    "market": target.get("market") or "KRX",
                    "currency": target.get("currency") or "KRW",
                    "price": num(row.get("f2")),
                    "change_pct": pct(row.get("f3")),
                    "change": num(row.get("f4")),
                    "prev_close": num(row.get("f18")) or num(row.get("f60")),
                    "day_high": num(row.get("f44")),
                    "day_low": num(row.get("f45")),
                    "open": num(row.get("f46")),
                    "volume": num(row.get("f47")),
                    "status_code": row.get("f292"),
                    "source_time": source_time,
                }
            )
        return out

    def _get_global_quote_one(self, secid: str) -> dict | None:
        """海外单股快照备用接口;东财批量接口偶发空响应时兜底。"""
        params = {
            "secid": secid,
            "fields": "f57,f58,f43,f44,f45,f46,f47,f60,f169,f170,f86,f107,f152,f292",
        }
        try:
            payload = _get_json_with_curl_fallback(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params,
                timeout=self.timeout,
                tries=max(1, self.tries),
                base=self.retry_base,
            )
            data = (payload.get("data") or {})
        except Exception:
            return None
        if not data:
            return None
        return {
            "f12": data.get("f57"),
            "f13": data.get("f107"),
            "f14": data.get("f58"),
            "f2": data.get("f43"),
            "f3": data.get("f170"),
            "f4": data.get("f169"),
            "f18": data.get("f60"),
            "f44": data.get("f44"),
            "f45": data.get("f45"),
            "f46": data.get("f46"),
            "f47": data.get("f47"),
            "f60": data.get("f60"),
            "f124": data.get("f86"),
            "f152": data.get("f152"),
            "f292": data.get("f292"),
        }

    def get_global_intraday(self, targets: list[dict]) -> dict[str, dict]:
        """海外日内分时;返回每个 target id 的价格与相对昨收涨跌幅序列。"""
        out: dict[str, dict] = {}

        def num(value):
            if value in (None, "", "-"):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        for target in targets:
            secid = str(target.get("secid") or "").strip()
            if not secid:
                continue
            params = {
                "secid": secid,
                "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                "iscr": "0",
                "iscca": "0",
                "ndays": "1",
            }
            try:
                payload = _get_json_with_curl_fallback(
                    "https://push2his.eastmoney.com/api/qt/stock/trends2/get",
                    params,
                    timeout=self.timeout,
                    tries=max(1, self.tries),
                    base=self.retry_base,
                )
                data = (payload.get("data") or {})
            except Exception:
                continue
            prev_close = num(data.get("preClose")) or num(data.get("prePrice"))
            points = []
            volumes = []
            prices = []
            for raw in data.get("trends") or []:
                parts = str(raw).split(",")
                if len(parts) < 3:
                    continue
                price = num(parts[2]) or num(parts[1])
                if price is None:
                    continue
                prices.append(price)
                if len(parts) > 5 and (volume := num(parts[5])) is not None:
                    volumes.append(volume)
                points.append(
                    {
                        "time": parts[0],
                        "price": price,
                        "pct": round((price - prev_close) / prev_close * 100, 4) if prev_close else None,
                    }
                )
            out[str(target.get("id") or secid)] = {
                "id": target.get("id") or secid,
                "secid": secid,
                "name": data.get("name") or target.get("name"),
                "code": data.get("code") or str(secid).split(".")[-1],
                "prev_close": prev_close,
                "price": points[-1]["price"] if points else None,
                "open": points[0]["price"] if points else None,
                "day_high": max(prices) if prices else None,
                "day_low": min(prices) if prices else None,
                "volume": sum(volumes) if volumes else None,
                "points": points,
                "source_time": data.get("time"),
            }
        return out

    def get_index_constituents(self, symbol: str) -> pd.DataFrame:
        with _AKSHARE_LOCK:
            import akshare as ak

            raw = _with_retry(
                lambda: ak.index_stock_cons_csindex(symbol=symbol),
                tries=self.tries,
                base=self.retry_base,
            )
        df = raw.rename(columns={"成分券代码": "code", "成分券名称": "name"})
        df["code"] = df["code"].astype(str).str.zfill(6)
        return df[["code", "name"]].copy()

    def get_trade_dates(self) -> set[date]:
        with _AKSHARE_LOCK:
            import akshare as ak

            raw = _with_retry(ak.tool_trade_date_hist_sina, tries=self.tries, base=self.retry_base)
        return {pd.to_datetime(d).date() for d in raw["trade_date"]}

    def get_lhb_detail(self, start: str, end: str) -> pd.DataFrame:
        """龙虎榜详情(东财数据中心),start/end 为 YYYYMMDD;返回原始中文列。"""
        with _AKSHARE_LOCK:
            import akshare as ak

            return _with_retry(
                lambda: ak.stock_lhb_detail_em(start_date=start, end_date=end),
                tries=self.tries,
                base=self.retry_base,
            )

    def get_lhb_org(self, start: str, end: str) -> pd.DataFrame:
        """龙虎榜机构买卖每日统计(仅含机构专用席位参与的上榜股);原始中文列。"""
        with _AKSHARE_LOCK:
            import akshare as ak

            return _with_retry(
                lambda: ak.stock_lhb_jgmmtj_em(start_date=start, end_date=end),
                tries=self.tries,
                base=self.retry_base,
            )

    def get_industry_map(self, target_codes: list[str] | None = None) -> dict[str, str]:
        """全 A 股 代码→东财行业,一次性批量拉取(东财行情列表接口,f100=行业)。

        东财接口会把 pz 实际限制在 100 左右,必须按 total 翻页;同时北交所
        条件不能和沪深条件混在一个 fs 里,否则容易只拿到前几页北交所股票。
        失败页跳出,返回已取到的部分。传入 target_codes 时,东财失败或覆盖不全则
        用同花顺行业成分股页面定向补齐这些代码,避免全市场暴力扫描。
        """
        out = self._get_eastmoney_industry_map()
        targets = {
            str(c).zfill(6)
            for c in (target_codes or [])
            if str(c).strip().isdigit() and len(str(c).strip()) <= 6
        }
        if targets and not targets.issubset(out):
            missing = sorted(targets - set(out))
            out.update(self._get_ths_target_industry_map(missing))
        return out

    def _get_eastmoney_industry_map(self) -> dict[str, str]:
        """东财行情列表行业映射;网络异常时返回已取到的部分。"""
        import requests

        url = "https://push2.eastmoney.com/api/qt/clist/get"
        out: dict[str, str] = {}
        fs_groups = (
            "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23",  # 沪深主板/创业板/科创板
            "m:0 t:81 s:2048",  # 北交所
        )
        page_size = 100
        for fs in fs_groups:
            page = 1
            while page <= 80:
                params = {
                    "pn": page,
                    "pz": page_size,
                    "po": 1,
                    "np": 1,
                    "fltt": 2,
                    "invt": 2,
                    "fid": "f12",
                    "fs": fs,
                    "fields": "f12,f100",
                }
                try:
                    resp = _with_retry(
                        lambda: requests.get(
                            url,
                            params=params,
                            timeout=self.timeout,
                            headers={"User-Agent": _BROWSER_UA},
                        ),
                        tries=self.tries,
                        base=self.retry_base,
                    )
                    data = (resp.json() or {}).get("data") or {}
                except Exception:
                    break
                diff = data.get("diff") or []
                if isinstance(diff, dict):  # 该接口两种返回形态都出现过
                    diff = list(diff.values())
                if not diff:
                    break
                for row in diff:
                    code = str(row.get("f12", "")).zfill(6)
                    ind = row.get("f100")
                    if code.isdigit() and ind and str(ind) not in ("-", "None", "nan"):
                        out[code] = str(ind)
                try:
                    total = int(data.get("total") or 0)
                except (TypeError, ValueError):
                    total = 0
                if total and page * page_size >= total:
                    break
                if not total and len(diff) < page_size:
                    break
                page += 1
        return out

    def _get_ths_target_industry_map(self, target_codes: list[str]) -> dict[str, str]:
        """同花顺行业成分股反查 代码→行业,只补传入的目标代码。

        这是东财行业接口断开时的备用源。接口需要同花顺 v Cookie,这里复用
        AkShare 内部工具生成;成分页每页约 20 只,找到全部目标后立即停止。
        """
        targets = {
            str(c).zfill(6)
            for c in target_codes
            if str(c).strip().isdigit() and len(str(c).strip()) <= 6
        }
        if not targets:
            return {}
        try:
            import py_mini_racer
            import requests
            from akshare.stock_feature.stock_board_industry_ths import (
                _get_file_content_ths,
                _get_stock_board_industry_name_ths,
            )
        except Exception:
            return {}

        try:
            js_code = py_mini_racer.MiniRacer()
            js_code.eval(_get_file_content_ths("ths.js"))
            v_code = js_code.call("v")
            industry_codes = _get_stock_board_industry_name_ths()
        except Exception:
            return {}

        headers = {
            "User-Agent": _BROWSER_UA,
            "Referer": "http://q.10jqka.com.cn/thshy/",
            "Cookie": f"v={v_code}",
        }
        out: dict[str, str] = {}

        def parse_codes(html: str, industry: str) -> None:
            try:
                tables = pd.read_html(StringIO(html))
            except Exception:
                return
            if not tables:
                return
            df = tables[0]
            if "代码" not in df.columns:
                return
            for raw in df["代码"]:
                code = str(raw).strip().split(".")[0].zfill(6)
                if code in targets and code not in out:
                    out[code] = industry

        def page_count(html: str) -> int:
            import re

            m = re.search(r'class=["\']page_info["\'][^>]*>\s*\d+\s*/\s*(\d+)', html)
            if not m:
                return 1
            try:
                return max(1, min(int(m.group(1)), 80))
            except ValueError:
                return 1

        for industry, board_code in industry_codes.items():
            if targets.issubset(out):
                break
            base = f"http://q.10jqka.com.cn/thshy/detail/code/{board_code}/"
            try:
                resp = requests.get(base, headers=headers, timeout=self.timeout)
                resp.encoding = "gbk"
            except Exception:
                continue
            parse_codes(resp.text, industry)
            pages = page_count(resp.text)
            for page in range(2, pages + 1):
                if targets.issubset(out):
                    break
                url = f"{base}field/199112/order/desc/page/{page}/ajax/1/"
                try:
                    resp = requests.get(url, headers={**headers, "Referer": base}, timeout=self.timeout)
                    resp.encoding = "gbk"
                except Exception:
                    break
                parse_codes(resp.text, industry)
                time.sleep(0.02)
        return out

    def get_stock_industry(self, code: str) -> str | None:
        """个股所属东财行业;查询失败返回 None(调用方兜底为"未分类")。"""
        with _AKSHARE_LOCK:
            import akshare as ak

            try:
                # 行业补缺量大且在后台跑,只试一次,失败下次请求再补
                raw = ak.stock_individual_info_em(symbol=str(code).zfill(6))
            except Exception:
                return None
        try:
            match = raw[raw["item"] == "行业"]
            if len(match):
                val = str(match.iloc[0]["value"]).strip()
                return val or None
        except Exception:
            return None
        return None

    def get_all_code_name(self) -> pd.DataFrame:
        """全 A 股代码+名称(轻量,一次请求)。列:code, name。"""
        with _AKSHARE_LOCK:
            import akshare as ak

            raw = _with_retry(ak.stock_info_a_code_name, tries=self.tries, base=self.retry_base)
        df = raw.rename(columns={"代码": "code", "名称": "name"})
        df["code"] = df["code"].astype(str)
        return df[["code", "name"]].copy()
