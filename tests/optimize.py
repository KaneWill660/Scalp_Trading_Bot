# -*- coding: utf-8 -*-
"""
Vòng lặp tối ưu tự động — chạy backtest qua lưới cấu hình cho đến khi đạt mục tiêu:
  - Winrate >= --target-wr (mặc định 60%)
  - Tần suất  >= 1 lệnh / --days-per-trade ngày (mặc định 2)
  - NET P&L > 0 (winrate cao mà lỗ thì vô nghĩa — tắt bằng --allow-loss)

Kết quả từng run ghi vào optimize_results.csv; kết thúc in top cấu hình
gần mục tiêu nhất (kể cả khi không config nào đạt tuyệt đối).

Usage:
  python -m tests.optimize --months 12 --symbol XAUUSDc --cash 10000
"""

import argparse
import csv
import os
import re
import subprocess
import sys
from datetime import datetime

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RE = {
    "period":  re.compile(r"Period\s*:\s*([\d\- :]+?)\s*→\s*([\d\- :]+)"),
    "trades":  re.compile(r"Total trades\s*:\s*(\d+)"),
    "winrate": re.compile(r"Win rate\s*:\s*([\d.]+)%"),
    "dd":      re.compile(r"Max drawdown\s*:\s*(-?[\d.]+)%"),
    "net":     re.compile(r"NET P&L\s*:\s*([+-]?)\$(-?[\d.,]+)"),
}


def build_grid() -> list:
    """Lưới cấu hình, xếp theo độ hứa hẹn về winrate (M30 + RR thấp trước)."""
    combos = []
    # EMA pullback: TF × RR × lọc SL × RSI filter
    for tf in ("M30", "M15", "M5"):
        for rr in ("1.0", "1.5", "2.0"):
            for max_sl in ("1.5", "2.0"):
                for rsi_f in ("0", "1"):
                    combos.append({
                        "STRATEGY": "ema_pullback", "ENTRY_TF": tf, "RR": rr,
                        "MAX_SL_ATR_H1_MULT": max_sl, "RSI_FILTER": rsi_f,
                    })
    # Price action: các setup đã chứng minh có edge
    for tf in ("M30", "M15"):
        for pats in ("flag,engulfing,triangle,hammer", "flag", "flag,triangle"):
            for rr in ("1.0", "2.0"):
                combos.append({
                    "STRATEGY": "price_action", "ENTRY_TF": tf, "PATTERNS": pats,
                    "RR": rr, "MAX_SL_ATR_H1_MULT": "1.5", "RSI_FILTER": "0",
                })
    return combos


def describe(combo: dict) -> str:
    parts = [combo["STRATEGY"].replace("ema_pullback", "EMA").replace("price_action", "PA"),
             combo["ENTRY_TF"], f"RR{combo['RR']}", f"SLf{combo['MAX_SL_ATR_H1_MULT']}"]
    if combo.get("RSI_FILTER") == "1":
        parts.append("RSI")
    if combo.get("PATTERNS"):
        parts.append(combo["PATTERNS"].replace(",", "+"))
    return " ".join(parts)


