"""
Risk management: lot size generic đa symbol, daily loss tracking, circuit breaker.
"""

import math

import MetaTrader5 as mt5
from loguru import logger

FALLBACK_MIN_LOT = 0.01


def calculate_lot_size(
    symbol: str,
    balance: float,
    entry: float,
    sl: float,
    risk_percent: float = 0.01,
) -> float:
    """
    Tính lot sao cho nếu dính SL thì lỗ = risk_percent × balance.
    Generic mọi symbol: loss/lot = sl_distance / tick_size × tick_value
    (tick_size/tick_value lấy từ mt5.symbol_info).
    """
    sl_distance = abs(entry - sl)
    info = mt5.symbol_info(symbol)

    if info is None or sl_distance == 0:
        logger.warning(f"{symbol}: symbol_info missing or SL distance 0 — returning min lot")
        return FALLBACK_MIN_LOT

    tick_size  = info.trade_tick_size or info.point
    tick_value = info.trade_tick_value
    if not tick_size or not tick_value:
        logger.warning(f"{symbol}: tick_size/tick_value unavailable — returning volume_min")
        return info.volume_min or FALLBACK_MIN_LOT

    loss_per_lot = sl_distance / tick_size * tick_value
    risk_amount  = balance * risk_percent
    lot = risk_amount / loss_per_lot

    step = info.volume_step or 0.01
    lot  = math.floor(lot / step) * step
    lot  = max(info.volume_min or FALLBACK_MIN_LOT, min(info.volume_max or 100.0, lot))
    return round(lot, 2)


def calculate_tp(
    entry: float,
    sl: float,
    rr: float = 2.0,
    direction: str = "BUY",
    digits: int = 2,
) -> float:
    """TP theo tỉ lệ RR từ khoảng cách SL."""
    tp_distance = abs(entry - sl) * rr
    if direction == "BUY":
        return round(entry + tp_distance, digits)
    return round(entry - tp_distance, digits)


class DailyRiskTracker:
    """Track daily PnL and trigger circuit breaker if max loss is exceeded."""

    def __init__(
        self,
        initial_balance: float,
        max_daily_loss_pct: float = 0.03,
    ):
        self.initial_balance    = initial_balance
        self.max_daily_loss_pct = max_daily_loss_pct
        self.daily_pnl          = 0.0

    def record_trade(self, pnl: float):
        self.daily_pnl += pnl
        if pnl < 0:
            logger.warning(f"Trade loss recorded: {pnl:.2f} | Daily PnL: {self.daily_pnl:.2f}")
        else:
            logger.info(f"Trade win recorded: +{pnl:.2f} | Daily PnL: {self.daily_pnl:.2f}")

    def circuit_breaker_tripped(self) -> bool:
        max_loss = self.initial_balance * self.max_daily_loss_pct
        tripped = self.daily_pnl <= -max_loss
        if tripped:
            logger.warning(
                f"Circuit breaker TRIPPED | Daily PnL: {self.daily_pnl:.2f} "
                f"/ Max loss: -{max_loss:.2f}"
            )
        return tripped

    def reset(self, new_balance: float):
        self.initial_balance = new_balance
        self.daily_pnl       = 0.0
        logger.info(f"Daily risk tracker reset | Balance: {new_balance:.2f}")
