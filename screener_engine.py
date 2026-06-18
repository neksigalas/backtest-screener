"""
Screener Backtesting Engine.

Simulates running a daily stock screener over a historical period,
opens virtual positions when criteria are met, and closes them on
TP / SL / MaxHold / RSI-exit signals.
"""

import json
import os
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

import pandas as pd
import yfinance as yf

from universe import UNIVERSES

CACHE_DIR = "cache"
SECTOR_CACHE_FILE = os.path.join(CACHE_DIR, "sectors.json")


# ── RSI ───────────────────────────────────────────────────────────────────────

def calc_rsi(closes: pd.Series, period: int) -> pd.Series:
    delta    = closes.diff()
    gain     = delta.where(delta > 0, 0.0)
    loss     = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ── Sector cache ──────────────────────────────────────────────────────────────

def load_sector_cache():
    os.makedirs(CACHE_DIR, exist_ok=True)
    if os.path.exists(SECTOR_CACHE_FILE):
        with open(SECTOR_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_sector_cache(cache):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(SECTOR_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def fetch_sectors(tickers, cache):
    missing = [t for t in tickers if t not in cache]
    if not missing:
        return cache
    print(f"Fetching sector info for {len(missing)} tickers...")
    for ticker in missing:
        try:
            info = yf.Ticker(ticker).info
            cache[ticker] = {
                "sector":     info.get("sector", "Unknown"),
                "market_cap": info.get("marketCap", 0) or 0,
            }
        except Exception:
            cache[ticker] = {"sector": "Unknown", "market_cap": 0}
    save_sector_cache(cache)
    return cache


# ── Main backtest ─────────────────────────────────────────────────────────────

def run_backtest(params: dict, job_id: str, jobs: dict):
    """
    params keys:
        rsi_threshold   (int)   — e.g. 30
        rsi_period      (int)   — e.g. 14
        sectors         (list)  — e.g. ["Energy", "Technology"]
        min_market_cap  (str)   — "any" | "mid" | "large"
        last_day_red    (bool)
        min_consec_red  (int)   — 0 = any
        market_regime   (bool)  — SPY above 200-day MA
        tp              (float) — take profit %
        sl              (float) — stop loss % (positive value, applied as negative)
        max_hold        (int)   — days
        lookback_months (int)   — 3 / 6 / 12
        universe        (str)   — "sp100" / "sp500" / "nasdaq100"
        investment      (float) — EUR per trade
        max_positions   (int)   — max concurrent open positions
    """
    try:
        jobs[job_id]["status"]   = "running"
        jobs[job_id]["progress"] = 2

        # ── Date range ────────────────────────────────────────────────────────
        end_date   = date.today()
        start_date = end_date - relativedelta(months=int(params["lookback_months"]))
        warmup_start = start_date - timedelta(days=90)   # RSI warmup

        # ── Universe ──────────────────────────────────────────────────────────
        tickers = list(dict.fromkeys(UNIVERSES.get(params["universe"], UNIVERSES["sp100"])))

        jobs[job_id]["progress"] = 5

        # ── Download price data ───────────────────────────────────────────────
        jobs[job_id]["message"] = "Downloading price data..."
        raw = yf.download(
            tickers + ["SPY"],
            start=str(warmup_start),
            end=str(end_date + timedelta(days=1)),
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )

        jobs[job_id]["progress"] = 30

        # ── Sector info ───────────────────────────────────────────────────────
        jobs[job_id]["message"] = "Loading sector info..."
        sector_cache = load_sector_cache()
        sector_cache  = fetch_sectors(tickers, sector_cache)

        jobs[job_id]["progress"] = 40

        # ── Build per-ticker DataFrames ───────────────────────────────────────
        def get_hist(ticker):
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    df = raw[ticker].dropna()
                else:
                    df = raw.dropna()
                return df if not df.empty else None
            except Exception:
                return None

        spy_hist = get_hist("SPY")

        # ── RSI + consecutive-red pre-computation ─────────────────────────────
        jobs[job_id]["message"] = "Computing indicators..."

        rsi_period = int(params.get("rsi_period", 14))

        ticker_data = {}   # ticker -> {rsi: Series, closes: Series}
        for ticker in tickers:
            hist = get_hist(ticker)
            if hist is None or len(hist) < rsi_period + 5:
                continue
            closes = hist["Close"]
            rsi    = calc_rsi(closes, rsi_period)
            ticker_data[ticker] = {"closes": closes, "rsi": rsi, "hist": hist}

        jobs[job_id]["progress"] = 50

        # ── Market cap thresholds ─────────────────────────────────────────────
        min_cap = 0
        if params.get("min_market_cap") == "mid":
            min_cap = 2_000_000_000
        elif params.get("min_market_cap") == "large":
            min_cap = 10_000_000_000

        # ── Simulation ────────────────────────────────────────────────────────
        jobs[job_id]["message"] = "Running simulation..."

        trading_days = pd.bdate_range(str(start_date), str(end_date))
        open_positions = {}   # ticker -> {entry_date, entry_price}
        all_trades     = []
        max_pos        = int(params.get("max_positions", 20))

        tp_pct  = float(params["tp"])
        sl_pct  = float(params["sl"])
        mh      = int(params["max_hold"])
        invest  = float(params.get("investment", 100))

        allowed_sectors = set(params.get("sectors", []))
        last_day_red    = params.get("last_day_red", False)
        min_crd         = int(params.get("min_consec_red", 0))
        check_regime    = params.get("market_regime", False)

        n_days = len(trading_days)

        for day_idx, ts in enumerate(trading_days):
            day = ts.date()

            # Progress update
            pct = 50 + int(day_idx / n_days * 45)
            jobs[job_id]["progress"] = pct

            # Market regime check
            regime_ok = True
            if check_regime and spy_hist is not None:
                try:
                    spy_slice  = spy_hist["Close"].loc[:str(ts)]
                    spy_close  = float(spy_slice.iloc[-1])
                    sma200     = float(spy_slice.tail(200).mean())
                    regime_ok  = spy_close >= sma200
                except Exception:
                    regime_ok = True

            # ── Check exits ───────────────────────────────────────────────────
            for ticker in list(open_positions.keys()):
                pos  = open_positions[ticker]
                td   = ticker_data.get(ticker)
                if td is None:
                    continue
                try:
                    day_close = float(td["closes"].loc[str(ts)])
                except Exception:
                    continue

                pl_pct      = (day_close - pos["entry_price"]) / pos["entry_price"] * 100
                days_held   = (day - pos["entry_date"]).days
                exit_reason = None
                exit_price  = day_close

                if pl_pct >= tp_pct:
                    exit_reason = "TP"
                    exit_price  = pos["entry_price"] * (1 + tp_pct / 100)
                elif pl_pct <= -sl_pct:
                    exit_reason = "SL"
                    exit_price  = pos["entry_price"] * (1 - sl_pct / 100)
                elif days_held >= mh:
                    exit_reason = "MAX_HOLD"

                if exit_reason:
                    pl = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100
                    all_trades.append({
                        "entry_date":  str(pos["entry_date"]),
                        "exit_date":   str(day),
                        "ticker":      ticker,
                        "sector":      sector_cache.get(ticker, {}).get("sector", "Unknown"),
                        "entry_price": round(pos["entry_price"], 2),
                        "exit_price":  round(exit_price, 2),
                        "pl_pct":      round(pl, 3),
                        "pl_eur":      round(invest * pl / 100, 2),
                        "exit_reason": exit_reason,
                        "days_held":   days_held,
                    })
                    del open_positions[ticker]

            # ── Screen for entries ────────────────────────────────────────────
            if not regime_ok:
                continue

            if len(open_positions) >= max_pos:
                continue

            current_month = day.month
            if current_month in (params.get("avoid_months") or []):
                continue

            for ticker in tickers:
                if len(open_positions) >= max_pos:
                    break
                if ticker in open_positions:
                    continue

                td = ticker_data.get(ticker)
                if td is None:
                    continue

                # RSI filter
                try:
                    rsi_val = float(td["rsi"].loc[str(ts)])
                except Exception:
                    continue
                if pd.isna(rsi_val) or rsi_val > float(params["rsi_threshold"]):
                    continue

                closes = td["closes"]

                # Last day red filter
                try:
                    close_today = float(closes.loc[str(ts)])
                    close_prev  = float(closes.iloc[closes.index.get_loc(str(ts)) - 1])
                except Exception:
                    continue

                if last_day_red and close_today >= close_prev:
                    continue

                # Consecutive red days
                if min_crd > 0:
                    idx = closes.index.get_loc(str(ts))
                    crd = 0
                    for i in range(idx, 0, -1):
                        if float(closes.iloc[i]) < float(closes.iloc[i - 1]):
                            crd += 1
                        else:
                            break
                    if crd < min_crd:
                        continue

                # Sector filter
                sec_info = sector_cache.get(ticker, {})
                sector   = sec_info.get("sector", "Unknown")
                if allowed_sectors and sector not in allowed_sectors:
                    continue

                # Market cap filter
                if min_cap > 0:
                    cap = sec_info.get("market_cap", 0) or 0
                    if cap and cap < min_cap:
                        continue

                # Signal! Open position
                open_positions[ticker] = {
                    "entry_date":  day,
                    "entry_price": close_today,
                }

        # ── Close remaining open positions at last available price ────────────
        for ticker, pos in open_positions.items():
            td = ticker_data.get(ticker)
            if td is None:
                continue
            try:
                exit_price = float(td["closes"].iloc[-1])
                pl = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100
                all_trades.append({
                    "entry_date":  str(pos["entry_date"]),
                    "exit_date":   str(end_date),
                    "ticker":      ticker,
                    "sector":      sector_cache.get(ticker, {}).get("sector", "Unknown"),
                    "entry_price": round(pos["entry_price"], 2),
                    "exit_price":  round(exit_price, 2),
                    "pl_pct":      round(pl, 3),
                    "pl_eur":      round(invest * pl / 100, 2),
                    "exit_reason": "OPEN",
                    "days_held":   (end_date - pos["entry_date"]).days,
                })
            except Exception:
                pass

        # ── Summary stats ─────────────────────────────────────────────────────
        closed = [t for t in all_trades if t["exit_reason"] != "OPEN"]
        if closed:
            pls   = [t["pl_pct"] for t in closed]
            wins  = [p for p in pls if p > 0]
            wr    = round(len(wins) / len(pls) * 100, 1)
            ev    = round(sum(pls) / len(pls), 3)
            total = round(sum(t["pl_eur"] for t in closed), 2)
            aw    = round(sum(wins) / len(wins), 2) if wins else 0
            al    = round(sum(p for p in pls if p <= 0) / max(1, len([p for p in pls if p <= 0])), 2)
            sl_n  = len([t for t in closed if t["exit_reason"] == "SL"])
            tp_n  = len([t for t in closed if t["exit_reason"] == "TP"])
            mh_n  = len([t for t in closed if t["exit_reason"] == "MAX_HOLD"])

            # Equity curve: cumulative P&L by date
            daily_pl = {}
            for t in closed:
                d = t["exit_date"]
                daily_pl[d] = daily_pl.get(d, 0) + t["pl_eur"]
            sorted_days = sorted(daily_pl.keys())
            cumulative  = []
            running     = 0
            for d in sorted_days:
                running += daily_pl[d]
                cumulative.append({"date": d, "cum_pl": round(running, 2)})

            # Sector breakdown
            sector_stats = {}
            for t in closed:
                s = t["sector"]
                if s not in sector_stats:
                    sector_stats[s] = {"n": 0, "wins": 0, "pl": 0}
                sector_stats[s]["n"]    += 1
                sector_stats[s]["wins"] += 1 if t["pl_pct"] > 0 else 0
                sector_stats[s]["pl"]   += t["pl_pct"]
            sector_breakdown = [
                {
                    "sector": s,
                    "trades": v["n"],
                    "wr": round(v["wins"] / v["n"] * 100, 1),
                    "ev": round(v["pl"] / v["n"], 3),
                }
                for s, v in sorted(sector_stats.items(), key=lambda x: -x[1]["pl"])
            ]

            summary = {
                "total_trades": len(closed),
                "open_trades":  len(all_trades) - len(closed),
                "win_rate":     wr,
                "ev":           ev,
                "total_pl":     total,
                "avg_win":      aw,
                "avg_loss":     al,
                "tp_count":     tp_n,
                "sl_count":     sl_n,
                "mh_count":     mh_n,
                "sl_rate":      round(sl_n / len(closed) * 100, 1),
                "equity_curve": cumulative,
                "sector_breakdown": sector_breakdown,
            }
        else:
            summary = {
                "total_trades": 0, "open_trades": 0, "win_rate": 0,
                "ev": 0, "total_pl": 0, "avg_win": 0, "avg_loss": 0,
                "tp_count": 0, "sl_count": 0, "mh_count": 0, "sl_rate": 0,
                "equity_curve": [], "sector_breakdown": [],
            }

        jobs[job_id] = {
            "status":   "done",
            "progress": 100,
            "message":  "Complete",
            "summary":  summary,
            "trades":   sorted(all_trades, key=lambda x: x["entry_date"]),
        }

    except Exception as e:
        import traceback
        jobs[job_id] = {
            "status":  "error",
            "message": str(e),
            "detail":  traceback.format_exc(),
        }
