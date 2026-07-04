"""
6 setup nến tại Supply/Demand zone (bộ infographic TradeCoinUnderground).

Quy ước (mô tả chiều SELL tại supply — BUY đối xứng tại demand):
- Nến tín hiệu phải CHẠM zone (high >= zone bottom với sell)
- Trigger: nến cuối window là nến ĐẦU TIÊN đóng cửa dưới trigger level,
  pattern phải nằm trong CONFIRM_WINDOW_BARS nến gần nhất
- Nếu có nến đóng vượt SL anchor trước khi trigger → pattern vô hiệu
- Riêng engulfing: bản thân nến engulfing là trigger (vào ngay tại close)

Mỗi detector nhận (window, zone, direction) — window: nến đã đóng, dòng cuối
là nến trigger tiềm năng — trả {"trigger_level","sl_anchor"} hoặc None.
"""

import config
from strategy.candles import (
    is_bearish_engulfing,
    is_bearish_pinbar,
    is_bullish_engulfing,
    is_bullish_pinbar,
)


# ── Helpers hình dạng nến ─────────────────────────────────────────────────────

def _body(c) -> float:
    return abs(float(c["close"]) - float(c["open"]))


def _rng(c) -> float:
    return float(c["high"]) - float(c["low"])


def _upper_wick(c) -> float:
    return float(c["high"]) - max(float(c["open"]), float(c["close"]))


def _lower_wick(c) -> float:
    return min(float(c["open"]), float(c["close"])) - float(c["low"])


def is_doji(c) -> bool:
    rng = _rng(c)
    return rng > 0 and _body(c) <= config.DOJI_BODY_RATIO * rng


def has_long_upper_wick(c) -> bool:
    """Râu trên chiếm >= 50% range — một lần thử phá lên thất bại."""
    rng = _rng(c)
    return rng > 0 and _upper_wick(c) >= 0.5 * rng


def has_long_lower_wick(c) -> bool:
    rng = _rng(c)
    return rng > 0 and _lower_wick(c) >= 0.5 * rng


def is_inverted_hammer(c) -> bool:
    """Búa ngược: râu trên dài >= 2×body, thân nhỏ đóng ở phần thấp."""
    body = _body(c)
    return (
        body > 0
        and has_long_upper_wick(c)
        and _upper_wick(c) >= 2 * body
        and _lower_wick(c) <= body
    )


def is_hammer(c) -> bool:
    """Búa thường (cho demand): râu dưới dài, thân nhỏ đóng ở phần cao."""
    body = _body(c)
    return (
        body > 0
        and has_long_lower_wick(c)
        and _lower_wick(c) >= 2 * body
        and _upper_wick(c) <= body
    )


def _touches_supply(c, zone) -> bool:
    return float(c["high"]) >= zone["bottom"]


def _touches_demand(c, zone) -> bool:
    return float(c["low"]) <= zone["top"]


# ── Generic trigger scan ──────────────────────────────────────────────────────

def _sell_trigger(window, pattern_at):
    """
    pattern_at(p) -> (trigger_level, sl_anchor) | None, p = index NẾN CUỐI của pattern.
    Trả kết quả nếu nến cuối window là nến đầu tiên đóng dưới trigger level.
    """
    n = len(window)
    cur_close = float(window["close"].iloc[-1])
    for back in range(1, config.CONFIRM_WINDOW_BARS + 1):
        p = n - 1 - back
        if p < 1:
            break
        res = pattern_at(p)
        if not res:
            continue
        trigger, anchor = res
        between = window.iloc[p + 1 : n - 1]
        if len(between) and (
            (between["close"] < trigger).any()      # đã trigger ở nến trước rồi
            or (between["close"] > anchor).any()    # pattern bị vô hiệu (đóng vượt anchor)
        ):
            continue
        if cur_close < trigger:
            return {"trigger_level": trigger, "sl_anchor": anchor}
    return None


def _buy_trigger(window, pattern_at):
    n = len(window)
    cur_close = float(window["close"].iloc[-1])
    for back in range(1, config.CONFIRM_WINDOW_BARS + 1):
        p = n - 1 - back
        if p < 1:
            break
        res = pattern_at(p)
        if not res:
            continue
        trigger, anchor = res
        between = window.iloc[p + 1 : n - 1]
        if len(between) and (
            (between["close"] > trigger).any()
            or (between["close"] < anchor).any()
        ):
            continue
        if cur_close > trigger:
            return {"trigger_level": trigger, "sl_anchor": anchor}
    return None


# ── 6 setup detectors ─────────────────────────────────────────────────────────

def detect_engulfing(window, zone, direction):
    """Cụm >= MIN_BASE_BARS nến giằng co trong zone → nến engulfing = trigger luôn."""
    cur, prev = window.iloc[-1], window.iloc[-2]
    n = len(window)

    if direction == "SELL":
        if not _touches_supply(cur, zone) or not is_bearish_engulfing(prev, cur):
            return None
        cluster, j = 0, n - 2
        while j >= 0 and _touches_supply(window.iloc[j], zone):
            cluster += 1
            j -= 1
        if cluster < config.MIN_BASE_BARS:
            return None
        anchor = float(window["high"].iloc[n - 1 - cluster :].max())
        return {"trigger_level": float(cur["close"]), "sl_anchor": anchor}

    if not _touches_demand(cur, zone) or not is_bullish_engulfing(prev, cur):
        return None
    cluster, j = 0, n - 2
    while j >= 0 and _touches_demand(window.iloc[j], zone):
        cluster += 1
        j -= 1
    if cluster < config.MIN_BASE_BARS:
        return None
    anchor = float(window["low"].iloc[n - 1 - cluster :].min())
    return {"trigger_level": float(cur["close"]), "sl_anchor": anchor}


