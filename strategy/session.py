"""Lọc phiên London/NY: 07:00–15:30 UTC, thứ 2–6. Tắt bằng SESSION_FILTER=0."""

from datetime import datetime, timezone

import config


def is_trading_session(dt: datetime = None) -> bool:
    """dt: thời điểm UTC cần kiểm tra (mặc định = bây giờ). Backtest truyền bar time đã quy về UTC."""
    if not config.SESSION_FILTER:
        return True  # trade 24/7
    dt = dt or datetime.now(timezone.utc)
    if dt.weekday() >= 5:  # thứ 7, CN
        return False
    minutes = dt.hour * 60 + dt.minute
    return config.SESSION_START_MIN <= minutes < config.SESSION_END_MIN


def get_current_session(dt: datetime = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    hour = dt.hour
    if 7 <= hour < 12:
        return "London"
    if 12 <= hour < 16:
        return "London/NY Overlap"
    if 16 <= hour < 21:
        return "New York"
    return "Off-session"
