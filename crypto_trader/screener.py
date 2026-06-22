"""
Screener for 3 Best Crypto Paper Trading.
Fetches top ~80 cryptos from CoinGecko (free API, no key needed),
computes a 0-100 score using technical + market-cap fundamentals,
and returns candidates with score >= SCORE_THRESHOLD.
"""

import json
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))
from crypto_trader.engine import SCORE_THRESHOLD

CACHE_DIR = Path(__file__).parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

STABLECOINS = {
    "usdt", "usdc", "busd", "dai", "tusd", "usdp", "usdd", "frax",
    "gusd", "lusd", "usde", "pyusd", "fdusd", "usd0", "susd", "cusd",
}

COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=market_cap_desc&per_page=100&page=1"
    "&price_change_percentage=7d"
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _r(val, d=2):
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


def _fetch_coingecko() -> list[dict]:
    """Fetch top 100 coins from CoinGecko, cached per day."""
    today = date.today().isoformat()
    cache = CACHE_DIR / f"crypto_{today}.json"

    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            print(f"  Using cached CoinGecko data ({len(data)} coins)")
            return data
        except Exception:
            pass

    for old in CACHE_DIR.glob("crypto_*.json"):
        try:
            old.unlink()
        except Exception:
            pass

    print("  Fetching top 100 coins from CoinGecko...")
    try:
        req = urllib.request.Request(
            COINGECKO_URL,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        print(f"  CoinGecko error: {e}")
        return []

    # exclude stablecoins
    coins = [c for c in data if c.get("symbol", "").lower() not in STABLECOINS]

    try:
        cache.write_text(json.dumps(coins), encoding="utf-8")
    except Exception:
        pass

    return coins


# ── Scoring ───────────────────────────────────────────────────────────────────

def compute_score(s: dict) -> int:
    score = 0

    # ── Technical (max 40) ────────────────────────────────────────────────────
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

    # ── Market fundamentals (max 60) ──────────────────────────────────────────
    rank = s.get("market_cap_rank")
    if rank is not None:
        if   rank <= 5:  score += 15
        elif rank <= 10: score += 10
        elif rank <= 20: score += 6
        elif rank <= 50: score += 2

    # 24h volume / market cap — liquidity proxy
    vol_mc = s.get("vol_to_mcap")
    if vol_mc is not None:
        if   vol_mc >= 0.10: score += 15
        elif vol_mc >= 0.05: score += 10
        elif vol_mc >= 0.02: score += 5

    # 7-day price change: mild pullback = buy opportunity
    chg7d = s.get("change_7d")
    if chg7d is not None:
        if   -20 <= chg7d <= -5:  score += 15
        elif  -5 <  chg7d <= 0:   score += 8
        elif -40 <= chg7d < -20:  score += 5

    # ATH distance: down but not dying
    ath_pct = s.get("ath_change_pct")
    if ath_pct is not None:
        if   -50 <= ath_pct <= -20: score += 15
        elif -70 <= ath_pct < -50:  score += 8
        elif -20 <  ath_pct <= 0:   score += 5

    return min(100, score)


# ── Main screener ─────────────────────────────────────────────────────────────

def run_screener() -> list[dict]:
    """Scan all top cryptos, return full list sorted by score (highest first)."""
    coins = _fetch_coingecko()
    if not coins:
        return []

    # map yfinance ticker → CoinGecko coin data
    ticker_map: dict[str, dict] = {}
    for c in coins:
        sym = c.get("symbol", "").upper()
        if not sym:
            continue
        ticker_map[f"{sym}-USD"] = c

    tickers = list(ticker_map.keys())
    print(f"  Downloading price history for {len(tickers)} tickers...")

    end   = date.today()
    start = end - timedelta(days=120)

    try:
        raw = yf.download(
            tickers,
            start=str(start), end=str(end),
            auto_adjust=True, progress=False, threads=True,
        )
    except Exception as e:
        print(f"  yfinance download error: {e}")
        return []

    if isinstance(raw.columns, pd.MultiIndex):
        close  = raw["Close"]
        volume = raw["Volume"]
    else:
        t      = tickers[0]
        close  = raw[["Close"]].rename(columns={"Close": t})
        volume = raw[["Volume"]].rename(columns={"Volume": t})

    results = []
    for yf_ticker, coin in ticker_map.items():
        if yf_ticker not in close.columns:
            continue
        prices = close[yf_ticker].dropna()
        vols   = volume[yf_ticker].dropna() if yf_ticker in volume.columns else pd.Series(dtype=float)
        if len(prices) < 25:
            continue

        cur   = float(prices.iloc[-1])
        prev1 = float(prices.iloc[-2])
        rsi   = _calc_rsi(prices)

        vol_today = float(vols.iloc[-1]) if len(vols) else 0
        avg_vol   = float(vols.iloc[-20:].mean()) if len(vols) >= 20 else (float(vols.mean()) if len(vols) else 1)
        vol_ratio = vol_today / avg_vol if avg_vol else 1.0

        crd = 0
        for c in reversed(prices.pct_change().dropna().values[:-1]):
            if c < 0:
                crd += 1
            else:
                break

        market_cap   = coin.get("market_cap") or 0
        total_volume = coin.get("total_volume") or 0
        vol_to_mcap  = total_volume / market_cap if market_cap > 0 else None

        chg7d   = coin.get("price_change_percentage_7d_in_currency")
        ath_pct = coin.get("ath_change_percentage")

        s = {
            "ticker":          yf_ticker,
            "name":            coin.get("name", yf_ticker),
            "price":           cur,
            "change_1d":       _r((cur / prev1 - 1) * 100),
            "rsi":             round(rsi, 1),
            "vol_ratio":       round(vol_ratio, 2),
            "consec_red":      crd,
            "market_cap_rank": coin.get("market_cap_rank"),
            "market_cap":      market_cap,
            "vol_to_mcap":     _r(vol_to_mcap, 4),
            "change_7d":       _r(chg7d),
            "ath_change_pct":  _r(ath_pct),
        }
        s["score"] = compute_score(s)
        results.append(s)

    return sorted(results, key=lambda x: x["score"], reverse=True)


def get_candidates(exclude_tickers: list[str]) -> list[dict]:
    """Return cryptos with score >= threshold, excluding already-held tickers."""
    all_coins = run_screener()
    exclude   = set(exclude_tickers)
    return [s for s in all_coins if s["score"] >= SCORE_THRESHOLD and s["ticker"] not in exclude]