def detect_hammer(window, zone, direction):
    """Búa ngược chiều tại zone → nến sau đóng phá đáy/đỉnh búa."""
    if direction == "SELL":
        def at(p):
            c = window.iloc[p]
            if _touches_supply(c, zone) and is_inverted_hammer(c):
                return float(c["low"]), float(c["high"])
            return None
        return _sell_trigger(window, at)

    def at(p):
        c = window.iloc[p]
        if _touches_demand(c, zone) and is_hammer(c):
            return float(c["high"]), float(c["low"])
        return None
    return _buy_trigger(window, at)


def detect_harami(window, zone, direction):
    """Mẹ bồng con: nến mẹ lớn + nến con nhỏ ngược màu nằm trong thân mẹ, tại zone."""
    def _is_harami_bear(mother, child):
        return (
            float(mother["close"]) > float(mother["open"])          # mẹ xanh
            and float(child["close"]) < float(child["open"])        # con đỏ
            and _body(child) <= 0.5 * _body(mother)
            and max(float(child["open"]), float(child["close"])) <= float(mother["close"])
            and min(float(child["open"]), float(child["close"])) >= float(mother["open"])
            and float(child["high"]) <= float(mother["high"])
        )

    def _is_harami_bull(mother, child):
        return (
            float(mother["close"]) < float(mother["open"])
            and float(child["close"]) > float(child["open"])
            and _body(child) <= 0.5 * _body(mother)
            and max(float(child["open"]), float(child["close"])) <= float(mother["open"])
            and min(float(child["open"]), float(child["close"])) >= float(mother["close"])
            and float(child["low"]) >= float(mother["low"])
        )

    if direction == "SELL":
        def at(p):  # p = nến con, p-1 = nến mẹ
            mother, child = window.iloc[p - 1], window.iloc[p]
            if _touches_supply(mother, zone) and _is_harami_bear(mother, child):
                return float(child["low"]), float(mother["high"])
            return None
        return _sell_trigger(window, at)

    def at(p):
        mother, child = window.iloc[p - 1], window.iloc[p]
        if _touches_demand(mother, zone) and _is_harami_bull(mother, child):
            return float(child["high"]), float(mother["low"])
        return None
    return _buy_trigger(window, at)


def detect_double_doji(window, zone, direction):
    """2 nến doji liên tiếp trong zone → nến xác nhận phá đáy/đỉnh cụm doji."""
    if direction == "SELL":
        def at(p):
            d1, d2 = window.iloc[p - 1], window.iloc[p]
            if (is_doji(d1) and is_doji(d2)
                    and _touches_supply(d1, zone) and _touches_supply(d2, zone)):
                return (min(float(d1["low"]), float(d2["low"])),
                        max(float(d1["high"]), float(d2["high"])))
            return None
        return _sell_trigger(window, at)

    def at(p):
        d1, d2 = window.iloc[p - 1], window.iloc[p]
        if (is_doji(d1) and is_doji(d2)
                and _touches_demand(d1, zone) and _touches_demand(d2, zone)):
            return (max(float(d1["high"]), float(d2["high"])),
                    min(float(d1["low"]), float(d2["low"])))
        return None
    return _buy_trigger(window, at)


def detect_pinbar(window, zone, direction):
    """Pin bar từ chối giá tại zone → nến sau đóng phá đáy/đỉnh pin bar."""
    if direction == "SELL":
        def at(p):
            c = window.iloc[p]
            if _touches_supply(c, zone) and is_bearish_pinbar(c):
                return float(c["low"]), float(c["high"])
            return None
        return _sell_trigger(window, at)

    def at(p):
        c = window.iloc[p]
        if _touches_demand(c, zone) and is_bullish_pinbar(c):
            return float(c["high"]), float(c["low"])
        return None
    return _buy_trigger(window, at)


def detect_double_hammer(window, zone, direction):
    """2 nến râu dài liên tiếp chạm zone (2 lần phá thất bại) → nến xác nhận phá cụm."""
    if direction == "SELL":
        def at(p):
            c1, c2 = window.iloc[p - 1], window.iloc[p]
            if (has_long_upper_wick(c1) and has_long_upper_wick(c2)
                    and _touches_supply(c1, zone) and _touches_supply(c2, zone)):
                return (min(float(c1["low"]), float(c2["low"])),
                        max(float(c1["high"]), float(c2["high"])))
            return None
        return _sell_trigger(window, at)

    def at(p):
        c1, c2 = window.iloc[p - 1], window.iloc[p]
        if (has_long_lower_wick(c1) and has_long_lower_wick(c2)
                and _touches_demand(c1, zone) and _touches_demand(c2, zone)):
            return (max(float(c1["high"]), float(c2["high"])),
                    min(float(c1["low"]), float(c2["low"])))
        return None
    return _buy_trigger(window, at)
