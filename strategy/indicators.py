"""EMA, ATR và ADX — pandas thuần, trả về Series để dùng theo từng bar."""

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR(period) từ DataFrame OHLCV (cột high/low/close)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI chuẩn Wilder (smoothing ewm alpha=1/period)."""
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1.0 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1.0 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(100.0).where(delta.notna(), np.nan)  # loss=0 → RSI 100; bar đầu NaN


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ADX(period) chuẩn Wilder (smoothing ewm alpha=1/period)."""
    high, low, close = df["high"], df["low"], df["close"]
    up   = high.diff()
    down = -low.diff()
    plus_dm  = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    alpha = 1.0 / period
    atr_w    = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_w
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_w
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=alpha, adjust=False).mean()
