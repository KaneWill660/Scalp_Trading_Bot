"""
Entry Manager — dispatch theo config.STRATEGY:

  ema_pullback  (mặc định):
    Trend H1 (EMA50/200) → pullback về vùng EMA20–EMA50 khung entry
    → nến xác nhận engulfing/pin bar → SL đáy/đỉnh nến ± SL_ATR_MULT×ATR

  price_action:
    Trend H1 khóa hướng → Supply/Demand zone + 8 setup infographic
    (engulfing/hammer/harami/double_doji/pinbar/double_hammer/flag/triangle)
    → trigger nến đóng cửa → SL trên/dưới cấu trúc + ZONE_SL_BUFFER_ATR×ATR

Chung cho cả hai: session London/NY, lọc SL > MAX_SL_ATR_H1_MULT×ATR H1,
TP theo RR, lot theo risk % hoặc lot cố định.
"""

from datetime import datetime, timezone

import MetaTrader5 as mt5
from loguru import logger

import config
from connectors.mt5_connector import get_ohlcv
from risk.risk_manager import calculate_lot_size, calculate_tp, loss_at_sl
from strategy.candles import bearish_confirmation, bullish_confirmation
from strategy.indicators import atr, ema, rsi
from strategy.price_action import check_signal as price_action_signal
from strategy.session import get_current_session, is_trading_session
from strategy.trend import get_h1_trend

ENTRY_TF_MT5 = {
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
}.get(config.ENTRY_TF, mt5.TIMEFRAME_M5)

COUNT_H1 = config.EMA_TREND_SLOW + 60   # đủ nến cho EMA200 + đệm

if config.STRATEGY == "price_action":
    COUNT_ENTRY = config.ZONE_LOOKBACK + 10
else:
    COUNT_ENTRY = max(config.EMA_PULLBACK_SLOW, config.ATR_PERIOD) + 70

# Mỗi nến entry chỉ sinh tối đa 1 tín hiệu — {symbol: bar_time đã có tín hiệu}
_last_signal_bar: dict = {}


def _ema_pullback_check(closed, cur, prev, atr_entry, trend, digits) -> "dict | None":
    """Nhánh EMA pullback — trả {direction, sl, candle_type, ema_zone} hoặc None."""
    ema_fast_e = float(ema(closed["close"], config.EMA_PULLBACK_FAST).iloc[-1])
    ema_slow_e = float(ema(closed["close"], config.EMA_PULLBACK_SLOW).iloc[-1])
    zone_top = max(ema_fast_e, ema_slow_e)
    zone_bot = min(ema_fast_e, ema_slow_e)

    if trend == "bullish":
        touched = cur["low"] <= zone_top and cur["close"] >= zone_bot
        candle_type = bullish_confirmation(prev, cur) if touched else None
    else:
        touched = cur["high"] >= zone_bot and cur["close"] <= zone_top
        candle_type = bearish_confirmation(prev, cur) if touched else None

    if not candle_type or candle_type not in config.CANDLE_TYPES:
        return None

    if trend == "bullish":
        direction = "BUY"
        sl = round(float(cur["low"]) - config.SL_ATR_MULT * atr_entry, digits)
    else:
        direction = "SELL"
        sl = round(float(cur["high"]) + config.SL_ATR_MULT * atr_entry, digits)

    return {
        "direction":   direction,
        "sl":          sl,
        "candle_type": candle_type,
        "ema_zone":    f"{zone_bot:.{digits}f}–{zone_top:.{digits}f}",
    }


