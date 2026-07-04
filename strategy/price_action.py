"""
Engine chiến lược price_action: Supply/Demand zone + 8 setup từ infographic.

check_signal(window, trend, atr_entry, digits):
  window   — DataFrame nến đã đóng (dòng cuối = nến trigger tiềm năng)
  trend    — "bullish"/"bearish" từ H1 (khóa hướng: bearish → chỉ SELL)
  Trả {"direction","entry","sl","setup","zone"?} hoặc None.
  Setup đầu tiên khớp theo thứ tự config.PATTERNS thắng.
"""

import config
from strategy.chart_patterns import detect_flag, detect_triangle
from strategy.patterns import (
    detect_double_doji,
    detect_double_hammer,
    detect_engulfing,
    detect_hammer,
    detect_harami,
    detect_pinbar,
)
from strategy.zones import find_active_demand, find_active_supply

ZONE_SETUPS = {
    "engulfing":     detect_engulfing,
    "hammer":        detect_hammer,
    "harami":        detect_harami,
    "double_doji":   detect_double_doji,
    "pinbar":        detect_pinbar,
    "double_hammer": detect_double_hammer,
}

CHART_SETUPS = {
    "flag":     detect_flag,
    "triangle": detect_triangle,
}


def check_signal(window, trend: str, atr_entry: float, digits: int) -> "dict | None":
    if window is None or len(window) < 30 or not atr_entry or atr_entry <= 0:
        return None

    direction = "SELL" if trend == "bearish" else "BUY"

    zone = None
    if any(s in ZONE_SETUPS for s in config.PATTERNS):
        zone_df = window.iloc[:-1]  # zone phải hình thành trước nến trigger
        zone = (find_active_supply(zone_df, atr_entry) if direction == "SELL"
                else find_active_demand(zone_df, atr_entry))

    entry  = float(window["close"].iloc[-1])
    buffer = config.ZONE_SL_BUFFER_ATR * atr_entry

    for setup in config.PATTERNS:
        if setup in ZONE_SETUPS:
            if zone is None:
                continue
            res = ZONE_SETUPS[setup](window, zone, direction)
        elif setup in CHART_SETUPS:
            res = CHART_SETUPS[setup](window, direction, atr_entry)
        else:
            continue

        if not res:
            continue

        if direction == "SELL":
            sl = round(res["sl_anchor"] + buffer, digits)
            if sl <= entry:
                continue  # SL không hợp lệ (anchor dưới entry)
        else:
            sl = round(res["sl_anchor"] - buffer, digits)
            if sl >= entry:
                continue

        signal = {"direction": direction, "entry": entry, "sl": sl, "setup": setup}
        if setup in ZONE_SETUPS and zone is not None:
            signal["zone"] = f"{zone['bottom']:.{digits}f}–{zone['top']:.{digits}f}"
        return signal

    return None