def run_one(combo: dict, months: int, symbol: str, cash: float) -> "dict | None":
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "RISK_PERCENT": "0.01", "SKIP_PLOT": "1"})
    env.update(combo)
    proc = subprocess.run(
        [sys.executable, "-m", "tests.backtest", "--months", str(months),
         "--symbol", symbol, "--cash", str(cash)],
        cwd=PROJECT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=1800,
    )
    out = proc.stdout.decode("utf-8", errors="replace")

    m_tr = RE["trades"].search(out)
    if not m_tr:
        return None
    result = {"trades": int(m_tr.group(1)), "winrate": 0.0, "dd": 0.0, "net": 0.0, "days": 0.0}

    m = RE["winrate"].search(out)
    if m:
        result["winrate"] = float(m.group(1))
    m = RE["dd"].search(out)
    if m:
        result["dd"] = float(m.group(1))
    m = RE["net"].search(out)
    if m:
        val = float(m.group(2).replace(",", ""))
        result["net"] = -val if m.group(1) == "-" else val
    m = RE["period"].search(out)
    if m:
        try:
            t0 = datetime.strptime(m.group(1).strip(), "%Y-%m-%d %H:%M:%S")
            t1 = datetime.strptime(m.group(2).strip(), "%Y-%m-%d %H:%M:%S")
            result["days"] = max((t1 - t0).total_seconds() / 86400.0, 1.0)
        except ValueError:
            pass
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--symbol", type=str, default="XAUUSDc")
    ap.add_argument("--cash", type=float, default=10_000)
    ap.add_argument("--target-wr", type=float, default=60.0, help="Winrate mục tiêu (%%)")
    ap.add_argument("--days-per-trade", type=float, default=2.0, help="Tối đa N ngày / 1 lệnh")
    ap.add_argument("--allow-loss", action="store_true", help="Chấp nhận config đạt WR+tần suất nhưng NET âm")
    ap.add_argument("--max-runs", type=int, default=0, help="Giới hạn số run (0 = chạy hết lưới)")
    args = ap.parse_args()

    grid = build_grid()
    if args.max_runs > 0:
        grid = grid[: args.max_runs]

    csv_path = os.path.join(PROJECT, "optimize_results.csv")
    rows = []
    winner = None

    print(f"Bắt đầu tối ưu: {len(grid)} cấu hình | mục tiêu WR>={args.target_wr}% "
          f"và >=1 lệnh/{args.days_per_trade:g} ngày | {args.symbol} {args.months} tháng\n", flush=True)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["config", "trades", "winrate_pct", "net_usd", "maxdd_pct",
                         "days", "days_per_trade", "meets_wr", "meets_freq", "profitable"])

        for k, combo in enumerate(grid, 1):
            name = describe(combo)
            try:
                r = run_one(combo, args.months, args.symbol, args.cash)
            except subprocess.TimeoutExpired:
                print(f"[{k}/{len(grid)}] {name} — TIMEOUT, bỏ qua", flush=True)
                continue
            if r is None:
                print(f"[{k}/{len(grid)}] {name} — không parse được kết quả, bỏ qua", flush=True)
                continue

            dpt = (r["days"] / r["trades"]) if r["trades"] else float("inf")
            meets_wr   = r["winrate"] >= args.target_wr and r["trades"] >= 10
            meets_freq = dpt <= args.days_per_trade
            profitable = r["net"] > 0

            row = dict(combo=name, **r, dpt=dpt,
                       meets_wr=meets_wr, meets_freq=meets_freq, profitable=profitable)
            rows.append(row)
            writer.writerow([name, r["trades"], r["winrate"], round(r["net"], 2), r["dd"],
                             round(r["days"], 1), round(dpt, 2), meets_wr, meets_freq, profitable])
            f.flush()

            status = "🎯 ĐẠT MỤC TIÊU!" if (meets_wr and meets_freq and (profitable or args.allow_loss)) else ""
            print(f"[{k}/{len(grid)}] {name:<45} trades={r['trades']:>4} "
                  f"wr={r['winrate']:5.1f}% net={r['net']:+9.2f} "
                  f"({dpt:5.1f} ngày/lệnh) {status}", flush=True)

            if meets_wr and meets_freq and (profitable or args.allow_loss):
                winner = row
                break

    print("\n" + "=" * 80)
    if winner:
        print(f"🎯 TÌM THẤY CẤU HÌNH ĐẠT MỤC TIÊU: {winner['combo']}")
        print(f"   {winner['trades']} lệnh | winrate {winner['winrate']:.1f}% | "
              f"NET {winner['net']:+.2f} USD | {winner['dpt']:.1f} ngày/lệnh")
    else:
        print("KHÔNG cấu hình nào đạt đồng thời cả 2 mục tiêu. Top gần nhất:")
        print("\n  -- Top 5 theo winrate (>=10 lệnh) --")
        for r in sorted([x for x in rows if x["trades"] >= 10],
                        key=lambda x: -x["winrate"])[:5]:
            print(f"  {r['combo']:<45} wr={r['winrate']:5.1f}% trades={r['trades']:>4} "
                  f"net={r['net']:+9.2f} ({r['dpt']:.1f} ngày/lệnh)")
        print("\n  -- Top 5 theo tần suất trong nhóm có lãi --")
        for r in sorted([x for x in rows if x["profitable"]],
                        key=lambda x: x["dpt"])[:5]:
            print(f"  {r['combo']:<45} wr={r['winrate']:5.1f}% trades={r['trades']:>4} "
                  f"net={r['net']:+9.2f} ({r['dpt']:.1f} ngày/lệnh)")
    print(f"\nToàn bộ kết quả: {csv_path}")


if __name__ == "__main__":
    main()
