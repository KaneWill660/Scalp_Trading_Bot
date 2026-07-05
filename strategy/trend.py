"""Xác định trend H1 bằng EMA50/EMA200, kèm lọc chất lượng trend (ADX, khoảng cách EMA)."""

import pandas as pd
from loguru import logger

import config
from strategy.indicators import adx, atr, ema


def get_h1_trend(df_h1: pd.DataFrame, drop_last_open: bool = True) -> "str | None":
    """
    Trend từ nến H1 đã đóng:
      EMA50 > EMA200 và Close > EMA50 → "bullish"
      EMA50 < EMA200 và Close < EMA50 → "bearish"
      còn lại → None
    Lọc chất lượng (bật qua .env, 0 = tắt):
      MIN_ADX_H1        — bỏ trend nếu ADX(14) H1 < ngưỡng
      MIN_EMA_GAP_ATR   — bỏ trend nếu |EMA50−EMA200| < X×ATR(14) H1
      MIN_EMA_SLOPE_ATR — bỏ trend nếu EMA50 dịch < X×ATR trong EMA_SLOPE_BARS nến
                          (loại trend "phẳng" — thị trường sideway)
    drop_last_open: bỏ nến cuối (đang hình thành) khi chạy live.
    """
    df = df_h1.iloc[:-1] if drop_last_open else df_h1
    if len(df) < config.EMA_TREND_SLOW:
        return None

    close       = df["close"]
    ema_fast_sr = ema(close, config.EMA_TREND_FAST)
    ema_fast    = float(ema_fast_sr.iloc[-1])
    ema_slow    = float(ema(close, config.EMA_TREND_SLOW).iloc[-1])
    last        = float(close.iloc[-1])

    if ema_fast > ema_slow and last > ema_fast:
        trend = "bullish"
    elif ema_fast < ema_slow and last < ema_fast:
        trend = "bearish"
    else:
        return None

    if config.MIN_ADX_H1 > 0:
        adx_val = float(adx(df, config.ADX_PERIOD).iloc[-1])
        if adx_val < config.MIN_ADX_H1:
            logger.debug(f"Trend {trend} bị loại: ADX H1 {adx_val:.1f} < {config.MIN_ADX_H1}")
            return None

    if config.MIN_EMA_GAP_ATR > 0:
        atr_h1 = float(atr(df, config.ATR_PERIOD).iloc[-1])
        if abs(ema_fast - ema_slow) < config.MIN_EMA_GAP_ATR * atr_h1:
            logger.debug(f"Trend {trend} bị loại: gap EMA < {config.MIN_EMA_GAP_ATR}×ATR H1")
            return None

    if config.MIN_EMA_SLOPE_ATR > 0 and len(ema_fast_sr) > config.EMA_SLOPE_BARS:
        atr_h1 = float(atr(df, config.ATR_PERIOD).iloc[-1])
        if atr_h1 > 0:
            slope = (ema_fast - float(ema_fast_sr.iloc[-1 - config.EMA_SLOPE_BARS])) / atr_h1
            if trend == "bullish" and slope < config.MIN_EMA_SLOPE_ATR:
                logger.debug(f"Trend bullish bị loại: slope EMA50 {slope:.2f} < {config.MIN_EMA_SLOPE_ATR}×ATR")
                return None
            if trend == "bearish" and slope > -config.MIN_EMA_SLOPE_ATR:
                logger.debug(f"Trend bearish bị loại: slope EMA50 {slope:.2f} > -{config.MIN_EMA_SLOPE_ATR}×ATR")
                return None

    return trend
