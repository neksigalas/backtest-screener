"""
Live Screener — scans stocks using current market data.
Technical indicators: bulk yfinance download (fast).
Fundamental data: per-stock yfinance.info (cached daily).
"""

import json
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from universe import UNIVERSES

CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _calc_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta    = prices.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


def _r(val, digits=1):
    if val is None:
        return None
    try:
        return round(float(val), digits)
    except Exception:
        return None


def _fmt_cap(mc):
    if mc is None:
        return "—"
    if mc >= 200e9: return f"${mc/1e9:.0f}B"
    if mc >= 10e9:  return f"${mc/1e9:.0f}B"
    if mc >= 2e9:   return f"${mc/1e9:.1f}B"
    if mc >= 1e6:   return f"${mc/1e6:.0f}M"
    return f"${mc:.0f}"


def _cap_tier(mc):
    if mc is None:    return ""
    if mc >= 200e9:   return "Mega"
    if mc >= 10e9:    return "Large"
    if mc >= 2e9:     return "Mid"
    if mc >= 300e6:   return "Small"
    if mc >= 50e6:    return "Micro"
    return "Nano"


# ── Fundamental cache (refreshed once per day) ────────────────────────────────

def _get_fundamentals(tickers: list, job_id: str, jobs: dict) -> dict:
    today      = date.today().isoformat()
    cache_file = CACHE_DIR / f"fund_{today}.json"

    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            cached = {}
    else:
        for old in CACHE_DIR.glob("fund_*.json"):
            try:
                old.unlink()
            except Exception:
                pass
        cached = {}

    missing = [t for t in tickers if t not in cached]

    if missing:
        for i, ticker in enumerate(missing):
            pct = 55 + int((i / len(missing)) * 25)
            jobs[job_id]["progress"] = pct
            jobs[job_id]["message"]  = f"Fetching fundamentals ({i+1}/{len(missing)})…"
            try:
                info = yf.Ticker(ticker).info
                cached[ticker] = {
                    "sector":           info.get("sector", ""),
                    "industry":         info.get("industry", ""),
                    "market_cap":       info.get("marketCap"),
                    # valuation
                    "pe":               info.get("trailingPE"),
                    "forward_pe":       info.get("forwardPE"),
                    "peg":              info.get("pegRatio"),
                    "ps_ratio":         info.get("priceToSalesTrailing12Months"),
                    "price_to_book":    info.get("priceToBook"),
                    "ev_ebitda":        info.get("enterpriseToEbitda"),
                    # dividend
                    "dividend_yield":   info.get("dividendYield"),
                    "payout_ratio":     info.get("payoutRatio"),
                    # profitability (raw 0–1 fractions)
                    "roa":              info.get("returnOnAssets"),
                    "roe":              info.get("returnOnEquity"),
                    "gross_margin":     info.get("grossMargins"),
                    "operating_margin": info.get("operatingMargins"),
                    "net_margin":       info.get("profitMargins"),
                    # debt / liquidity
                    "debt_to_equity":   info.get("debtToEquity"),
                    "current_ratio":    info.get("currentRatio"),
                    "quick_ratio":      info.get("quickRatio"),
                    # other
                    "beta":             info.get("beta"),
                    "analyst_rating":   info.get("recommendationMean"),
                    "52w_high":         info.get("fiftyTwoWeekHigh"),
                    "52w_low":          info.get("fiftyTwoWeekLow"),
                }
            except Exception:
                cached[ticker] = {}
            time.sleep(0.07)

        try:
            cache_file.write_text(json.dumps(cached), encoding="utf-8")
        except Exception:
            pass

    return cached


# ── Apply all filters ─────────────────────────────────────────────────────────

