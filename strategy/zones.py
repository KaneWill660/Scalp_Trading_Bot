"""
Supply/Demand zone detection.

Supply zone: swing high mà sau đó giá rơi mạnh (>= IMPULSE_ATR_MULT × ATR)
— nơi phe bán từng áp đảo. Zone bị vô hiệu khi có nến ĐÓNG CỬA vượt đỉnh zone.
Demand zone: đối xứng với swing low + nhịp tăng mạnh.
"""

import pandas as pd

import config


def swing_indices(values, n: int, mode: str) -> list:
    """Chỉ số các swing (fractal): cực trị so với n nến mỗi bên."""
    idxs = []
    for i in range(n, len(values) - n):
        window = values[i - n : i + n + 1]
        if mode == "high" and values[i] >= max(window):
            idxs.append(i)
        elif mode == "low" and values[i] <= min(window):
            idxs.append(i)
    return idxs


def find_active_supply(df: pd.DataFrame, atr_val: float) -> "dict | None":
    """
    Zone supply gần nhất còn hiệu lực trong df (nến đã đóng, KHÔNG gồm nến trigger).
    Trả {"top", "bottom", "index"} hoặc None.
    """
    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values

    for i in reversed(swing_indices(highs, config.ZONE_SWING_N, "high")):
        top = highs[i]
        after_lows = lows[i + 1 :]
        if len(after_lows) < config.ZONE_SWING_N + 2:
            continue  # chưa đủ thời gian rời vùng
        if top - after_lows.min() < config.IMPULSE_ATR_MULT * atr_val:
            continue  # không có nhịp giảm mạnh rời vùng — supply yếu
        if (closes[i + 1 :] > top).any():
            continue  # zone đã bị phá (nến đóng vượt đỉnh)
        return {"top": top, "bottom": top - config.ZONE_ATR_MULT * atr_val, "index": i}
    return None


def find_active_demand(df: pd.DataFrame, atr_val: float) -> "dict | None":
    """Đối xứng: swing low + nhịp tăng mạnh rời vùng, chưa có nến đóng thủng đáy."""
    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values

    for i in reversed(swing_indices(lows, config.ZONE_SWING_N, "low")):
        bottom = lows[i]
        after_highs = highs[i + 1 :]
        if len(after_highs) < config.ZONE_SWING_N + 2:
            continue
        if after_highs.max() - bottom < config.IMPULSE_ATR_MULT * atr_val:
            continue
        if (closes[i + 1 :] < bottom).any():
            continue
        return {"top": bottom + config.ZONE_ATR_MULT * atr_val, "bottom": bottom, "index": i}
    return None
