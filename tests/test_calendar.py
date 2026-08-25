from datetime import date, datetime

from quant.calendar import is_in_session, is_trading_day


def test_in_session_morning():
    assert is_in_session(datetime(2024, 1, 2, 10, 0)) is True


def test_in_session_afternoon():
    assert is_in_session(datetime(2024, 1, 2, 14, 0)) is True


def test_lunch_break_not_in_session():
    assert is_in_session(datetime(2024, 1, 2, 12, 0)) is False


def test_before_open_not_in_session():
    assert is_in_session(datetime(2024, 1, 2, 9, 0)) is False


def test_after_close_not_in_session():
    assert is_in_session(datetime(2024, 1, 2, 15, 30)) is False


def test_is_trading_day():
    trade_dates = {date(2024, 1, 2), date(2024, 1, 3)}
    assert is_trading_day(date(2024, 1, 2), trade_dates) is True
    assert is_trading_day(date(2024, 1, 1), trade_dates) is False  # 元旦