def _apply_filters(stocks: list, p: dict) -> list:
    out = []
    for s in stocks:

        # ── Sector ──
        sectors = p.get("sectors", [])
        if sectors and s.get("sector") not in sectors:
            continue

        # ── Market cap ──
        mc_ranges = p.get("market_cap_ranges", [])
        mc = s.get("market_cap")
        if mc_ranges:
            if not mc:
                continue
            ok = any([
                "mega"  in mc_ranges and mc >= 200e9,
                "large" in mc_ranges and 10e9  <= mc < 200e9,
                "mid"   in mc_ranges and 2e9   <= mc < 10e9,
                "small" in mc_ranges and 300e6 <= mc < 2e9,
                "micro" in mc_ranges and 50e6  <= mc < 300e6,
                "nano"  in mc_ranges and mc < 50e6,
            ])
            if not ok:
                continue

        # ── RSI ──
        rsi = s.get("rsi")
        if p.get("rsi_max") is not None and (rsi is None or rsi > p["rsi_max"]):
            continue
        if p.get("rsi_min") is not None and (rsi is None or rsi < p["rsi_min"]):
            continue

        # ── Price ──
        price = s.get("price", 0)
        if p.get("price_min") is not None and price < p["price_min"]:
            continue
        if p.get("price_max") is not None and price > p["price_max"]:
            continue

        # ── 1-day change ──
        chg = s.get("change_1d", 0)
        if p.get("change_min") is not None and chg < p["change_min"]:
            continue
        if p.get("change_max") is not None and chg > p["change_max"]:
            continue

        # ── Last day red ──
        if p.get("last_day_red") and s.get("change_1d", 0) >= 0:
            continue

        # ── Consecutive red days ──
        min_crd = p.get("min_consec_red", 0)
        if min_crd and s.get("consec_red", 0) < min_crd:
            continue

        # ── Volume ratio ──
        if p.get("vol_ratio_min") is not None and s.get("vol_ratio", 0) < p["vol_ratio_min"]:
            continue

        # ── SMA positions (fixed: flag avoids break-vs-continue confusion) ──
        sma_ok = True
        for key, col in [("sma20_pos", "sma20"), ("sma50_pos", "sma50"), ("sma200_pos", "sma200")]:
            pos = p.get(key)
            if not pos:
                continue
            sma = s.get(col)
            if sma is None:
                continue
            if pos == "above" and price <= sma:
                sma_ok = False
                break
            if pos == "below" and price >= sma:
                sma_ok = False
                break
        if not sma_ok:
            continue

        # ── Beta ──
        beta = s.get("beta")
        if p.get("beta_max") is not None and beta is not None and beta > p["beta_max"]:
            continue
        if p.get("beta_min") is not None and beta is not None and beta < p["beta_min"]:
            continue

        # ── P/E ──
        pe = s.get("pe")
        if p.get("pe_max") is not None:
            if not pe or pe <= 0 or pe > p["pe_max"]:
                continue
        if p.get("pe_positive") and (not pe or pe <= 0):
            continue

        # ── Forward P/E ──
        fpe = s.get("forward_pe")
        if p.get("fpe_max") is not None:
            if not fpe or fpe <= 0 or fpe > p["fpe_max"]:
                continue

        # ── PEG ──
        peg = s.get("peg")
        if p.get("peg_max") is not None:
            if not peg or peg <= 0 or peg > p["peg_max"]:
                continue

        # ── P/S ──
        ps = s.get("ps_ratio")
        if p.get("ps_max") is not None:
            if ps is None or ps > p["ps_max"]:
                continue

        # ── P/B ──
        pb = s.get("price_to_book")
        if p.get("pb_max") is not None:
            if pb is None or pb > p["pb_max"]:
                continue

        # ── EV/EBITDA ──
        ev = s.get("ev_ebitda")
        if p.get("ev_ebitda_max") is not None:
            if ev is None or ev > p["ev_ebitda_max"]:
                continue

        # ── Dividend yield ──
        if p.get("div_min") is not None:
            dy = s.get("dividend_yield") or 0
            if dy < p["div_min"]:
                continue

        # ── Payout ratio (stored as %) ──
        if p.get("payout_ratio_max") is not None:
            pr = s.get("payout_ratio")
            if pr is not None and pr > p["payout_ratio_max"]:
                continue

        # ── ROA (stored as %) ──
        roa = s.get("roa")
        if p.get("roa_min") is not None:
            if roa is None or roa < p["roa_min"]:
                continue

        # ── ROE (stored as %) ──
        roe = s.get("roe")
        if p.get("roe_min") is not None:
            if roe is None or roe < p["roe_min"]:
                continue

        # ── Gross margin (stored as %) ──
        if p.get("gross_margin_min") is not None:
            gm = s.get("gross_margin")
            if gm is None or gm < p["gross_margin_min"]:
                continue

        # ── Operating margin (stored as %) ──
        if p.get("operating_margin_min") is not None:
            om = s.get("operating_margin")
            if om is None or om < p["operating_margin_min"]:
                continue

        # ── Net margin (stored as %) ──
        if p.get("net_margin_min") is not None:
            nm = s.get("net_margin")
            if nm is None or nm < p["net_margin_min"]:
                continue

        # ── Debt/Equity ──
        de = s.get("debt_to_equity")
        if p.get("de_max") is not None:
            if de is None or de > p["de_max"]:
                continue

        # ── Current ratio ──
        cr = s.get("current_ratio")
        if p.get("current_ratio_min") is not None:
            if cr is None or cr < p["current_ratio_min"]:
                continue

        # ── Quick ratio ──
        qr = s.get("quick_ratio")
        if p.get("quick_ratio_min") is not None:
            if qr is None or qr < p["quick_ratio_min"]:
                continue

        # ── Analyst rating (1=Strong Buy … 5=Strong Sell) ──
        ar = s.get("analyst_rating")
        if p.get("analyst_max") is not None:
            if ar is None or ar > p["analyst_max"]:
                continue

        # ── Distance from 52W high ──
        if p.get("from_52w_high_max") is not None:
            f52 = s.get("from_52w_high")
            if f52 is None or f52 < p["from_52w_high_max"]:
                continue

        out.append(s)
    return out