def check_for_signal(
    symbol: str,
    balance: float,
    risk_percent: float = 0.01,
    fixed_lot: "float | None" = None,
) -> "dict | None":
    """
    Phân tích symbol trên nến entry vừa đóng, trả về signal dict nếu đủ điều kiện.
    fixed_lot: nếu set thì dùng lot này thay vì tính từ risk_percent.
    """
    if not is_trading_session(symbol=symbol):
        return None

    sym_info = mt5.symbol_info(symbol)
    if not sym_info or sym_info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
        logger.debug(f"Market closed for {symbol}")
        return None
    digits = sym_info.digits

    # ── Bước 1: Trend H1 (kèm lọc chất lượng nếu bật) ────────────────────────
    df_h1 = get_ohlcv(mt5.TIMEFRAME_H1, COUNT_H1, symbol)
    if df_h1 is None or len(df_h1) < config.EMA_TREND_SLOW + 1:
        return None
    trend = get_h1_trend(df_h1)  # bỏ nến H1 đang hình thành
    if trend is None:
        logger.debug(f"{symbol} — H1 không có trend đạt chuẩn")
        return None

    # ── Bước 2: Dữ liệu khung entry (chỉ dùng nến đã đóng) ───────────────────
    df_entry = get_ohlcv(ENTRY_TF_MT5, COUNT_ENTRY, symbol)
    if df_entry is None or len(df_entry) < 32:
        return None
    closed = df_entry.iloc[:-1]
    cur    = closed.iloc[-1]   # nến vừa đóng = nến trigger
    prev   = closed.iloc[-2]

    bar_time = cur["time"]
    if _last_signal_bar.get(symbol) == bar_time:
        return None  # nến này đã sinh tín hiệu rồi

    atr_entry = float(atr(closed, config.ATR_PERIOD).iloc[-1])
    atr_h1    = float(atr(df_h1.iloc[:-1], config.ATR_PERIOD).iloc[-1])

    # ── Bước 3: Logic entry theo chiến lược ──────────────────────────────────
    extra = {}
    if config.STRATEGY == "price_action":
        window = closed.tail(config.ZONE_LOOKBACK).reset_index(drop=True)
        res = price_action_signal(window, trend, atr_entry, digits)
        if not res:
            return None
        direction, sl = res["direction"], res["sl"]
        candle_type = res["setup"]
        extra["setup"] = res["setup"]
        if res.get("zone"):
            extra["zone"] = res["zone"]
    else:
        res = _ema_pullback_check(closed, cur, prev, atr_entry, trend, digits)
        if not res:
            return None
        direction, sl, candle_type = res["direction"], res["sl"], res["candle_type"]
        extra["ema_zone"] = res["ema_zone"]

    # ── Bước 4: Lọc RSI + SL xa + TP + lot (chung) ───────────────────────────
    if config.RSI_FILTER:
        rsi_val = float(rsi(closed["close"], config.RSI_PERIOD).iloc[-1])
        if direction == "BUY" and rsi_val > config.RSI_BUY_MAX:
            return None
        if direction == "SELL" and rsi_val < config.RSI_SELL_MIN:
            return None

    entry = float(cur["close"])
    sl_distance = abs(entry - sl)
    if sl_distance <= 0:
        return None
    if sl_distance > config.MAX_SL_ATR_H1_MULT * atr_h1:
        logger.info(
            f"{symbol} — bỏ tín hiệu {direction} ({candle_type}): SL "
            f"{sl_distance:.{digits}f} > {config.MAX_SL_ATR_H1_MULT}×ATR H1 ({atr_h1:.{digits}f})"
        )
        return None

    tp  = calculate_tp(entry, sl, rr=config.RR, direction=direction, digits=digits)
    lot = fixed_lot if fixed_lot else calculate_lot_size(symbol, balance, entry, sl, risk_percent)

    # Chặn lệnh nếu lỗ dự kiến tại SL vượt ngưỡng USD (quan trọng khi dùng lot cố định)
    if config.MAX_LOSS_PER_TRADE > 0:
        est_loss = loss_at_sl(symbol, sl_distance, lot)
        if est_loss > config.MAX_LOSS_PER_TRADE:
            logger.info(
                f"{symbol} — bỏ tín hiệu {direction} ({candle_type}): lỗ dự kiến "
                f"${est_loss:.2f} > MAX_LOSS_PER_TRADE ${config.MAX_LOSS_PER_TRADE:.2f}"
            )
            return None

    _last_signal_bar[symbol] = bar_time
    logger.info(
        f"{symbol} — {direction} signal ({candle_type}, {config.ENTRY_TF}, {config.STRATEGY}) "
        f"@ {entry} | SL {sl} | TP {tp}"
    )

    signal = {
        "symbol":      symbol,
        "direction":   direction,
        "trend":       trend,
        "strategy":    config.STRATEGY,
        "entry_tf":    config.ENTRY_TF,
        "candle_type": candle_type,
        "entry":       round(entry, digits),
        "sl":          sl,
        "tp":          tp,
        "lot":         lot,
        "atr_entry":   round(atr_entry, digits),
        "atr_h1":      round(atr_h1, digits),
        "time":        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "session":     get_current_session(),
    }
    signal.update(extra)
    return signal
