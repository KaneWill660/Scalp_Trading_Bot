"""
Backtest chiến lược EMA Trend H1 + Pullback M5 bằng thư viện backtesting.py.
Lấy dữ liệu lịch sử từ MT5 (cần terminal MT5 đang mở).

Indicators được precompute và map H1 → M5 bằng merge_asof (chỉ dùng nến H1
đã đóng tại thời điểm quyết định — không lookahead).

Usage:
  python -m tests.backtest --months 3
  python -m tests.backtest --months 6 --symbol XAUUSDc --cash 10000
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from typing import Optional, Tuple

# Console Windows mặc định là cp1252 — ép UTF-8 để in được ✅/→ khi redirect output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import MetaTrader5 as mt5
import numpy as np
import pandas as pd
from backtesting import Backtest, Strategy
from dotenv import load_dotenv
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

import config
from connectors.mt5_connector import connect, disconnect, get_account_balance, get_ohlcv
from strategy.candles import bearish_confirmation, bullish_confirmation
from strategy.entry_manager import ENTRY_TF_MT5
from strategy.indicators import adx, atr, ema, rsi
from strategy.price_action import check_signal as price_action_signal
from strategy.session import is_trading_session

IND_COLS = ["ema_pb_fast", "ema_pb_slow", "atr_entry", "ema_h1_fast", "ema_h1_slow", "atr_h1", "close_h1"]


def _fetch_with_cap(timeframe: int, count: int, symbol: str) -> Optional[pd.DataFrame]:
    """MT5 từ chối count vượt 'Max bars in chart' — giảm dần 20% tới khi lấy được."""
    while count > 0:
        df = get_ohlcv(timeframe, count, symbol)
        if df is not None:
            return df
        if count < 1000:
            return None
        count = int(count * 0.8)
        logger.warning(f"{symbol} — giảm số nến xuống {count} (vượt giới hạn Max bars của terminal)")
    return None


def fetch_backtest_data(symbol: str, months: int) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    candles_entry = months * 30 * 24 * 60 // config.ENTRY_TF_MINUTES
    candles_h1    = months * 30 * 24 + config.EMA_TREND_SLOW + 50  # đệm cho EMA200

    logger.info(f"Fetching {months} months of data for {symbol} (entry TF: {config.ENTRY_TF})...")
    df_h1    = _fetch_with_cap(mt5.TIMEFRAME_H1, candles_h1, symbol)
    df_entry = _fetch_with_cap(ENTRY_TF_MT5, candles_entry, symbol)
    return df_h1, df_entry


def build_indicator_frame(df_entry: pd.DataFrame, df_h1: pd.DataFrame) -> pd.DataFrame:
    """
    Precompute toàn bộ indicator, map giá trị H1 sang từng bar khung entry.
    Bar H1 mở lúc T chỉ được dùng từ T+1h (đã đóng) — tránh lookahead.
    """
    h1 = df_h1.copy()
    h1["ema_h1_fast"] = ema(h1["close"], config.EMA_TREND_FAST)
    h1["ema_h1_slow"] = ema(h1["close"], config.EMA_TREND_SLOW)
    h1["atr_h1"]      = atr(h1, config.ATR_PERIOD)
    h1["adx_h1"]      = adx(h1, config.ADX_PERIOD)
    h1["close_h1"]    = h1["close"]
    h1["closed_at"]   = h1["time"] + pd.Timedelta(hours=1)

    en = df_entry.copy()
    en["ema_pb_fast"] = ema(en["close"], config.EMA_PULLBACK_FAST)
    en["ema_pb_slow"] = ema(en["close"], config.EMA_PULLBACK_SLOW)
    en["atr_entry"]   = atr(en, config.ATR_PERIOD)
    en["rsi_entry"]   = rsi(en["close"], config.RSI_PERIOD)
    # Quyết định được đưa ra tại thời điểm đóng nến entry
    en["decision_time"] = en["time"] + pd.Timedelta(minutes=config.ENTRY_TF_MINUTES)

    merged = pd.merge_asof(
        en.sort_values("decision_time"),
        h1[["closed_at", "ema_h1_fast", "ema_h1_slow", "atr_h1", "adx_h1", "close_h1"]].sort_values("closed_at"),
        left_on="decision_time",
        right_on="closed_at",
        direction="backward",
        allow_exact_matches=True,
    ).reset_index(drop=True)

    # Trend H1 per bar: 1 = bullish, -1 = bearish, 0 = không trend
    bull = (merged["ema_h1_fast"] > merged["ema_h1_slow"]) & (merged["close_h1"] > merged["ema_h1_fast"])
    bear = (merged["ema_h1_fast"] < merged["ema_h1_slow"]) & (merged["close_h1"] < merged["ema_h1_fast"])

    # Lọc chất lượng trend (giống strategy/trend.py, dạng vectorized)
    strong = pd.Series(True, index=merged.index)
    if config.MIN_ADX_H1 > 0:
        strong &= merged["adx_h1"] >= config.MIN_ADX_H1
    if config.MIN_EMA_GAP_ATR > 0:
        strong &= (merged["ema_h1_fast"] - merged["ema_h1_slow"]).abs() >= config.MIN_EMA_GAP_ATR * merged["atr_h1"]

    merged["trend"] = np.where(bull & strong, 1, np.where(bear & strong, -1, 0))
    return merged


class ScalpStrategy(Strategy):
    ind: pd.DataFrame = None       # indicator frame, aligned theo vị trí với data
    raw: pd.DataFrame = None       # OHLCV gốc (lowercase) cho chiến lược price_action
    symbol: str = ""               # symbol đang backtest (cho session filter 24/7)
    trade_log: list = []
    fixed_lot: float = 0.0
    tick_size: float = 0.01
    tick_value: float = 1.0
    volume_min: float = 0.01
    volume_step: float = 0.01
    risk_percent: float = 0.01
    start_cash: float = 10_000.0
    digits: int = 2

    def init(self):
        ScalpStrategy.trade_log = []

    def _calc_lot(self, sl_distance: float) -> float:
        if ScalpStrategy.fixed_lot > 0:
            return ScalpStrategy.fixed_lot
        loss_per_lot = sl_distance / self.tick_size * self.tick_value
        if loss_per_lot <= 0:
            return self.volume_min
        lot = (self.start_cash * self.risk_percent) / loss_per_lot
        lot = max(self.volume_min, np.floor(lot / self.volume_step) * self.volume_step)
        return round(lot, 2)

    def next(self):
        i = len(self.data) - 1
        if i < 1 or self.position:
            return

        row = self.ind.iloc[i]
        if row[IND_COLS].isna().any():
            return  # chưa đủ warmup

        # Session filter (bar time server → UTC)
        bar_time = self.data.index[i].to_pydatetime()
        utc_time = bar_time - timedelta(hours=config.SERVER_UTC_OFFSET_HOURS)
        if not is_trading_session(utc_time, ScalpStrategy.symbol):
            return

        trend = int(row["trend"])
        if trend == 0:
            return

        cur = {
            "open":  float(self.data.Open[-1]),
            "high":  float(self.data.High[-1]),
            "low":   float(self.data.Low[-1]),
            "close": float(self.data.Close[-1]),
        }
        prev = {
            "open":  float(self.data.Open[-2]),
            "high":  float(self.data.High[-2]),
            "low":   float(self.data.Low[-2]),
            "close": float(self.data.Close[-2]),
        }

        entry = cur["close"]

        if config.STRATEGY == "price_action":
            start = max(0, i - config.ZONE_LOOKBACK + 1)
            window = ScalpStrategy.raw.iloc[start : i + 1]
            trend_str = "bullish" if trend == 1 else "bearish"
            res = price_action_signal(window, trend_str, float(row["atr_entry"]), self.digits)
            if not res:
                return
            direction, sl, candle_type = res["direction"], res["sl"], res["setup"]
        else:
            zone_top = max(row["ema_pb_fast"], row["ema_pb_slow"])
            zone_bot = min(row["ema_pb_fast"], row["ema_pb_slow"])

            if trend == 1:
                if not (cur["low"] <= zone_top and cur["close"] >= zone_bot):
                    return
                candle_type = bullish_confirmation(prev, cur)
                if not candle_type or candle_type not in config.CANDLE_TYPES:
                    return
                sl = round(cur["low"] - config.SL_ATR_MULT * row["atr_entry"], self.digits)
                direction = "BUY"
            else:
                if not (cur["high"] >= zone_bot and cur["close"] <= zone_top):
                    return
                candle_type = bearish_confirmation(prev, cur)
                if not candle_type or candle_type not in config.CANDLE_TYPES:
                    return
                sl = round(cur["high"] + config.SL_ATR_MULT * row["atr_entry"], self.digits)
                direction = "SELL"

        if config.RSI_FILTER:
            rsi_val = self.ind["rsi_entry"].iloc[i]
            if np.isnan(rsi_val):
                return
            if direction == "BUY" and rsi_val > config.RSI_BUY_MAX:
                return
            if direction == "SELL" and rsi_val < config.RSI_SELL_MIN:
                return

        sl_distance = abs(entry - sl)
        if sl_distance <= 0 or sl_distance > config.MAX_SL_ATR_H1_MULT * row["atr_h1"]:
            return  # SL quá xa so với ATR H1

        # Size theo lot thực để Return/Drawdown của backtesting.py khớp bảng USD.
        # 1 unit backtesting.py = $1 PnL / $1 price move → units/lot = tick_value/tick_size
        # (không dùng trade_contract_size — nhiều broker báo 1.0 kể cả với gold).
        # Nếu units < 1 (vd BTC lot nhỏ) thì để backtesting.py tự sizing, chỉ tin bảng USD.
        lot = self._calc_lot(sl_distance)

        # Chặn lệnh nếu lỗ dự kiến tại SL vượt ngưỡng USD (khớp logic live)
        if config.MAX_LOSS_PER_TRADE > 0:
            est_loss = sl_distance / self.tick_size * self.tick_value * lot
            if est_loss > config.MAX_LOSS_PER_TRADE:
                return

        units = int(round(lot * self.tick_value / self.tick_size))
        size_arg = {"size": units} if units >= 1 else {}

        if direction == "BUY":
            tp = round(entry + config.RR * sl_distance, self.digits)
            self.buy(sl=sl, tp=tp, **size_arg)
        else:
            tp = round(entry - config.RR * sl_distance, self.digits)
            self.sell(sl=sl, tp=tp, **size_arg)
        ScalpStrategy.trade_log.append({
            "time":        str(bar_time),
            "direction":   direction,
            "candle_type": candle_type,
            "entry":       round(entry, self.digits),
            "sl":          sl,
            "tp":          tp,
            "lot":         lot,
        })


def _fmt_duration(td) -> str:
    """Timedelta → chuỗi gọn: '45m', '3h20m', '1d05h'."""
    try:
        total_min = int(td.total_seconds() // 60)
    except (AttributeError, ValueError):
        return "?"
    d, rem = divmod(total_min, 1440)
    h, m = divmod(rem, 60)
    if d:
        return f"{d}d{h:02d}h"
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"


def real_pnl_usd(trade_row, log: dict) -> float:
    """PnL USD thực theo lot: (exit − entry) / tick_size × tick_value × lot."""
    lot = log.get("lot")
    if not isinstance(lot, (int, float)) or lot <= 0:
        return float(trade_row["PnL"])
    dir_sign = 1 if trade_row["Size"] > 0 else -1
    price_move = (trade_row["ExitPrice"] - trade_row["EntryPrice"]) * dir_sign
    return price_move / ScalpStrategy.tick_size * ScalpStrategy.tick_value * lot


def print_summary(symbol: str, stats, trade_log: list) -> list:
    """In tổng kết ra console, trả về các dòng text để ghi file."""
    trades = stats._trades

    lines = []
    filters = []
    if config.MIN_ADX_H1 > 0:
        filters.append(f"ADX>={config.MIN_ADX_H1:g}")
    if config.MIN_EMA_GAP_ATR > 0:
        filters.append(f"gapEMA>={config.MIN_EMA_GAP_ATR:g}xATR")
    filter_str = f" | Lọc: {', '.join(filters)}" if filters else ""

    strat_desc = ("Price Action (zone + patterns)" if config.STRATEGY == "price_action"
                  else "EMA Trend H1 + Pullback")
    lines.append("=" * 60)
    lines.append(f"  BACKTEST SUMMARY — Scalp Bot ({symbol})")
    lines.append(f"  {strat_desc} {config.ENTRY_TF} | RR 1:{config.RR:g}{filter_str}")
    lines.append("=" * 60)
    lines.append(f"  Period        : {stats['Start']} → {stats['End']}")
    lines.append(f"  Total trades  : {stats['# Trades']}")
    if stats['# Trades'] > 0:
        lines.append(f"  Win rate      : {stats['Win Rate [%]']:.1f}%")
        lines.append(f"  Return        : {stats['Return [%]']:.2f}%")
        lines.append(f"  Max drawdown  : {stats['Max. Drawdown [%]']:.2f}%")
        lines.append(f"  Sharpe ratio  : {stats['Sharpe Ratio']:.2f}")
        lines.append(f"  Best trade    : {stats['Best Trade [%]']:.2f}%")
        lines.append(f"  Worst trade   : {stats['Worst Trade [%]']:.2f}%")
        lines.append(f"  Avg trade     : {stats['Avg. Trade [%]']:.2f}%")
        lines.append(f"  Giữ lệnh TB   : {_fmt_duration(stats['Avg. Trade Duration'])}")
        lines.append(f"  Giữ lệnh max  : {_fmt_duration(stats['Max. Trade Duration'])}")
    lines.append("=" * 60)

    if trades.empty:
        lines.append("  No trades found.")
        print("\n".join(lines))
        return lines

    real_pnls = [
        real_pnl_usd(row, trade_log[i] if i < len(trade_log) else {})
        for i, (_, row) in enumerate(trades.iterrows())
    ]
    win_pnls  = [p for p, (_, row) in zip(real_pnls, trades.iterrows()) if row["PnL"] > 0]
    loss_pnls = [p for p, (_, row) in zip(real_pnls, trades.iterrows()) if row["PnL"] <= 0]

    total_usd = sum(real_pnls)
    win_usd   = sum(win_pnls)
    loss_usd  = sum(loss_pnls)

    lines.append(f"\n  Wins   : {len(win_pnls)}  |  Losses: {len(loss_pnls)}")
    if win_pnls:
        lines.append(f"  Avg win  : ${sum(win_pnls) / len(win_pnls):.2f}")
    if loss_pnls:
        lines.append(f"  Avg loss : ${sum(loss_pnls) / len(loss_pnls):.2f}")
    if "Duration" in trades.columns:
        win_dur  = trades.loc[trades["PnL"] > 0, "Duration"]
        loss_dur = trades.loc[trades["PnL"] <= 0, "Duration"]
        if len(win_dur):
            lines.append(f"  Giữ lệnh thắng TB : {_fmt_duration(win_dur.mean())}")
        if len(loss_dur):
            lines.append(f"  Giữ lệnh thua TB  : {_fmt_duration(loss_dur.mean())}")

    sign = "+" if total_usd >= 0 else ""
    lines.append(f"\n  ── Tổng kết USD ────────────────────────────")
    lines.append(f"  Tổng thắng  : +${win_usd:.2f}")
    lines.append(f"  Tổng thua   :  -${abs(loss_usd):.2f}")
    lines.append(f"  NET P&L     : {sign}${total_usd:.2f}  {'✅ PROFIT' if total_usd >= 0 else '❌ LOSS'}")
    lines.append(f"  ─────────────────────────────────────────────")

    # Thống kê theo setup / loại nến
    by_setup = {}
    for i, (_, row) in enumerate(trades.iterrows()):
        log = trade_log[i] if i < len(trade_log) else {}
        s = log.get("candle_type", "?")
        d = by_setup.setdefault(s, {"n": 0, "w": 0, "usd": 0.0})
        d["n"]   += 1
        d["w"]   += 1 if row["PnL"] > 0 else 0
        d["usd"] += real_pnls[i]
    if len(by_setup) >= 1:
        lines.append(f"\n  ── Theo setup ──────────────────────────────")
        for s, d in sorted(by_setup.items(), key=lambda kv: -kv[1]["usd"]):
            wr = 100.0 * d["w"] / d["n"] if d["n"] else 0.0
            lines.append(f"  {s:<15} {d['n']:>4} lệnh | wr {wr:5.1f}% | {d['usd']:+10.2f} USD")
        lines.append(f"  ─────────────────────────────────────────────")

    lines.append(f"\n  {'#':<4} {'Entry Time':<20} {'Dir':<5} {'Nến':<10} {'Vol':>6} {'Entry':>10} {'SL':>10} {'TP':>10} {'PnL':>10} {'Giữ lệnh':>9}  Result")
    lines.append(f"  {'-'*105}")
    for i, (_, row) in enumerate(trades.iterrows()):
        log       = trade_log[i] if i < len(trade_log) else {}
        pnl       = real_pnls[i]
        result    = "✅ WIN " if row["PnL"] > 0 else "❌ LOSS"
        time_str  = str(row.get("EntryTime", ""))[:16]
        dur_str   = _fmt_duration(row.get("Duration"))
        lines.append(
            f"  {i+1:<4} {time_str:<20} {log.get('direction', '?'):<5} {log.get('candle_type', '?'):<10} "
            f"{log.get('lot', '?')!s:>6} {log.get('entry', 0):>10} {log.get('sl', '?')!s:>10} "
            f"{log.get('tp', '?')!s:>10} {pnl:>10.2f} {dur_str:>9}  {result}"
        )

    lines.append("=" * 60)
    print("\n".join(lines))
    return lines


def save_summary(all_lines: list):
    filename = f"backtest_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    output_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), filename)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(all_lines))
    print(f"\n  Summary saved → {output_path}")


def run_backtest_for_symbol(symbol: str, months: int, cash: float) -> list:
    """Run backtest for a single symbol. MT5 must already be connected. Trả về lines cho summary file."""
    sym_info = mt5.symbol_info(symbol)
    if sym_info is None:
        logger.error(f"Symbol {symbol} không tồn tại trên broker — bỏ qua (kiểm tra suffix)")
        return [f"  {symbol}: symbol không tồn tại — skipped"]

    df_h1, df_entry = fetch_backtest_data(symbol, months)
    if df_entry is None or df_h1 is None:
        logger.error(f"Failed to fetch data for {symbol}")
        return [f"  {symbol}: fetch data failed — skipped"]

    logger.info(f"{symbol} — H1: {len(df_h1)} | {config.ENTRY_TF}: {len(df_entry)} candles")

    ind = build_indicator_frame(df_entry, df_h1)

    df_bt = df_entry.rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
    }).set_index("time")

    ScalpStrategy.ind          = ind
    ScalpStrategy.raw          = df_entry.reset_index(drop=True)
    ScalpStrategy.symbol       = symbol
    ScalpStrategy.fixed_lot    = config.get_symbol_lot(symbol)
    ScalpStrategy.tick_size    = sym_info.trade_tick_size or sym_info.point
    ScalpStrategy.tick_value   = sym_info.trade_tick_value or 1.0
    ScalpStrategy.volume_min   = sym_info.volume_min or 0.01
    ScalpStrategy.volume_step  = sym_info.volume_step or 0.01
    ScalpStrategy.risk_percent = config.RISK_PERCENT
    ScalpStrategy.start_cash   = cash
    ScalpStrategy.digits       = sym_info.digits

    if ScalpStrategy.fixed_lot > 0:
        logger.info(f"{symbol} — fixed lot: {ScalpStrategy.fixed_lot}")
    else:
        logger.info(f"{symbol} — lot theo risk {config.RISK_PERCENT*100:.1f}%/lệnh trên cash {cash:.0f}")

    logger.info(f"Running backtest for {symbol} | cash={cash:.0f} | leverage={config.LEVERAGE}")
    bt = Backtest(
        df_bt, ScalpStrategy,
        cash=cash,
        commission=0.0002,
        margin=1 / config.LEVERAGE,
        exclusive_orders=True,
    )
    stats = bt.run()

    print(f"\n{'#'*60}")
    print(f"  SYMBOL: {symbol}")
    print(f"{'#'*60}")
    lines = print_summary(symbol, stats, ScalpStrategy.trade_log)

    if os.getenv("SKIP_PLOT"):
        return lines  # optimizer đặt SKIP_PLOT=1 để tiết kiệm RAM

    html_file = f"backtest_result_{symbol}.html"
    try:
        bt.plot(filename=html_file, open_browser=False)
        print(f"  Chart → {html_file}")
    except Exception as e:
        logger.warning(f"Chart generation skipped: {e}")

    return lines


def run_backtest(months: int, cash: float, symbol: str = None):
    if not connect():
        logger.error("MT5 connection failed")
        return

    if cash <= 0:
        cash = get_account_balance()
        logger.info(f"Using real account balance: {cash:.2f}")

    symbols = [symbol] if symbol else config.SYMBOLS
    logger.info(f"Backtesting symbols: {symbols}")

    all_lines = []
    try:
        for sym in symbols:
            all_lines += run_backtest_for_symbol(sym, months, cash)
            all_lines.append("")
    finally:
        disconnect()

    if all_lines:
        save_summary(all_lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=3, help="Số tháng lịch sử để backtest")
    parser.add_argument("--cash",   type=float, default=0, help="Vốn ban đầu (0 = dùng balance thật từ MT5)")
    parser.add_argument("--symbol", type=str, default=None, help="Chỉ backtest 1 symbol (mặc định: tất cả trong SYMBOLS)")
    args = parser.parse_args()
    run_backtest(args.months, args.cash, args.symbol)
