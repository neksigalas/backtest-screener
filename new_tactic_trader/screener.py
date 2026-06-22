"""
Improved screener for New Tactic 3 Best Stocks Paper Trading.

Key differences vs original paper_trader/screener.py:
  1. Reversal confirmation  — stock must show first green day after red streak
  2. MACD bullish cross     — momentum turning up (cross within last 5 days)
  3. Market regime filter   — SPY > 50d MA adds points; bear market penalises
  4. Bullish RSI divergence — price lower low but RSI higher low (bonus)

Fundamentals (P/E, ROE, margins, D/E) reuse the same daily cache as the
original screener so no duplicate API calls on days both scans run.
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))
from universe import SP100, NASDAQ100, SP500_SAMPLE
from new_tactic_trader.engine import SCORE_THRESHOLD

CACHE_DIR   = Path(__file__).parent.parent / "cache"
ALL_TICKERS = list(dict.fromkeys(SP100 + NASDAQ100 + SP500_SAMPLE))
CACHE_DIR.mkdir(exist_ok=True)


# ── Technical indicators ──────────────────────────────────────────────────────

def _calc_rsi(prices: pd.Series, period: int = 14) -> float:
    delta = prices.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    ag    = gain.ewm(alpha=1 / period, min_periods=period).mean()
    al    = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs    = ag / al.replace(0, 1e-9)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def _calc_macd(prices: pd.Series):
    ema12  = prices.ewm(span=12, adjust=False).mean()
    ema26  = prices.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal


def _macd_cross_days_ago(prices: pd.Series) -> int | None:
    """
    Returns how many completed trading days ago MACD crossed above its signal line.
    0 = crossed on the most recent completed session.
    None = no bullish cross in the last 5 sessions.
    """
    if len(prices) < 35:
        return None
    macd, signal = _calc_macd(prices)
    for i in range(1, 6):
        curr_above = macd.iloc[-i]   > signal.iloc[-i]
        prev_below = macd.iloc[-i-1] <= signal.iloc[-i-1]
        if curr_above and prev_below:
            return i - 1   # 0-indexed: 0 = today's session
    return None


def _spy_bull_regime(spy_prices: pd.Series) -> bool:
    """True when SPY is above its 50-day simple moving average."""
    if len(spy_prices) < 50:
        return True   # not enough history, assume bull
    return float(spy_prices.iloc[-1]) > float(spy_prices.iloc[-50:].mean())


def _bullish_divergence(prices: pd.Series, rsi_series: pd.Series, lookback: int = 10) -> bool:
    """
    True when price makes a lower low but RSI makes a higher low over `lookback` bars.
    Classic reversal signal: sellers losing momentum.
    """
    if len(prices) < lookback + 2 or len(rsi_series) < lookback + 2:
        return False
    price_prev_low = float(prices.iloc[-lookback:-1].min())
    price_curr_low = float(prices.iloc[-1])
    rsi_prev_low   = float(rsi_series.iloc[-lookback:-1].min())
    rsi_curr_low   = float(rsi_series.iloc[-1])
    return price_curr_low < price_prev_low and rsi_curr_low > rsi_prev_low


def _r(val, d=1):
    try:
        return round(float(val), d)
    except Exception:
        return None


# ── Fundamentals (shared daily cache with paper_trader) ───────────────────────

def _fetch_one(ticker: str) -> tuple:
    try:
        info = yf.Ticker(ticker).info
        return ticker, {
            "sector":         info.get("sector", ""),
            "forward_pe":     info.get("forwardPE"),
            "roe":            info.get("returnOnEquity"),
            "net_margin":     info.get("profitMargins"),
            "debt_to_equity": info.get("debtToEquity"),
        }
    except Exception:
        return ticker, {}


def _load_fund(tickers: list) -> dict:
    today = date.today().isoformat()
    cache = CACHE_DIR / f"fund_{today}.json"

    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if len(data) >= len(tickers) * 0.8:
                print(f"  Using cached fundamentals ({len(data)} tickers)")
                return data
        except Exception:
            pass

    for old in CACHE_DIR.glob("fund_*.json"):
        try:
            old.unlink()
        except Exception:
            pass

    data = {}
    print(f"  Fetching fundamentals for {len(tickers)} tickers (~60s)...")
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_fetch_one, t): t for t in tickers}
        done = 0
        for fut in as_completed(futs):
            tk, d = fut.result()
            data[tk] = d
            done += 1
            if done % 50 == 0:
                print(f"    {done}/{len(tickers)}...")

    try:
        cache.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass
    return data


# ── Scoring ───────────────────────────────────────────────────────────────────

def compute_score(s: dict) -> int:
    score = 0

    # ── 1. Reversal confirmation (max 20) ─────────────────────────────────────
    # The most important NEW signal: first green day after a red streak.
    # Old tactic buys during the fall; new tactic waits for the first bounce.
    reds_before   = s.get("reds_before_reversal", 0)
    today_is_green = s.get("today_is_green", False)
    if today_is_green:
        if   reds_before >= 3: score += 20   # strongest: bounce after 3+ reds
        elif reds_before == 2: score += 14
        elif reds_before == 1: score += 8

    # ── 2. RSI — oversold level at time of reversal (max 20) ─────────────────
    # We score RSI from the previous red session (the bottom), not today's.
    rsi_at_low = s.get("rsi_at_low", s.get("rsi", 50))
    if   rsi_at_low <= 25: score += 20
    elif rsi_at_low <= 32: score += 16
    elif rsi_at_low <= 40: score += 10
    elif rsi_at_low <= 50: score += 4

    # ── 3. MACD bullish cross (max 15) ────────────────────────────────────────
    cross_days = s.get("macd_cross_days_ago")
    if cross_days is not None:
        if   cross_days == 0: score += 15   # crossed today/yesterday
        elif cross_days <= 2: score += 10
        elif cross_days <= 4: score += 5

    # ── 4. Volume confirmation (max 5) ────────────────────────────────────────
    # High volume on the green reversal day = genuine buying pressure
    vr = s.get("vol_ratio", 1.0)
    if   vr >= 2.0: score += 5
    elif vr >= 1.5: score += 3
    elif vr >= 1.2: score += 1

    # ── 5. Market regime — SPY vs 50d MA (max 10, can subtract) ──────────────
    if s.get("bull_regime", True):
        score += 10   # rising tide lifts all boats
    else:
        score -= 5    # bear market → mean-reversion fails more often

    # ── 6. Bullish RSI divergence bonus (max 5) ───────────────────────────────
    if s.get("bullish_divergence", False):
        score += 5

    # ── 7. Fundamentals (max 45) ──────────────────────────────────────────────
    # Same weights as original tactic so fundamentals comparison is fair.
    fpe = s.get("forward_pe")
    if fpe and fpe > 0:
        if   5  <= fpe <= 15: score += 15
        elif 15 < fpe <= 25:  score += 10
        elif 25 < fpe <= 35:  score += 5

    roe = s.get("roe")
    if roe is not None:
        if   roe >= 25: score += 15
        elif roe >= 15: score += 10
        elif roe >= 5:  score += 5

    nm = s.get("net_margin")
    if nm is not None:
        if   nm >= 20: score += 15
        elif nm >= 10: score += 10
        elif nm >= 5:  score += 5

    return min(100, max(0, score))


# ── Main screener ─────────────────────────────────────────────────────────────

def run_screener(spy_prices: pd.Series | None = None) -> list[dict]:
    """
    Scan all tickers. Returns full list sorted by score (highest first).
    spy_prices injected for testing; fetched internally when None.
    """
    print(f"[New Tactic] Scanning {len(ALL_TICKERS)} tickers...")
    end   = date.today()
    start = end - timedelta(days=120)

    # Download SPY together with all tickers to save a round-trip
    all_dl = list(dict.fromkeys(["SPY"] + ALL_TICKERS))
    raw = yf.download(
        all_dl,
        start=str(start), end=str(end),
        auto_adjust=True, progress=False, threads=True,
    )

    if isinstance(raw.columns, pd.MultiIndex):
        close  = raw["Close"]
        volume = raw["Volume"]
    else:
        t      = all_dl[0]
        close  = raw[["Close"]].rename(columns={"Close": t})
        volume = raw[["Volume"]].rename(columns={"Volume": t})

    # Market regime: is SPY in a bull trend?
    if spy_prices is None and "SPY" in close.columns:
        spy_prices = close["SPY"].dropna()
    bull = _spy_bull_regime(spy_prices) if spy_prices is not None else True
    print(f"  Market regime: {'🟢 BULL (SPY > 50d MA)' if bull else '🔴 BEAR (SPY < 50d MA)'}")

    stocks = []
    for ticker in ALL_TICKERS:
        if ticker not in close.columns:
            continue
        prices = close[ticker].dropna()
        vols   = volume[ticker].dropna() if ticker in volume.columns else pd.Series(dtype=float)
        if len(prices) < 35:
            continue

        returns = prices.pct_change().dropna()

        # Reversal: most recent completed day
        today_return = float(returns.iloc[-1])
        today_is_green = today_return > 0

        # Count consecutive reds immediately before today's session
        reds_before = 0
        for r in reversed(returns.values[:-1]):
            if r < 0:
                reds_before += 1
            else:
                break

        cur   = float(prices.iloc[-1])
        prev1 = float(prices.iloc[-2])

        # RSI at the bottom (yesterday's RSI when today is green, else current)
        rsi_now    = _calc_rsi(prices)
        rsi_at_low = _calc_rsi(prices.iloc[:-1]) if today_is_green and len(prices) > 35 else rsi_now

        # MACD cross
        cross_days = _macd_cross_days_ago(prices)

        # Volume
        vol_today = float(vols.iloc[-1]) if len(vols) else 0
        avg_vol   = float(vols.iloc[-20:].mean()) if len(vols) >= 20 else (float(vols.mean()) if len(vols) else 1)
        vol_ratio = vol_today / avg_vol if avg_vol else 1.0

        # Bullish divergence
        rsi_series = pd.Series([
            _calc_rsi(prices.iloc[:i]) for i in range(max(15, len(prices) - 15), len(prices) + 1)
        ])
        divergence = _bullish_divergence(prices.iloc[-len(rsi_series):], rsi_series)

        stocks.append({
            "ticker":               ticker,
            "price":                round(cur, 2),
            "change_1d":            round((cur / prev1 - 1) * 100, 2),
            "rsi":                  round(rsi_now, 1),
            "rsi_at_low":           round(rsi_at_low, 1),
            "vol_ratio":            round(vol_ratio, 2),
            "today_is_green":       today_is_green,
            "reds_before_reversal": reds_before,
            "macd_cross_days_ago":  cross_days,
            "bull_regime":          bull,
            "bullish_divergence":   divergence,
        })

    fund = _load_fund([s["ticker"] for s in stocks])
    for s in stocks:
        f   = fund.get(s["ticker"], {})
        roe = f.get("roe")
        nm  = f.get("net_margin")
        s.update({
            "sector":         f.get("sector") or "—",
            "forward_pe":     _r(f.get("forward_pe")),
            "roe":            _r((roe or 0) * 100, 1) if roe is not None else None,
            "net_margin":     _r((nm  or 0) * 100, 1) if nm  is not None else None,
            "debt_to_equity": _r(f.get("debt_to_equity")),
        })
        s["score"] = compute_score(s)

    return sorted(stocks, key=lambda x: x["score"], reverse=True)


def get_candidates(exclude_tickers: list[str]) -> list[dict]:
    """Return stocks with score >= threshold, excluding already-held tickers."""
    all_stocks = run_screener()
    exclude    = set(exclude_tickers)
    return [s for s in all_stocks if s["score"] >= SCORE_THRESHOLD and s["ticker"] not in exclude]
