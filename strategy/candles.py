"""
Nhận diện nến xác nhận: engulfing và pin bar.
Mỗi hàm nhận nến dạng dict/Series có các key open, high, low, close.
"""

PINBAR_WICK_BODY_RATIO = 2.0  # wick chính phải dài >= 2× body


def is_bullish_engulfing(prev, cur) -> bool:
    """Nến trước đỏ, nến này xanh, body bao trùm body nến trước."""
    return (
        prev["close"] < prev["open"]
        and cur["close"] > cur["open"]
        and cur["open"] <= prev["close"]
        and cur["close"] >= prev["open"]
    )


def is_bearish_engulfing(prev, cur) -> bool:
    return (
        prev["close"] > prev["open"]
        and cur["close"] < cur["open"]
        and cur["open"] >= prev["close"]
        and cur["close"] <= prev["open"]
    )


def is_bullish_pinbar(cur) -> bool:
    """Wick dưới dài (từ chối giá xuống), wick trên ngắn, đóng cửa xanh."""
    body       = abs(cur["close"] - cur["open"])
    lower_wick = min(cur["open"], cur["close"]) - cur["low"]
    upper_wick = cur["high"] - max(cur["open"], cur["close"])
    return (
        body > 0
        and cur["close"] > cur["open"]
        and lower_wick >= PINBAR_WICK_BODY_RATIO * body
        and upper_wick <= body
    )


def is_bearish_pinbar(cur) -> bool:
    body       = abs(cur["close"] - cur["open"])
    lower_wick = min(cur["open"], cur["close"]) - cur["low"]
    upper_wick = cur["high"] - max(cur["open"], cur["close"])
    return (
        body > 0
        and cur["close"] < cur["open"]
        and upper_wick >= PINBAR_WICK_BODY_RATIO * body
        and lower_wick <= body
    )


def bullish_confirmation(prev, cur) -> "str | None":
    """Trả về loại nến xác nhận bullish ('engulfing'/'pinbar') hoặc None."""
    if is_bullish_engulfing(prev, cur):
        return "engulfing"
    if is_bullish_pinbar(cur):
        return "pinbar"
    return None


def bearish_confirmation(prev, cur) -> "str | None":
    if is_bearish_engulfing(prev, cur):
        return "engulfing"
    if is_bearish_pinbar(cur):
        return "pinbar"
    return None
