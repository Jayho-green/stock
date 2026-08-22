"""龙虎榜行业归类的 GLM 兜底分类器。"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any


GLM_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
GLM_MODEL = "glm-4.7"

# 申万 2021 版一级行业口径。龙虎榜做板块资金趋势时用一级行业比概念题材更稳定。
A_SHARE_SECTORS = (
    "农林牧渔",
    "基础化工",
    "钢铁",
    "有色金属",
    "电子",
    "家用电器",
    "食品饮料",
    "纺织服饰",
    "轻工制造",
    "医药生物",
    "公用事业",
    "交通运输",
    "房地产",
    "商贸零售",
    "社会服务",
    "综合",
    "建筑材料",
    "建筑装饰",
    "电力设备",
    "国防军工",
    "计算机",
    "传媒",
    "通信",
    "银行",
    "非银金融",
    "汽车",
    "机械设备",
    "煤炭",
    "石油石化",
    "环保",
    "美容护理",
)

SECTOR_ALIASES = {
    "化工": "基础化工",
    "商业贸易": "商贸零售",
    "商贸": "商贸零售",
    "零售": "商贸零售",
    "休闲服务": "社会服务",
    "社会服务业": "社会服务",
    "电气设备": "电力设备",
    "新能源": "电力设备",
    "半导体": "电子",
    "元件": "电子",
    "消费电子": "电子",
    "光学光电子": "电子",
    "自动化设备": "机械设备",
    "通用设备": "机械设备",
    "专用设备": "机械设备",
    "医疗器械": "医药生物",
    "医疗服务": "医药生物",
    "中药": "医药生物",
    "航空装备": "国防军工",
    "航天装备": "国防军工",
    "证券": "非银金融",
    "保险": "非银金融",
}

GLM_LHB_SYSTEM_PROMPT = """你是一名A股行业研究员，熟悉申万2021版一级行业分类和上市公司主营业务。

任务：把给定的龙虎榜股票归入唯一一个A股行业板块。

分类规则：
1. 只能从用户提供的 sector_list 中选择 sector，禁止自造板块名。
2. 优先依据上市公司主营业务、证券简称、常识性行业归属；上榜原因只作辅助，不要把“涨停、换手率”等交易原因当作行业。
3. 新股或名称不明确时，结合公司名称中的业务词推断；仍不确定时选择最接近的一级行业，并降低 confidence。
4. 金融类区分银行与非银金融；电池、光伏、风电、储能、电网设备归入电力设备；半导体、元件、消费电子归入电子；软件、IT服务、信创归入计算机。
5. 每只股票必须返回一条结果。

输出要求：
- 只能输出合法JSON，不要Markdown，不要解释过程。
- JSON格式为 {"items":[{"code":"000001","sector":"银行","confidence":0.88,"reason":"20字以内分类依据"}]}。
- code 必须来自输入 stocks；sector 必须来自 sector_list；confidence 为0到1的小数。"""


def _text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _clip(value: Any, length: int) -> str:
    text = _text(value)
    return text if len(text) <= length else text[:length].rstrip() + "..."


def normalize_sector(value: Any) -> str | None:
    text = _text(value).replace("Ⅱ", "").replace("II", "").replace("I", "").strip()
    if text in A_SHARE_SECTORS:
        return text
    return SECTOR_ALIASES.get(text)


def _extract_json(text: str) -> dict | None:
    text = _text(text)
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


class GlmLhbIndustryClassifier:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = GLM_MODEL,
        endpoint: str = GLM_ENDPOINT,
        timeout: float = 20.0,
        max_stocks: int = 120,
    ):
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
        self.timeout = timeout
        self.max_stocks = max_stocks

    @classmethod
    def from_env(cls) -> "GlmLhbIndustryClassifier":
        api_key = (
            os.environ.get("GLM_LHB_API_KEY")
            or os.environ.get("ZAI_API_KEY")
            or os.environ.get("BIGMODEL_API_KEY")
            or os.environ.get("GLM_API_KEY")
        )
        return cls(
            api_key=api_key,
            model=os.environ.get("GLM_LHB_MODEL", os.environ.get("GLM_NEWS_MODEL", GLM_MODEL)),
            endpoint=os.environ.get("GLM_LHB_ENDPOINT", os.environ.get("GLM_NEWS_ENDPOINT", GLM_ENDPOINT)),
            timeout=float(os.environ.get("GLM_LHB_TIMEOUT", "20")),
            max_stocks=max(1, min(int(os.environ.get("GLM_LHB_MAX_STOCKS", "120")), 200)),
        )

    @property
    def enabled(self) -> bool:
        if not self.api_key:
            return False
        return os.environ.get("GLM_LHB_ENABLED", "1").lower() not in {"0", "false", "no", "off"}

    def classify(self, stocks: list[dict]) -> dict[str, str]:
        if not self.enabled:
            return {}
        rows = []
        seen = set()
        for stock in stocks[: self.max_stocks]:
            code = _text(stock.get("code")).zfill(6)
            if not (code.isdigit() and len(code) == 6) or code in seen:
                continue
            seen.add(code)
            rows.append(
                {
                    "code": code,
                    "name": _clip(stock.get("name"), 24),
                    "reasons": [_clip(x, 36) for x in (stock.get("reasons") or [])[:3]],
                    "interpretation": _clip(stock.get("interpretation"), 40),
                }
            )
        if not rows:
            return {}
        batch_size = max(1, min(int(os.environ.get("GLM_LHB_BATCH_SIZE", "25")), 50))
        out: dict[str, str] = {}
        for start in range(0, len(rows), batch_size):
            out.update(self._classify_batch(rows[start : start + batch_size]))
        return out

    def _classify_batch(self, rows: list[dict]) -> dict[str, str]:
        payload = {
            "sector_list": list(A_SHARE_SECTORS),
            "stocks": rows,
        }
        messages = [
            {"role": "system", "content": GLM_LHB_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "请对以下今日龙虎榜股票做行业板块归类："
                + json.dumps(payload, ensure_ascii=False),
            },
        ]
        content = self._chat(messages)
        parsed = _extract_json(content)
        if not parsed:
            return {}

        allowed_codes = {row["code"] for row in rows}
        out: dict[str, str] = {}
        for item in parsed.get("items") or []:
            code = _text(item.get("code")).zfill(6)
            if code not in allowed_codes:
                continue
            sector = normalize_sector(item.get("sector"))
            if sector:
                out[code] = sector
        return out

    def _chat(self, messages: list[dict]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.05,
            "max_tokens": 4096,
            "thinking": {"type": "disabled"},
        }
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="ignore")[:240]
            raise RuntimeError(f"GLM LHB HTTP {e.code}: {detail}") from e
        data = json.loads(body)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("GLM LHB 返回为空")
        time.sleep(0.05)
        return _text((choices[0].get("message") or {}).get("content"))
