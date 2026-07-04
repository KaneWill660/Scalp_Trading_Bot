"""
Scalp Trading Bot — EMA Trend (H1) + Pullback M5.
Quét tín hiệu mỗi phút trong phiên London/NY, 1 lệnh duy nhất TP theo RR 1:2.
Symbols cấu hình qua .env.
"""

import asyncio
from datetime import datetime, timezone

import MetaTrader5 as mt5
from loguru import logger

import config
from connectors.mt5_connector import (
    connect,
    disconnect,
    get_account_balance,
    get_all_positions,
    get_open_positions,
    move_sl_to_entry,
    place_market_order,
)
from notifications.telegram_commands import poll_commands
from notifications.telegram_notifier import send_message, send_signal
from risk.risk_manager import DailyRiskTracker
from strategy.entry_manager import check_for_signal
from strategy.session import is_trading_session

# Track last signal time per symbol to prevent duplicate entries
_last_signal_time: dict = {}


def _validate_symbols() -> list:
    """Kiểm tra từng symbol trong config tồn tại trên broker, bật vào Market Watch."""
    valid = []
    for sym in config.SYMBOLS:
        info = mt5.symbol_info(sym)
        if info is None:
            logger.warning(f"Symbol {sym} không tồn tại trên broker — bỏ qua (kiểm tra suffix trong .env)")
            continue
        if not info.visible and not mt5.symbol_select(sym, True):
            logger.warning(f"Không bật được {sym} trong Market Watch — bỏ qua")
            continue
        valid.append(sym)
    return valid


async def trading_loop(bot_state: dict, symbols: list):
    balance = get_account_balance()
    tracker = DailyRiskTracker(balance, config.MAX_DAILY_LOSS_PCT)
    last_reset_day = datetime.now(timezone.utc).date()
    open_tickets   = {}  # {ticket: {"symbol": str, "balance_before": float}}
    be_done        = set()  # tickets đã được auto-BE

    while True:
        now = datetime.now(timezone.utc)

        # Reset daily tracker at start of new day
        if now.date() != last_reset_day:
            balance = get_account_balance()
            tracker.reset(balance)
            last_reset_day = now.date()
            logger.info(f"New trading day — balance: {balance:.2f}")

        # Auto-resume after /pause N expires
        pause_until = bot_state.get("pause_until")
        if pause_until and now >= pause_until:
            bot_state["paused"] = False
            bot_state["pause_until"] = None
            await send_message("▶️ Hết thời gian tạm dừng — Bot tiếp tục quét tín hiệu.")

        # Auto breakeven khi R:R đạt 1:1
        for p in get_all_positions():
            if p.ticket in be_done:
                continue
            if p.sl == 0 or p.sl == p.price_open:
                continue
            risk = abs(p.price_open - p.sl)
            moved = (p.price_current - p.price_open) if p.type == 0 else (p.price_open - p.price_current)
            if moved >= risk:
                if move_sl_to_entry(p.ticket):
                    be_done.add(p.ticket)
                    logger.info(f"Auto-BE triggered | ticket={p.ticket} | {p.symbol}")
                    await send_message(
                        f"🔒 <b>Auto Breakeven</b> — {p.symbol}\n"
                        f"Ticket: <code>{p.ticket}</code> đã đạt 1:1 R:R → SL chuyển về entry."
                    )

        # Kiểm tra lệnh đã đóng → record PnL
        current_open = {p.ticket for p in get_all_positions()}
        for ticket, info in list(open_tickets.items()):
            if ticket not in current_open:
                bal_after = get_account_balance()
                pnl = bal_after - info["balance_before"]
                tracker.record_trade(pnl)
                status = "❌ <b>Lệnh đóng — THUA</b>" if pnl < 0 else "✅ <b>Lệnh đóng — THẮNG</b>"
                await send_message(
                    f"{status}\n"
                    f"Symbol: {info['symbol']} | Ticket: <code>{ticket}</code>\n"
                    f"PnL: {pnl:+.2f} USD"
                )
                del open_tickets[ticket]
                balance = get_account_balance()

        # Circuit breaker check
        if tracker.circuit_breaker_tripped():
            logger.warning("Circuit breaker active — waiting until next day")
            await asyncio.sleep(config.CHECK_INTERVAL_SEC)
            continue

        # Paused by /stop or /pause command
        if bot_state.get("paused"):
            logger.debug("Bot paused — skipping signal scan")
            await asyncio.sleep(config.CHECK_INTERVAL_SEC)
            continue

        # Only analyze during London/NY session
        if not is_trading_session():
            logger.debug("Outside session — sleeping")
            await asyncio.sleep(config.CHECK_INTERVAL_SEC)
            continue

        # Scan each symbol
        balance = get_account_balance()
        risk_percent = bot_state.get("risk_percent", config.RISK_PERCENT)

        for symbol in symbols:
            # 1 vị thế mở per symbol
            if get_open_positions(symbol):
                continue

            # Cooldown per symbol
            last_sig = _last_signal_time.get(symbol)
            if last_sig and (now - last_sig).total_seconds() < config.SIGNAL_COOLDOWN_MIN * 60:
                continue

            fixed_lot = config.get_symbol_lot(symbol) or None
            signal = check_for_signal(symbol, balance, risk_percent, fixed_lot)

            if signal:
                logger.info(f"Signal found: {symbol} {signal['direction']} @ {signal['entry']}")
                _last_signal_time[symbol] = now  # prevent duplicate signals even if order fails

                await send_signal(signal)

                ticket = place_market_order(
                    direction=signal["direction"],
                    lot=signal["lot"],
                    sl=signal["sl"],
                    tp=signal["tp"],
                    symbol=symbol,
                    comment="Scalp_Bot",
                )

                if ticket:
                    open_tickets[ticket] = {"symbol": symbol, "balance_before": balance}
                    await send_message(
                        f"✅ <b>Order placed</b> — {symbol} {signal['direction']}\n"
                        f"  Lot: {signal['lot']} | Entry: ~{signal['entry']}\n"
                        f"  SL: {signal['sl']} | TP: {signal['tp']} (RR 1:{config.RR:g})\n"
                        f"  Ticket: <code>{ticket}</code>"
                    )
                else:
                    logger.error(f"Order failed for {symbol}")
                    terminal = mt5.terminal_info()
                    if terminal and not terminal.trade_allowed:
                        await send_message(
                            f"❌ <b>Order failed</b> — {symbol}\n"
                            f"⚠️ <b>Auto Trading đang TẮT!</b> Bật nút Auto Trading trong MT5 để đặt lệnh."
                        )
                    else:
                        await send_message(f"❌ <b>Order failed</b> — {symbol}. Check logs.")

        await asyncio.sleep(config.CHECK_INTERVAL_SEC)


