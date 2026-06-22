"""
Evening scan for New Tactic 3 Best Stocks Paper Trading.
Uses improved entry signals: reversal confirmation + MACD cross + market regime.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from new_tactic_trader.engine import (
    MAX_POSITIONS, SCORE_THRESHOLD,
    load_state, save_state, open_position,
)
from new_tactic_trader.screener import get_candidates


def run_scan() -> list[dict]:
    state = load_state()

    open_slots = MAX_POSITIONS - len(state["positions"])
    if open_slots <= 0:
        print(f"Portfolio full ({MAX_POSITIONS}/{MAX_POSITIONS} positions). No scan needed.")
        return []

    held = [p["ticker"] for p in state["positions"]]
    print(f"[New Tactic] Open slots: {open_slots}  |  Currently held: {held or 'none'}")

    candidates = get_candidates(exclude_tickers=held)

    if not candidates:
        print(f"No stocks found with score >= {SCORE_THRESHOLD}.")
        save_state(state)
        return []

    print(f"\nTop candidates (score >= {SCORE_THRESHOLD}):")
    for s in candidates[:5]:
        reversal = "✅ REVERSAL" if s["today_is_green"] and s["reds_before_reversal"] >= 1 else "⏳ NO REVERSAL"
        macd     = f"MACD✅ ({s['macd_cross_days_ago']}d ago)" if s["macd_cross_days_ago"] is not None else "MACD—"
        regime   = "🟢BULL" if s["bull_regime"] else "🔴BEAR"
        print(
            f"  {s['ticker']:6s}  score={s['score']}  RSI={s['rsi']}  "
            f"{reversal}  {macd}  {regime}  sector={s.get('sector','—')}"
        )

    new_positions = []
    for candidate in candidates:
        if len(state["positions"]) >= MAX_POSITIONS:
            break
        bought = open_position(
            state,
            ticker=candidate["ticker"],
            price=candidate["price"],
            score=candidate["score"],
        )
        if bought:
            new_positions.append(candidate)

    save_state(state)
    print(f"\nScan complete. Opened {len(new_positions)} new position(s).")
    return new_positions


if __name__ == "__main__":
    run_scan()
