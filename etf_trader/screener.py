"""
Screener for 3 Best ETF Paper Trading.
Scores each ETF 0-100 using:
  Technical  (60 pts): RSI + consecutive red days + volume ratio + 52w drawdown
  ETF traits (40 pts): leverage factor + daily liquidity (avg volume × price)
All data via yfinance — no external API needed.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))
from etf_trader.universe import ETF_UNIVERSE, LEVERAGE
from etf_trader.engine import SCORE_THRESHOLD


# ── Helpers ───────────────────────────────────────────────────────────────────

def _r(val, d=1):
    try:
        return round(float(val), d)
    except Exception:
        return None


def _calc_rsi(prices: pd.Series, period: int = 14) -> float:
    delta = prices.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    ag    = gain.ewm(alpha=1 / period, min_periods=period).mean()
    al    = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs    = ag / al.replace(0, 1e-9)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def _get_name(ticker: str) -> str:
    try:
        info = yf.Ticker(ticker).info
        return info.get("shortName") or info.get("longName") or ticker
    except Exception:
        return ticker


# ── Scoring ───────────────────────────────────────────────────────────────────

def compute_score(s: dict) -> int:
    score = 0

    # ── Technical (max 60) ────────────────────────────────────────────────────
    rsi = s.get("rsi", 50)
    if rsi <= 30:
        score += 25
    elif rsi <= 40:
        score += 15
    elif rsi <= 50:
        score += 5

    score += {0: 0, 1: 3, 2: 6}.get(s.get("consec_red", 0), 10)

    vr = s.get("vol_ratio", 1.0)
    if   vr >= 2.0: score += 5
    elif vr >= 1.5: score += 3
    elif vr >= 1.2: score += 1

    # Drawdown from 52-week high: oversold but not in structural collapse
    dd = s.get("drawdown_52w")
    if dd is not None:
        dd = abs(dd)   # dd stored as negative pct, abs for comparison
        if   15 <= dd <= 30: score += 20
        elif 30 <  dd <= 50: score += 12
        elif  8 <= dd <  15: score += 6

    # ── ETF characteristics (max 40) ──────────────────────────────────────────
    lev = s.get("leverage", 1)
    if   lev >= 3: score += 20
    elif lev == 2: score += 12
    else:          score += 4

    # Daily liquidity = avg_volume × price (USD traded per day)
    liq = s.get("liquidity_m")  # in $M
    if liq is not None:
        if   liq >= 100: score += 20
        elif liq >=  25: score += 14
        elif liq >=   5: score += 8
        elif liq >=   1: score += 3

    return min(100, score)


# ── Main screener ─────────────────────────────────────────────────────────────

def run_screener() -> list[dict]:
    """Download price history for all ETFs, compute scores, return sorted list."""
    print(f"Scanning {len(ETF_UNIVERSE)} ETFs...")
    end   = date.today()
    start = end - timedelta(days=270)   # ~1 year for 52w high calc

    raw = yf.download(
        ETF_UNIVERSE,
        start=str(start), end=str(end),
        auto_adjust=True, progress=False, threads=True,
    )

    if isinstance(raw.columns, pd.MultiIndex):
        close  = raw["Close"]
        volume = raw["Volume"]
    else:
        t      = ETF_UNIVERSE[0]
        close  = raw[["Close"]].rename(columns={"Close": t})
        volume = raw[["Volume"]].rename(columns={"Volume": t})

    # Fetch names in parallel (only for ETFs present in data)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    present = [t for t in ETF_UNIVERSE if t in close.columns]
    names: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_get_name, t): t for t in present}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                names[t] = fut.result()
            except Exception:
                names[t] = t

    results = []
    for ticker in ETF_UNIVERSE:
        if ticker not in close.columns:
            continue
        prices = close[ticker].dropna()
        vols   = volume[ticker].dropna() if ticker in volume.columns else pd.Series(dtype=float)
        if len(prices) < 25:
            continue

        cur   = float(prices.iloc[-1])
        prev1 = float(prices.iloc[-2])
        rsi   = _calc_rsi(prices)

        vol_today = float(vols.iloc[-1]) if len(vols) else 0
        avg_vol   = float(vols.iloc[-20:].mean()) if len(vols) >= 20 else (float(vols.mean()) if len(vols) else 1)
        vol_ratio = vol_today / avg_vol if avg_vol else 1.0

        # Liquidity in $M/day
        liquidity_m = _r(avg_vol * cur / 1_000_000)

        # Consecutive red days
        crd = 0
        for c in reversed(prices.pct_change().dropna().values[:-1]):
            if c < 0:
                crd += 1
            else:
                break

        # 52-week drawdown
        high_52w = float(prices.iloc[-252:].max()) if len(prices) >= 252 else float(prices.max())
        drawdown = _r((cur / high_52w - 1) * 100)

        s = {
            "ticker":      ticker,
            "name":        names.get(ticker, ticker),
            "price":       round(cur, 2),
            "change_1d":   _r((cur / prev1 - 1) * 100),
            "rsi":         round(rsi, 1),
            "vol_ratio":   round(vol_ratio, 2),
            "consec_red":  crd,
            "drawdown_52w": drawdown,
            "leverage":    LEVERAGE.get(ticker, 1),
            "liquidity_m": liquidity_m,
        }
        s["score"] = compute_score(s)
        results.append(s)

    return sorted(results, key=lambda x: x["score"], reverse=True)


def get_candidates(exclude_tickers: list[str]) -> list[dict]:
    """Return ETFs with score >= threshold, excluding already-held tickers."""
    all_etfs = run_screener()
    exclude  = set(exclude_tickers)
    return [s for s in all_etfs if s["score"] >= SCORE_THRESHOLD and s["ticker"] not in exclude]