async def run_bot():
    logger.info("Scalp Trading Bot starting...")

    if not connect():
        logger.error("Failed to connect to MT5 — exiting")
        return

    symbols = _validate_symbols()
    if not symbols:
        logger.error("Không có symbol hợp lệ nào — exiting")
        disconnect()
        return
    logger.info(f"Symbols: {symbols}")

    terminal = mt5.terminal_info()
    if terminal and not terminal.trade_allowed:
        await send_message("⚠️ <b>Auto Trading đang TẮT trong MT5!</b>\nBật nút Auto Trading trên toolbar MT5 để bot có thể đặt lệnh.")

    await send_message(
        f"🤖 <b>Scalp Trading Bot started</b>\n"
        f"Chiến lược: EMA Trend H1 + Pullback M5 (RR 1:{config.RR:g})\n"
        f"Symbols: {', '.join(symbols)}\n"
        f"Phiên: {config.SESSION_START_MIN // 60:02d}:{config.SESSION_START_MIN % 60:02d}–"
        f"{config.SESSION_END_MIN // 60:02d}:{config.SESSION_END_MIN % 60:02d} UTC\n"
        f"Gõ /help để xem danh sách lệnh."
    )

    bot_state = {
        "paused":       False,
        "pause_until":  None,
        "risk_percent": config.RISK_PERCENT,
    }

    try:
        await asyncio.gather(
            trading_loop(bot_state, symbols),
            poll_commands(bot_state),
        )
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        await send_message("🛑 <b>Scalp Trading Bot stopped</b>")
    finally:
        disconnect()


if __name__ == "__main__":
    asyncio.run(run_bot())
