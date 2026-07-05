"""
Central config — đọc .env, tập trung mọi tham số chiến lược một chỗ.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _parse_hhmm(value: str, default_minutes: int) -> int:
    """Parse 'HH:MM' → số phút trong ngày."""
    try:
        h, m = value.strip().split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return default_minutes


# ── Symbols ───────────────────────────────────────────────────────────────────
SYMBOLS = [
    s.strip()
    for s in os.getenv("SYMBOLS", "XAUUSDc,EURUSDc,GBPUSDc,BTCUSDc").split(",")
    if s.strip()
]

# ── Chiến lược ────────────────────────────────────────────────────────────────
# ema_pullback  — trend H1 + pullback EMA20/50 + nến xác nhận (mặc định)
# price_action  — Supply/Demand zone + 8 setup price action (PATTERNS bên dưới)
STRATEGY = os.getenv("STRATEGY", "ema_pullback").strip().lower()

# ── Trend H1 ──────────────────────────────────────────────────────────────────
EMA_TREND_FAST = int(os.getenv("EMA_TREND_FAST", "50"))
EMA_TREND_SLOW = int(os.getenv("EMA_TREND_SLOW", "200"))

# Lọc chất lượng trend H1 (0 = tắt)
ADX_PERIOD      = int(os.getenv("ADX_PERIOD", "14"))
MIN_ADX_H1      = float(os.getenv("MIN_ADX_H1", "0"))        # vd 25: chỉ trade khi ADX H1 >= 25
MIN_EMA_GAP_ATR = float(os.getenv("MIN_EMA_GAP_ATR", "0"))   # vd 1.0: |EMA50−EMA200| >= 1×ATR H1

# ── Khung entry + Pullback EMA ────────────────────────────────────────────────
ENTRY_TF = os.getenv("ENTRY_TF", "M5").upper()               # M5 | M15 | M30
ENTRY_TF_MINUTES = {"M5": 5, "M15": 15, "M30": 30}.get(ENTRY_TF, 5)

EMA_PULLBACK_FAST = int(os.getenv("EMA_PULLBACK_FAST", "20"))
EMA_PULLBACK_SLOW = int(os.getenv("EMA_PULLBACK_SLOW", "50"))

# Loại nến xác nhận được chấp nhận: engulfing, pinbar (mặc định cả hai)
CANDLE_TYPES = [
    c.strip().lower()
    for c in os.getenv("CANDLE_TYPES", "engulfing,pinbar").split(",")
    if c.strip()
]

# ── Chiến lược price_action (STRATEGY=price_action) ──────────────────────────
# Các setup bật (thứ tự = độ ưu tiên khi nhiều setup khớp cùng 1 nến)
PATTERNS = [
    p.strip().lower()
    for p in os.getenv(
        "PATTERNS",
        "engulfing,hammer,harami,double_doji,pinbar,double_hammer,flag,triangle",
    ).split(",")
    if p.strip()
]

# Supply/Demand zone
ZONE_LOOKBACK       = int(os.getenv("ZONE_LOOKBACK", "150"))     # số nến quét zone
ZONE_SWING_N        = int(os.getenv("ZONE_SWING_N", "3"))        # fractal swing
IMPULSE_ATR_MULT    = float(os.getenv("IMPULSE_ATR_MULT", "2.0"))  # nhịp rời vùng >= X×ATR
ZONE_ATR_MULT       = float(os.getenv("ZONE_ATR_MULT", "0.5"))   # độ dày zone
ZONE_SL_BUFFER_ATR  = float(os.getenv("ZONE_SL_BUFFER_ATR", "0.25"))  # đệm SL trên đỉnh mẫu nến

# Setup nến tại zone
MIN_BASE_BARS       = int(os.getenv("MIN_BASE_BARS", "2"))       # cụm giằng co tối thiểu (engulfing)
CONFIRM_WINDOW_BARS = int(os.getenv("CONFIRM_WINDOW_BARS", "3")) # pattern phải trong N nến gần nhất
DOJI_BODY_RATIO     = float(os.getenv("DOJI_BODY_RATIO", "0.15"))

# Flag
FLAG_POLE_BARS      = int(os.getenv("FLAG_POLE_BARS", "10"))
FLAG_POLE_ATR       = float(os.getenv("FLAG_POLE_ATR", "3.0"))
FLAG_MIN_BARS       = int(os.getenv("FLAG_MIN_BARS", "4"))
FLAG_MAX_BARS       = int(os.getenv("FLAG_MAX_BARS", "15"))

# Triangle
TRIANGLE_WINDOW     = int(os.getenv("TRIANGLE_WINDOW", "30"))
TRIANGLE_COMPRESS   = float(os.getenv("TRIANGLE_COMPRESS", "0.6"))

# ── SL / TP ───────────────────────────────────────────────────────────────────
ATR_PERIOD         = int(os.getenv("ATR_PERIOD", "14"))
SL_ATR_MULT        = float(os.getenv("SL_ATR_MULT", "1.5"))    # đệm SL = 1.5×ATR(14) M5
RR                 = float(os.getenv("RR", "2.0"))             # TP = RR × khoảng SL
MAX_SL_ATR_H1_MULT = float(os.getenv("MAX_SL_ATR_H1_MULT", "1.0"))  # bỏ tín hiệu nếu SL > 1×ATR H1

# ── Lọc RSI khung entry (0 = tắt) ─────────────────────────────────────────────
# BUY chỉ khi RSI <= RSI_BUY_MAX (pullback đủ sâu); SELL chỉ khi RSI >= RSI_SELL_MIN
RSI_FILTER   = int(os.getenv("RSI_FILTER", "0"))
RSI_PERIOD   = int(os.getenv("RSI_PERIOD", "14"))
RSI_BUY_MAX  = float(os.getenv("RSI_BUY_MAX", "45"))
RSI_SELL_MIN = float(os.getenv("RSI_SELL_MIN", "55"))

# ── Lọc độ dốc EMA50 H1 (0 = tắt) — loại trend "phẳng"/sideway ────────────────
# Yêu cầu EMA50 H1 dịch chuyển >= X × ATR H1 trong EMA_SLOPE_BARS nến, đúng hướng trend
EMA_SLOPE_BARS    = int(os.getenv("EMA_SLOPE_BARS", "5"))
MIN_EMA_SLOPE_ATR = float(os.getenv("MIN_EMA_SLOPE_ATR", "0"))

# ── Lọc volume nến xác nhận (0 = tắt) ─────────────────────────────────────────
# Volume nến xác nhận >= X × trung bình VOL_AVG_BARS nến trước đó
MIN_VOL_CONFIRM_MULT = float(os.getenv("MIN_VOL_CONFIRM_MULT", "0"))
VOL_AVG_BARS         = int(os.getenv("VOL_AVG_BARS", "20"))

# ── Session (giờ UTC) ─────────────────────────────────────────────────────────
# SESSION_FILTER=0 → trade 24/7, bỏ qua giờ phiên lẫn cuối tuần
# (thị trường đóng cửa thì MT5 tự chặn lệnh, BTC vẫn trade được cuối tuần)
SESSION_FILTER    = int(os.getenv("SESSION_FILTER", "1"))
# Symbol trade 24/7 (crypto) — luôn bỏ qua lọc phiên + cuối tuần, kể cả khi SESSION_FILTER=1
SESSION_24_7_SYMBOLS = [
    s.strip()
    for s in os.getenv("SESSION_24_7_SYMBOLS", "BTCUSDc").split(",")
    if s.strip()
]
SESSION_START_MIN = _parse_hhmm(os.getenv("SESSION_START", "07:00"), 7 * 60)
SESSION_END_MIN   = _parse_hhmm(os.getenv("SESSION_END", "15:30"), 15 * 60 + 30)
# Chênh lệch giờ server MT5 so với UTC (dùng cho backtest — data trả về theo giờ server)
SERVER_UTC_OFFSET_HOURS = int(os.getenv("SERVER_UTC_OFFSET_HOURS", "0"))

# ── Risk ──────────────────────────────────────────────────────────────────────
RISK_PERCENT        = float(os.getenv("RISK_PERCENT", "0.01"))
MAX_DAILY_LOSS_PCT  = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.03"))
LEVERAGE            = int(os.getenv("LEVERAGE", "100"))
# Lỗ tối đa cho 1 lệnh (USD). 0 = tắt. Nếu lỗ dự kiến tại SL > ngưỡng này → bỏ lệnh.
MAX_LOSS_PER_TRADE  = float(os.getenv("MAX_LOSS_PER_TRADE", "0"))

# ── Live loop ─────────────────────────────────────────────────────────────────
CHECK_INTERVAL_SEC  = int(os.getenv("CHECK_INTERVAL_SEC", "60"))
SIGNAL_COOLDOWN_MIN = int(os.getenv("SIGNAL_COOLDOWN_MIN", "15"))

MAGIC = 20260703


def get_symbol_lot(symbol: str) -> float:
    """Lot cố định per-symbol từ .env (SYMBOL_LOT_<symbol>). Trả 0.0 nếu không set."""
    val = os.getenv(f"SYMBOL_LOT_{symbol}", "").strip()
    try:
        return float(val) if val else 0.0
    except ValueError:
        return 0.0
