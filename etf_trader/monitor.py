"""
Hourly price monitor for 3 Best ETF Paper Trading.
Checks open positions against Take Profit (+10%) and Stop Loss (-10%).
Runs via GitHub Actions every hour during NYSE market hours (Mon-Fri).
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import yfinance as yf
from etf_trader.engine import (
    TAKE_PROFIT_PCT, STOP_LOSS_PCT,
    load_state, save_state, close_position,
)


def get_current_price(ticker: str) -> float | None:
    try:
        info  = yf.Ticker(ticker).fast_info
        price = info.get("last_price") or info.get("previous_close")
        return float(price) if price else None
    except Exception:
        return None


def run_monitor():
    state = load_state()

    if not state["positions"]:
        print("No open ETF positions to monitor.")
        return False

    print(f"Monitoring {len(state['positions'])} open position(s)...")
    changed = False

    for pos in list(state["positions"]):
        ticker      = pos["ticker"]
        entry_price = pos["entry_price"]
        name        = pos.get("name", ticker)

        price = get_current_price(ticker)
        if price is None:
            print(f"  {ticker}: could not fetch price, skipping")
            continue

        pnl_pct = (price / entry_price - 1) * 100
        print(f"  {ticker} ({name}): entry=${entry_price:.2f}  now=${price:.2f}  P&L={pnl_pct:+.1f}%")

        if pnl_pct >= TAKE_PROFIT_PCT * 100:
            close_position(state, ticker, price, "take_profit")
            changed = True
        elif pnl_pct <= STOP_LOSS_PCT * 100:
            close_position(state, ticker, price, "stop_loss")
            changed = True

    if changed:
        save_state(state)
        print("State saved.")
    else:
        print("No positions triggered. State unchanged.")

    return changed


if __name__ == "__main__":
    run_monitor()
