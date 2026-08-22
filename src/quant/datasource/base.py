"""数据源抽象接口。换源(tushare/券商)只需实现这套接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class DataSource(ABC):
    @abstractmethod
    def get_minute_bars(self, code: str, period: str = "1") -> pd.DataFrame:
        """分钟K线,标准 Bars schema。"""

    @abstractmethod
    def get_daily_bars(self, code: str, start: str, end: str) -> pd.DataFrame:
        """日线,标准 Bars schema。"""

    @abstractmethod
    def get_realtime(self, codes: list[str]) -> pd.DataFrame:
        """实时快照,列含 code/name/price。"""

    @abstractmethod
    def get_index_constituents(self, symbol: str) -> pd.DataFrame:
        """指数成分股,列含 code/name。"""

    @abstractmethod
    def get_trade_dates(self) -> set[date]:
        """交易日历集合。"""
