"""
State management and core trading logic for 3 Best ETF Paper Trading.
Same rules as stock/crypto systems — separate state file.
"""

import json
from datetime import date
from pathlib import Path

STATE_FILE       = Path(__file__).parent / "state.json"
COST_PER_TRADE   = 100.0
MAX_POSITIONS    = 3
TAKE_PROFIT_PCT  = 0.10   # +10%
STOP_LOSS_PCT    = -0.10  # -10%
SCORE_THRESHOLD  = 70     # between stocks (75) and crypto (65)


def load_state() -> dict:
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        data.setdefault("positions", [])
        data.setdefault("trades", [])
        return data
    return {"positions": [], "trades": []}


def save_state(state: dict):
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def open_position(state: dict, ticker: str, name: str, price: float, score: int) -> bool:
    held = {p["ticker"] for p in state["positions"]}
    if len(state["positions"]) >= MAX_POSITIONS:
        print(f"  SKIP {ticker} — portfolio full ({MAX_POSITIONS} positions)")
        return False
    if ticker in held:
        print(f"  SKIP {ticker} — already held")
        return False

    quantity = round(COST_PER_TRADE / price, 6)
    state["positions"].append({
        "ticker":      ticker,
        "name":        name,
        "entry_price": round(price, 4),
        "entry_date":  date.today().isoformat(),
        "entry_score": score,
        "quantity":    quantity,
        "cost":        COST_PER_TRADE,
    })
    print(f"  BUY  {ticker} ({name}) @ ${price:.2f}  qty={quantity:.4f}  score={score}")
    return True


def close_position(state: dict, ticker: str, exit_price: float, reason: str) -> dict | None:
    pos = next((p for p in state["positions"] if p["ticker"] == ticker), None)
    if not pos:
        return None

    pnl     = round((exit_price - pos["entry_price"]) * pos["quantity"], 4)
    pnl_pct = round((exit_price / pos["entry_price"] - 1) * 100, 2)

    trade = {
        "ticker":      ticker,
        "name":        pos.get("name", ticker),
        "entry_price": pos["entry_price"],
        "exit_price":  round(exit_price, 4),
        "entry_date":  pos["entry_date"],
        "exit_date":   date.today().isoformat(),
        "entry_score": pos.get("entry_score", 0),
        "quantity":    pos["quantity"],
        "cost":        pos["cost"],
        "pnl":         pnl,
        "pnl_pct":     pnl_pct,
        "exit_reason": reason,
    }
    state["trades"].append(trade)
    state["positions"] = [p for p in state["positions"] if p["ticker"] != ticker]

    emoji = "✅" if pnl >= 0 else "❌"
    print(f"  SELL {ticker} @ ${exit_price:.2f}  P&L={pnl_pct:+.1f}%  [{reason}] {emoji}")
    return trade


def portfolio_stats(state: dict) -> dict:
    trades = state["trades"]
    if not trades:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
            "total_pnl": 0, "avg_win": 0, "avg_loss": 0,
            "best_trade": None, "worst_trade": None,
        }

    wins   = [t for t in trades if t["pnl"] >= 0]
    losses = [t for t in trades if t["pnl"] < 0]
    total  = sum(t["pnl"] for t in trades)

    return {
        "total_trades": len(trades),
        "wins":         len(wins),
        "losses":       len(losses),
        "win_rate":     round(len(wins) / len(trades) * 100, 1),
        "total_pnl":    round(total, 2),
        "avg_win":      round(sum(t["pnl_pct"] for t in wins)   / len(wins),   1) if wins   else 0,
        "avg_loss":     round(sum(t["pnl_pct"] for t in losses) / len(losses), 1) if losses else 0,
        "best_trade":   max(trades, key=lambda t: t["pnl_pct"]),
        "worst_trade":  min(trades, key=lambda t: t["pnl_pct"]),
    }
