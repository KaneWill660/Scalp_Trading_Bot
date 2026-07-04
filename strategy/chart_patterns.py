"""
Mô hình giá: Bull/Bear Flag và Symmetrical Triangle (heuristic swing-based).

Quy ước: window là nến đã đóng, dòng cuối = nến breakout tiềm năng.
Detector trả {"trigger_level","sl_anchor"} hoặc None; chiều trade do trend H1
quyết định (flag tăng chỉ BUY khi bullish — "không sell ngược trong cờ tăng").
"""

import config
from strategy.zones import swing_indices


def detect_flag(window, direction, atr_val):
    """
    Cột cờ: nhịp chạy mạnh >= FLAG_POLE_ATR × ATR trong FLAG_POLE_BARS nến.
    Lá cờ: FLAG_MIN..FLAG_MAX nến điều chỉnh nhẹ ngược chiều, retrace <= 50% cột.
    Trigger: nến cuối là nến ĐẦU TIÊN đóng ngoài biên lá cờ theo hướng trend.
    """
    cur_close = float(window["close"].iloc[-1])
    n = len(window)

    for length in range(config.FLAG_MIN_BARS, config.FLAG_MAX_BARS + 1):
        if n < length + config.FLAG_POLE_BARS + 2:
            break
        flag = window.iloc[n - 1 - length : n - 1]
        pole = window.iloc[n - 1 - length - config.FLAG_POLE_BARS : n - 1 - length]

        if direction == "BUY":
            pole_low  = float(pole["low"].min())
            pole_high = float(pole["high"].max())
            pole_rise = pole_high - pole_low
            if pole_rise < config.FLAG_POLE_ATR * atr_val:
                continue  # không có cột cờ đủ mạnh
            if float(pole["close"].iloc[-1]) <= float(pole["close"].iloc[0]):
                continue  # cột không dốc lên
            flag_high = float(flag["high"].max())
            flag_low  = float(flag["low"].min())
            if flag_low < pole_high - 0.5 * pole_rise:
                continue  # điều chỉnh quá sâu — không còn là lá cờ
            if flag_high - flag_low > 0.5 * pole_rise:
                continue  # lá cờ quá rộng so với cột — không phải "nghỉ gọn"
            if float(flag["close"].iloc[-1]) > float(flag["close"].iloc[0]):
                continue  # lá cờ phải trôi xuống nhẹ (nghỉ lấy đà)
            prev_close = float(window["close"].iloc[-2])
            if prev_close <= flag_high < cur_close:  # nến đầu tiên phá cạnh trên
                return {"trigger_level": flag_high, "sl_anchor": flag_low}

        else:  # SELL — bear flag đối xứng
            pole_low  = float(pole["low"].min())
            pole_high = float(pole["high"].max())
            pole_drop = pole_high - pole_low
            if pole_drop < config.FLAG_POLE_ATR * atr_val:
                continue
            if float(pole["close"].iloc[-1]) >= float(pole["close"].iloc[0]):
                continue
            flag_high = float(flag["high"].max())
            flag_low  = float(flag["low"].min())
            if flag_high > pole_low + 0.5 * pole_drop:
                continue
            if flag_high - flag_low > 0.5 * pole_drop:
                continue
            if float(flag["close"].iloc[-1]) < float(flag["close"].iloc[0]):
                continue
            prev_close = float(window["close"].iloc[-2])
            if prev_close >= flag_low > cur_close:
                return {"trigger_level": flag_low, "sl_anchor": flag_high}

    return None


def detect_triangle(window, direction, atr_val):
    """
    Tam giác đối xứng trong TRIANGLE_WINDOW nến (không tính nến breakout):
      >= 2 swing high thấp dần + >= 2 swing low cao dần, biên độ nén còn
      < TRIANGLE_COMPRESS × biên độ ban đầu.
    Trigger: nến cuối đóng ngoài swing gần nhất theo hướng trend
    (đơn giản hóa: dùng swing extreme gần nhất làm cạnh thay vì fit trendline).
    """
    n = len(window)
    if n < config.TRIANGLE_WINDOW + 2:
        return None
    seg = window.iloc[n - 1 - config.TRIANGLE_WINDOW : n - 1]

    highs = seg["high"].values
    lows  = seg["low"].values
    sh = swing_indices(highs, 2, "high")
    sl = swing_indices(lows, 2, "low")
    if len(sh) < 2 or len(sl) < 2:
        return None

    h1, h2 = highs[sh[-2]], highs[sh[-1]]   # 2 swing high gần nhất
    l1, l2 = lows[sl[-2]], lows[sl[-1]]     # 2 swing low gần nhất
    if not (h2 < h1 and l2 > l1):
        return None  # không phải đỉnh thấp dần + đáy cao dần
    early_range = h1 - l1
    late_range  = h2 - l2
    if early_range <= 0 or late_range <= 0:
        return None
    if late_range > config.TRIANGLE_COMPRESS * early_range:
        return None  # chưa nén đủ

    cur_close  = float(window["close"].iloc[-1])
    prev_close = float(window["close"].iloc[-2])

    if direction == "BUY":
        if prev_close <= h2 < cur_close:  # nến đầu tiên đóng trên cạnh trên
            return {"trigger_level": float(h2), "sl_anchor": float(l2)}
    else:
        if prev_close >= l2 > cur_close:
            return {"trigger_level": float(l2), "sl_anchor": float(h2)}
    return None
