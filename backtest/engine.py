"""
Vectorised backtest engine.

Given pre-downloaded OHLCV DataFrames, computes all technical signals once
for every ticker/date, then simulates each strategy day-by-day (monitor →
scan) using only data available at that point in time.

Deliberate simplifications:
- Fills at daily closing price (not intraday TP/SL)
- Fundamentals (P/E, ROE …) use today's live data — noted in the report
- Crypto market-cap ranks are static (current CoinGecko data)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class Trade:
    ticker:       str
    entry_date:   str
    exit_date:    str
    entry_price:  float
    exit_price:   float
    pnl:          float
    pnl_pct:      float
    exit_reason:  str
    entry_score:  int
    holding_days: int


@dataclass
class SimResult:
    strategy_name: str
    trades: list[Trade]          = field(default_factory=list)
    # (date_str, cumulative_pnl_usd)
    equity: list[tuple[str, float]] = field(default_factory=list)


# ── Signal pre-computation ────────────────────────────────────────────────────

def precompute_signals(
    close_df:   pd.DataFrame,
    volume_df:  pd.DataFrame,
    spy_close:  pd.Series,
) -> dict[str, pd.DataFrame]:
    """
    Returns {ticker: DataFrame} with columns:
        price, rsi, macd_cross, vol_ratio,
        consec_red, today_green, reds_before,
        chg7d, ath_pct, bull_regime
    Computed causally — no look-ahead bias in technical signals.
    """
    spy_ma50   = spy_close.rolling(50, min_periods=50).mean()
    bull_global = (spy_close > spy_ma50)

    result: dict[str, pd.DataFrame] = {}

    for ticker in close_df.columns:
        if ticker == "SPY":
            continue
        prices = close_df[ticker].dropna()
        if len(prices) < 40:
            continue

        vols = (
            volume_df[ticker].reindex(prices.index).fillna(0)
            if ticker in volume_df.columns
            else pd.Series(0.0, index=prices.index)
        )

        # RSI (EWM, causal)
        delta = prices.diff()
        ag    = delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
        al    = (-delta.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
        rsi   = 100 - 100 / (1 + ag / al.replace(0, 1e-9))

        # MACD bullish cross within last 5 sessions
        macd    = prices.ewm(span=12, adjust=False).mean() - prices.ewm(span=26, adjust=False).mean()
        msig    = macd.ewm(span=9, adjust=False).mean()
        above   = macd > msig
        crossed = above & ~above.shift(1).fillna(False)
        macd_cross = crossed.rolling(5, min_periods=1).max().astype(bool)

        # Volume ratio vs 20-day average
        vol_ma    = vols.rolling(20, min_periods=5).mean().replace(0, np.nan)
        vol_ratio = (vols / vol_ma).fillna(1.0).clip(upper=10.0)

        # Consecutive red-day streak (vectorised)
        returns = prices.pct_change()
        is_red  = (returns < 0).astype(int)
        grp     = (is_red != is_red.shift()).cumsum()
        consec_red  = (is_red.groupby(grp).cumsum() * is_red)
        today_green = returns > 0
        reds_before = consec_red.shift(1).fillna(0).astype(int)

        # 7-day return
        chg7d = prices.pct_change(7) * 100

        # Distance from rolling 2-year high (ATH proxy)
        ath_px  = prices.rolling(504, min_periods=126).max()
        ath_pct = (prices / ath_px - 1) * 100

        # SPY regime aligned to this ticker's trading calendar
        bull = bull_global.reindex(prices.index, method="ffill").fillna(True)

        result[ticker] = pd.DataFrame({
            "price":       prices,
            "rsi":         rsi,
            "macd_cross":  macd_cross,
            "vol_ratio":   vol_ratio,
            "consec_red":  consec_red,
            "today_green": today_green,
            "reds_before": reds_before,
            "chg7d":       chg7d,
            "ath_pct":     ath_pct,
            "bull":        bull,
        })

    return result


# ── Simulation ────────────────────────────────────────────────────────────────

def simulate(
    strategy_name: str,
    universe:      list[str],
    signals:       dict[str, pd.DataFrame],
    fund_data:     dict[str, dict],
    score_fn,                       # (row: dict, fund: dict) -> int
    threshold:     int,
    start_date:    str,             # "YYYY-MM-DD" — first date to trade
    take_profit:   float = 0.10,
    stop_loss:     float = 0.10,    # stored positive, applied as negative
    max_pos:       int   = 3,
    cost:          float = 100.0,
) -> SimResult:
    """Day-by-day backtest: monitor (TP/SL exits) then scan (entries)."""
    res = SimResult(strategy_name=strategy_name)

    # Build sorted list of trading days across the universe
    all_dates: set = set()
    for t in universe:
        if t in signals:
            all_dates.update(signals[t].index.tolist())
    trading_days = sorted(d for d in all_dates if str(d.date()) >= start_date)

    positions: list[dict] = []
    cum_pnl = 0.0

    for day in trading_days:
        day_str = str(day.date())

        # ── Monitor ──────────────────────────────────────────────────────────
        for pos in list(positions):
            tkr = pos["ticker"]
            if tkr not in signals or day not in signals[tkr].index:
                continue
            price = float(signals[tkr].at[day, "price"])
            if math.isnan(price):
                continue
            pnl_pct = (price / pos["entry_price"] - 1) * 100

            if pnl_pct >= take_profit * 100:
                reason = "take_profit"
            elif pnl_pct <= -stop_loss * 100:
                reason = "stop_loss"
            else:
                continue

            qty     = pos["quantity"]
            pnl     = round((price - pos["entry_price"]) * qty, 4)
            cum_pnl += pnl
            res.trades.append(Trade(
                ticker      = tkr,
                entry_date  = pos["entry_date"],
                exit_date   = day_str,
                entry_price = pos["entry_price"],
                exit_price  = round(price, 6),
                pnl         = pnl,
                pnl_pct     = round(pnl_pct, 2),
                exit_reason = reason,
                entry_score = pos["score"],
                holding_days= (day.date() - pd.Timestamp(pos["entry_date"]).date()).days,
            ))
            positions = [p for p in positions if p["ticker"] != tkr]
            res.equity.append((day_str, round(cum_pnl, 2)))

        # ── Scan ─────────────────────────────────────────────────────────────
        if len(positions) >= max_pos:
            continue

        held = {p["ticker"] for p in positions}
        candidates: list[tuple[int, str, dict]] = []

        for tkr in universe:
            if tkr in held or tkr not in signals:
                continue
            df = signals[tkr]
            if day not in df.index:
                continue
            row = df.loc[day].to_dict()
            if math.isnan(row.get("price", float("nan"))):
                continue
            s = score_fn(row, fund_data.get(tkr, {}))
            if s >= threshold:
                candidates.append((s, tkr, row))

        candidates.sort(key=lambda x: x[0], reverse=True)

        for score, tkr, row in candidates:
            if len(positions) >= max_pos:
                break
            price = float(row["price"])
            positions.append({
                "ticker":      tkr,
                "entry_price": round(price, 6),
                "entry_date":  day_str,
                "score":       score,
                "quantity":    round(cost / price, 8),
            })

    # Close any still-open positions at last available price
    if positions and trading_days:
        last_day = trading_days[-1]
        last_str = str(last_day.date())
        for pos in positions:
            tkr = pos["ticker"]
            if tkr not in signals or last_day not in signals[tkr].index:
                continue
            price = float(signals[tkr].at[last_day, "price"])
            if math.isnan(price):
                continue
            pnl     = round((price - pos["entry_price"]) * pos["quantity"], 4)
            pnl_pct = round((price / pos["entry_price"] - 1) * 100, 2)
            cum_pnl += pnl
            res.trades.append(Trade(
                ticker=tkr, entry_date=pos["entry_date"],
                exit_date=last_str, entry_price=pos["entry_price"],
                exit_price=round(price, 6), pnl=pnl, pnl_pct=pnl_pct,
                exit_reason="end_of_backtest", entry_score=pos["score"],
                holding_days=(last_day.date() - pd.Timestamp(pos["entry_date"]).date()).days,
            ))
        res.equity.append((last_str, round(cum_pnl, 2)))

    return res


# ── Stats helper ──────────────────────────────────────────────────────────────

def calc_stats(res: SimResult) -> dict:
    trades = res.trades
    if not trades:
        return dict(
            total_trades=0, wins=0, losses=0, win_rate=0,
            total_pnl=0.0, avg_win=0.0, avg_loss=0.0,
            best_trade=None, worst_trade=None,
            avg_hold=0, max_drawdown=0.0,
        )

    wins   = [t for t in trades if t.pnl >= 0]
    losses = [t for t in trades if t.pnl < 0]
    total  = round(sum(t.pnl for t in trades), 2)

    # Max drawdown from equity curve
    equity_vals = [v for _, v in res.equity]
    max_dd = 0.0
    if equity_vals:
        peak = equity_vals[0]
        for v in equity_vals:
            peak  = max(peak, v)
            max_dd = min(max_dd, v - peak)

    return dict(
        total_trades = len(trades),
        wins         = len(wins),
        losses       = len(losses),
        win_rate     = round(len(wins) / len(trades) * 100, 1),
        total_pnl    = total,
        avg_win      = round(sum(t.pnl_pct for t in wins)   / len(wins),   1) if wins   else 0.0,
        avg_loss     = round(sum(t.pnl_pct for t in losses) / len(losses), 1) if losses else 0.0,
        best_trade   = max(trades, key=lambda t: t.pnl_pct),
        worst_trade  = min(trades, key=lambda t: t.pnl_pct),
        avg_hold     = round(sum(t.holding_days for t in trades) / len(trades), 1),
        max_drawdown = round(max_dd, 2),
    )
