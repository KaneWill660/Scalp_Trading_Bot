import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
BASE_URL  = f"https://api.telegram.org/bot{BOT_TOKEN}"

CANDLE_NAMES = {"engulfing": "Engulfing", "pinbar": "Pin Bar"}


def format_signal_message(signal: dict) -> str:
    direction       = signal["direction"]
    direction_emoji = "🟢" if direction == "BUY" else "🔴"
    trend_arrow     = "↑" if signal["trend"] == "bullish" else "↓"

    entry = signal["entry"]
    sl    = signal["sl"]
    tp    = signal["tp"]
    rr    = round(abs(tp - entry) / abs(entry - sl), 2)

    candle = CANDLE_NAMES.get(signal.get("candle_type", ""), signal.get("candle_type", "?"))

    time_str = signal.get("time") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if signal.get("strategy") == "price_action":
        analysis = f"  Setup ({signal.get('entry_tf', '?')}) : {signal.get('setup', '?')} ✅\n"
        if signal.get("zone"):
            analysis += f"  S/D Zone    : {signal['zone']}\n"
    else:
        analysis = (
            f"  {signal.get('entry_tf', 'M5')} Pullback : vùng EMA {signal.get('ema_zone', 'N/A')}\n"
            f"  Nến xác nhận: {candle} ✅\n"
        )

    return (
        f"{direction_emoji} <b>SIGNAL: {direction} {signal['symbol']}</b>\n"
        f"\n"
        f"📊 <b>Phân tích:</b>\n"
        f"  H1 Trend    : {signal['trend'].upper()} {trend_arrow} (EMA50/EMA200)\n"
        f"{analysis}"
        f"\n"
        f"💰 <b>Trade Setup:</b>\n"
        f"  Entry      : ~{entry}\n"
        f"  Stop Loss  : {sl}\n"
        f"  Take Profit: {tp}\n"
        f"  RR Ratio   : 1 : {rr}\n"
        f"  Lot        : {signal['lot']}\n"
        f"\n"
        f"🕐 {time_str}\n"
        f"📍 Session: {signal.get('session', 'N/A')}"
    )


async def send_signal(signal: dict) -> bool:
    """Send entry signal notification to Telegram. Returns True on success."""
    if not BOT_TOKEN or BOT_TOKEN == "your_token_here":
        logger.warning("Telegram BOT_TOKEN not configured — skipping notification")
        return False

    msg = format_signal_message(signal)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{BASE_URL}/sendMessage",
                json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            )
        if resp.status_code != 200:
            logger.error(f"Telegram send failed ({resp.status_code}): {resp.text}")
            return False
        logger.info(f"Telegram signal sent: {signal['direction']} @ {signal['entry']}")
        return True
    except httpx.RequestError as e:
        logger.error(f"Telegram network error: {e}")
        return False


async def send_message(text: str) -> bool:
    """Send a plain text message to Telegram."""
    if not BOT_TOKEN or BOT_TOKEN == "your_token_here":
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{BASE_URL}/sendMessage",
                json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            )
        return resp.status_code == 200
    except httpx.RequestError as e:
        logger.error(f"Telegram network error: {e}")
        return False
