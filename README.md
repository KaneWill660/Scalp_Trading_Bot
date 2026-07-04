# Scalp Trading Bot — MT5 (Python)

Bot scalping MT5 với 2 chiến lược chuyển đổi qua `STRATEGY=` trong `.env`:

## Chiến lược 1 — `ema_pullback` (mặc định)

1. **Trend H1**: EMA50 > EMA200 và Close > EMA50 → chỉ Buy; EMA50 < EMA200 và Close < EMA50 → chỉ Sell
2. **Entry** (khung `ENTRY_TF`): giá hồi về vùng EMA20–EMA50, xuất hiện nến xác nhận (engulfing hoặc pin bar) đóng cửa cùng chiều trend
3. **SL**: đáy/đỉnh nến xác nhận ± đệm 1.5×ATR(14) khung entry
4. **TP**: RR cố định 1:2 (config qua `RR` trong `.env`)
5. **Lọc**: chỉ trade phiên London/NY (07:00–15:30 UTC); bỏ tín hiệu nếu khoảng SL > `MAX_SL_ATR_H1_MULT`×ATR(14) H1

## Chiến lược 2 — `price_action` (zone + 8 setup)

Trend H1 khóa hướng → tín hiệu tại Supply/Demand zone hoặc mô hình giá → chờ **nến đóng cửa xác nhận** → SL trên/dưới cấu trúc + đệm `ZONE_SL_BUFFER_ATR`×ATR → TP theo RR.

Setup bật/tắt qua `PATTERNS=` (thứ tự = độ ưu tiên):
- Tại zone: `engulfing`, `hammer` (búa ngược), `harami` (mẹ bồng con), `double_doji`, `pinbar`, `double_hammer`
- Mô hình giá: `flag` (cờ tăng/giảm), `triangle` (tam giác đối xứng)

Supply zone = swing high có nhịp rơi ≥ `IMPULSE_ATR_MULT`×ATR sau đó, vô hiệu khi có nến đóng vượt đỉnh (Demand đối xứng). Backtest in bảng thống kê **per-setup** để đánh giá từng mẫu.

## Cài đặt

```bash
pip install -r requirements.txt
copy .env.example .env
# Điền MT5_LOGIN / MT5_PASSWORD / MT5_SERVER và TELEGRAM_* vào .env
```

Yêu cầu: MetaTrader 5 terminal đã cài và đang mở (cả backtest lẫn live đều lấy data/đặt lệnh qua terminal).

## Backtest

```bash
# Tất cả symbols trong .env, 3 tháng gần nhất
python -m tests.backtest --months 3

# 1 symbol, vốn tùy chọn
python -m tests.backtest --months 6 --symbol XAUUSDc --cash 10000
```

Kết quả:
- Console + file `backtest_summary_<timestamp>.txt`: winrate, drawdown, PnL USD theo lot thực, danh sách từng lệnh
- `backtest_result_<symbol>.html`: chart mở bằng browser

Lưu ý: data MT5 trả về theo **giờ server broker**. Nếu server không phải UTC+0, set `SERVER_UTC_OFFSET_HOURS` trong `.env` để session filter backtest đúng giờ (live bot luôn dùng UTC thật nên không ảnh hưởng).

## Chạy live

```bash
python main.py
```

- Quét tín hiệu mỗi 60 giây trong phiên, mỗi symbol tối đa 1 vị thế mở
- 1 lệnh/tín hiệu với TP RR 1:2, tự động breakeven khi lãi đạt 1:1
- Circuit breaker: dừng trade trong ngày nếu lỗ vượt 3% balance
- Cooldown 15 phút/symbol sau mỗi tín hiệu

### Lệnh Telegram

`/balance` `/status` `/pending` `/report N` `/stop` `/start` `/be` `/closeall` `/risk 0.5` `/pause 30m` `/help`

## Cấu trúc

```
config.py                 # toàn bộ tham số chiến lược (đọc .env)
main.py                   # vòng lặp live
connectors/mt5_connector.py
strategy/
  indicators.py           # EMA, ATR, ADX
  candles.py              # engulfing, pin bar
  trend.py                # trend H1 (EMA50/200 + lọc ADX/gap)
  session.py              # lọc phiên London/NY
  zones.py                # Supply/Demand zone detection
  patterns.py             # 6 setup nến tại zone
  chart_patterns.py       # flag, triangle
  price_action.py         # engine chiến lược price_action
  entry_manager.py        # dispatch chiến lược, tổng hợp tín hiệu
risk/risk_manager.py      # lot size generic theo tick value, circuit breaker
notifications/            # Telegram notifier + commands
tests/backtest.py         # backtest trên data MT5 (backtesting.py), thống kê per-setup
```

## Lot sizing

- Mặc định: risk `RISK_PERCENT` (1%) mỗi lệnh — lot tính generic cho mọi symbol từ `tick_size`/`tick_value` của broker
- Override lot cố định per-symbol: `SYMBOL_LOT_XAUUSDc=0.02` trong `.env`
