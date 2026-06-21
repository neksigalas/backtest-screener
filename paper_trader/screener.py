"""
Standalone screener for 3 Best Stocks Paper Trading.
Adapted from screener_live.py — runs synchronously, no FastAPI job tracking.
Scores each stock 0-100 and returns candidates with score > SCORE_THRESHOLD.
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
from paper_trader.engine import SCORE_THRESHOLD

CACHE_DIR   = Path(__file__).parent.parent / "cache"
ALL_TICKERS = list(dict.fromkeys(SP100 + NASDAQ100 + SP500_SAMPLE))
CACHE_DIR.mkdir(exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _calc_rsi(prices: pd.Series, period: int = 14) -> float:
    delta = prices.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    ag    = gain.ewm(alpha=1 / period, min_periods=period).mean()
    al    = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs    = ag / al.replace(0, 1e-9)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def _r(val, d=1):
    try:
        return round(float(val), d)
    except Exception:
        return None


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

    # Technical (max 40)
    rsi = s.get("rsi", 50)
    if rsi <= 40:
        score += round(25 * (40 - rsi) / 40)

    score += {0: 0, 1: 3, 2: 6}.get(s.get("consec_red", 0), 10)

    vr = s.get("vol_ratio", 1.0)
    if   vr >= 2.0: score += 5
    elif vr >= 1.5: score += 3
    elif vr >= 1.2: score += 1

    # Fundamental (max 60)
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

    # yfinance debtToEquity: 50 = 0.5 actual D/E ratio
    de = s.get("debt_to_equity")
    if   de is None: score += 5
    elif de <= 30:   score += 15
    elif de <= 70:   score += 10
    elif de <= 150:  score += 5

    return min(100, score)


# ── Main screener ─────────────────────────────────────────────────────────────

def run_screener() -> list[dict]:
    """Scan all tickers, return full list sorted by score (highest first)."""
    print(f"Scanning {len(ALL_TICKERS)} tickers...")
    end   = date.today()
    start = end - timedelta(days=120)

    raw = yf.download(
        ALL_TICKERS,
        start=str(start), end=str(end),
        auto_adjust=True, progress=False, threads=True,
    )

    if isinstance(raw.columns, pd.MultiIndex):
        close, volume = raw["Close"], raw["Volume"]
    else:
        t      = ALL_TICKERS[0]
        close  = raw[["Close"]].rename(columns={"Close": t})
        volume = raw[["Volume"]].rename(columns={"Volume": t})

    stocks = []
    for ticker in ALL_TICKERS:
        if ticker not in close.columns:
            continue
        prices = close[ticker].dropna()
        vols   = volume[ticker].dropna() if ticker in volume.columns else pd.Series(dtype=float)
        if len(prices) < 25:
            continue

        cur   = float(prices.iloc[-1])
        prev1 = float(prices.iloc[-2])

        rsi = _calc_rsi(prices)

        vol_today = float(vols.iloc[-1]) if len(vols) else 0
        avg_vol   = float(vols.iloc[-20:].mean()) if len(vols) >= 20 else (float(vols.mean()) if len(vols) else 1)
        vol_ratio = vol_today / avg_vol if avg_vol else 1.0

        crd = 0
        for c in reversed(prices.pct_change().dropna().values[:-1]):
            if c < 0:
                crd += 1
            else:
                break

        stocks.append({
            "ticker":     ticker,
            "price":      round(cur, 2),
            "change_1d":  round((cur / prev1 - 1) * 100, 2),
            "rsi":        round(rsi, 1),
            "vol_ratio":  round(vol_ratio, 2),
            "consec_red": crd,
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
    """Return stocks with score > threshold, excluding already-held tickers."""
    all_stocks = run_screener()
    exclude = set(exclude_tickers)
    return [s for s in all_stocks if s["score"] >= SCORE_THRESHOLD and s["ticker"] not in exclude]