# ── Main entry point ──────────────────────────────────────────────────────────

def run_live_screener(params: dict, job_id: str, jobs: dict):
    try:
        universe_key = params.get("universe", "sp100")
        tickers      = list(dict.fromkeys(UNIVERSES.get(universe_key, UNIVERSES["sp100"])))

        jobs[job_id] = {"status": "running", "progress": 5, "message": "Downloading price data…"}

        end   = date.today()
        start = end - timedelta(days=120)

        raw = yf.download(
            tickers,
            start=str(start), end=str(end),
            auto_adjust=True, progress=False, threads=True,
        )

        jobs[job_id]["progress"] = 40
        jobs[job_id]["message"]  = "Computing technical indicators…"

        if isinstance(raw.columns, pd.MultiIndex):
            close  = raw["Close"]
            volume = raw["Volume"]
        else:
            name   = tickers[0]
            close  = raw[["Close"]].rename(columns={"Close": name})
            volume = raw[["Volume"]].rename(columns={"Volume": name})

        rsi_period = params.get("rsi_period", 14)
        stocks     = []

        for ticker in tickers:
            if ticker not in close.columns:
                continue
            prices = close[ticker].dropna()
            vols   = volume[ticker].dropna() if ticker in volume.columns else pd.Series(dtype=float)

            if len(prices) < 25:
                continue

            cur    = float(prices.iloc[-1])
            prev1  = float(prices.iloc[-2])
            prev5  = float(prices.iloc[-6])  if len(prices) > 5  else prev1
            prev22 = float(prices.iloc[-22]) if len(prices) > 21 else prev1

            rsi_s = _calc_rsi(prices, rsi_period)
            rsi   = float(rsi_s.iloc[-1])

            sma20  = float(prices.rolling(20).mean().iloc[-1])  if len(prices) >= 20  else None
            sma50  = float(prices.rolling(50).mean().iloc[-1])  if len(prices) >= 50  else None
            sma200 = float(prices.rolling(200).mean().iloc[-1]) if len(prices) >= 200 else None

            vol_today = float(vols.iloc[-1])  if len(vols) >= 1  else 0
            avg_vol   = float(vols.iloc[-20:].mean()) if len(vols) >= 20 else (float(vols.mean()) if len(vols) else 0)
            vol_ratio = vol_today / avg_vol if avg_vol > 0 else 1.0

            chg_vals = prices.pct_change().dropna().values
            crd = 0
            for c in reversed(chg_vals[:-1]):
                if c < 0:
                    crd += 1
                else:
                    break

            stocks.append({
                "ticker":     ticker,
                "price":      round(cur, 2),
                "change_1d":  round((cur / prev1  - 1) * 100, 2),
                "change_5d":  round((cur / prev5  - 1) * 100, 2),
                "change_1m":  round((cur / prev22 - 1) * 100, 2),
                "rsi":        round(rsi, 1),
                "sma20":      round(sma20,  2) if sma20  else None,
                "sma50":      round(sma50,  2) if sma50  else None,
                "sma200":     round(sma200, 2) if sma200 else None,
                "volume":     int(vol_today),
                "avg_volume": int(avg_vol),
                "vol_ratio":  round(vol_ratio, 2),
                "consec_red": crd,
            })

        jobs[job_id]["progress"] = 55
        jobs[job_id]["message"]  = "Fetching fundamental data…"

        fund = _get_fundamentals([s["ticker"] for s in stocks], job_id, jobs)

        for s in stocks:
            f    = fund.get(s["ticker"], {})
            mc   = f.get("market_cap")
            w52h = f.get("52w_high")
            w52l = f.get("52w_low")
            dy   = f.get("dividend_yield") or 0
            pr   = f.get("payout_ratio")

            s.update({
                "sector":           f.get("sector", ""),
                "industry":         f.get("industry", ""),
                "market_cap":       mc,
                "market_cap_fmt":   _fmt_cap(mc),
                "market_cap_tier":  _cap_tier(mc),
                # valuation
                "pe":               _r(f.get("pe")),
                "forward_pe":       _r(f.get("forward_pe")),
                "peg":              _r(f.get("peg")),
                "ps_ratio":         _r(f.get("ps_ratio")),
                "price_to_book":    _r(f.get("price_to_book")),
                "ev_ebitda":        _r(f.get("ev_ebitda")),
                # dividend
                "dividend_yield":   _r(dy * 100, 2),
                "payout_ratio":     _r((pr or 0) * 100, 1),
                # profitability (convert fractions → %)
                "roa":              _r((f.get("roa") or 0) * 100, 1),
                "roe":              _r((f.get("roe") or 0) * 100, 1),
                "gross_margin":     _r((f.get("gross_margin") or 0) * 100, 1),
                "operating_margin": _r((f.get("operating_margin") or 0) * 100, 1),
                "net_margin":       _r((f.get("net_margin") or 0) * 100, 1),
                # debt / liquidity
                "debt_to_equity":   _r(f.get("debt_to_equity")),
                "current_ratio":    _r(f.get("current_ratio")),
                "quick_ratio":      _r(f.get("quick_ratio")),
                # other
                "beta":             _r(f.get("beta")),
                "analyst_rating":   _r(f.get("analyst_rating")),
                "52w_high":         _r(w52h, 2),
                "52w_low":          _r(w52l, 2),
                "from_52w_high":    _r((s["price"] / w52h - 1) * 100, 1) if w52h else None,
                "from_52w_low":     _r((s["price"] / w52l - 1) * 100, 1) if w52l else None,
            })

        jobs[job_id]["progress"] = 85
        jobs[job_id]["message"]  = "Applying filters…"

        filtered = _apply_filters(stocks, params)
        filtered.sort(key=lambda x: x.get("rsi") or 99)

        jobs[job_id] = {
            "status":        "done",
            "progress":      100,
            "message":       f"Found {len(filtered)} matching stocks",
            "results":       filtered,
            "total_scanned": len(stocks),
        }

    except Exception as exc:
        import traceback
        jobs[job_id] = {
            "status":  "error",
            "message": str(exc),
            "detail":  traceback.format_exc(),
        }
