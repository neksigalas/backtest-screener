"""
Nightly stock screener report.

Scans all stocks in all universes, scores each one 0-100, and sends an HTML
email via Gmail SMTP. Designed to run via GitHub Actions every weekday evening.

Score breakdown (max 100):
  Technical  (40): RSI oversold (25) + consecutive red days (10) + volume spike (5)
  Fundamental(60): Forward P/E (15) + ROE (15) + Net Margin (15) + Low Debt (15)

Required environment variables (set as GitHub Secrets):
  GMAIL_USER         - Gmail address used as sender
  GMAIL_APP_PASSWORD - Gmail App Password (not your regular password)
  REPORT_TO          - Recipient email (defaults to GMAIL_USER if omitted)
"""

import json
import os
import smtplib
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd
import yfinance as yf

from universe import SP100, NASDAQ100, SP500_SAMPLE

CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

TOP_N = 20  # number of stocks shown in the email


# ── Helpers ───────────────────────────────────────────────────────────────────

def _calc_rsi(prices: pd.Series, period: int = 14) -> float:
    delta    = prices.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return float((100 - (100 / (1 + rs))).iloc[-1])


def _r(val, digits=1):
    if val is None:
        return None
    try:
        return round(float(val), digits)
    except Exception:
        return None


def _fmt(val, suffix="", none_str="—"):
    if val is None:
        return none_str
    return f"{val}{suffix}"


# ── Fundamentals ──────────────────────────────────────────────────────────────

def _fetch_one(ticker: str) -> tuple:
    try:
        info = yf.Ticker(ticker).info
        return ticker, {
            "sector":         info.get("sector", ""),
            "market_cap":     info.get("marketCap"),
            "pe":             info.get("trailingPE"),
            "forward_pe":     info.get("forwardPE"),
            "roe":            info.get("returnOnEquity"),
            "net_margin":     info.get("profitMargins"),
            "debt_to_equity": info.get("debtToEquity"),
            "analyst_rating": info.get("recommendationMean"),
        }
    except Exception:
        return ticker, {}


def _load_fundamentals(tickers: list) -> dict:
    today      = date.today().isoformat()
    cache_file = CACHE_DIR / f"fund_{today}.json"

    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            print(f"  Using cached fundamentals ({len(cached)} tickers)")
            return cached
        except Exception:
            pass

    for old in CACHE_DIR.glob("fund_*.json"):
        try:
            old.unlink()
        except Exception:
            pass
    cached = {}

    print(f"  Fetching fundamentals for {len(tickers)} tickers (~60s)...")
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_one, t): t for t in tickers}
        done = 0
        for future in as_completed(futures):
            ticker, data = future.result()
            cached[ticker] = data
            done += 1
            if done % 50 == 0:
                print(f"    {done}/{len(tickers)} done...")

    try:
        cache_file.write_text(json.dumps(cached), encoding="utf-8")
    except Exception:
        pass

    return cached


# ── Scoring ───────────────────────────────────────────────────────────────────

def compute_score(s: dict) -> int:
    score = 0

    # ── Technical (max 40) ──────────────────────────────────────────────────
    rsi = s.get("rsi", 50)
    if rsi <= 40:
        score += round(25 * (40 - rsi) / 40)

    score += {0: 0, 1: 3, 2: 6}.get(s.get("consec_red", 0), 10)

    vr = s.get("vol_ratio", 1.0)
    if   vr >= 2.0: score += 5
    elif vr >= 1.5: score += 3
    elif vr >= 1.2: score += 1

    # ── Fundamental (max 60) ────────────────────────────────────────────────
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

    # debt_to_equity from yfinance: 50 = 0.5 actual D/E ratio
    de = s.get("debt_to_equity")
    if   de is None: score += 5   # unknown → neutral
    elif de <= 30:   score += 15
    elif de <= 70:   score += 10
    elif de <= 150:  score += 5

    return min(100, score)


# ── Screener ──────────────────────────────────────────────────────────────────

def run_screener() -> list:
    all_tickers = list(dict.fromkeys(SP100 + NASDAQ100 + SP500_SAMPLE))
    print(f"Scanning {len(all_tickers)} unique tickers...")

    end   = date.today()
    start = end - timedelta(days=120)

    print("Downloading price data (bulk)...")
    raw = yf.download(
        all_tickers,
        start=str(start), end=str(end),
        auto_adjust=True, progress=False, threads=True,
    )

    if isinstance(raw.columns, pd.MultiIndex):
        close  = raw["Close"]
        volume = raw["Volume"]
    else:
        t      = all_tickers[0]
        close  = raw[["Close"]].rename(columns={"Close": t})
        volume = raw[["Volume"]].rename(columns={"Volume": t})

    print("Computing technical indicators...")
    stocks = []
    for ticker in all_tickers:
        if ticker not in close.columns:
            continue
        prices = close[ticker].dropna()
        vols   = volume[ticker].dropna() if ticker in volume.columns else pd.Series(dtype=float)

        if len(prices) < 25:
            continue

        cur   = float(prices.iloc[-1])
        prev1 = float(prices.iloc[-2])

        rsi = _calc_rsi(prices)

        vol_today = float(vols.iloc[-1]) if len(vols) >= 1 else 0
        avg_vol   = float(vols.iloc[-20:].mean()) if len(vols) >= 20 else (float(vols.mean()) if len(vols) else 1)
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
            "change_1d":  round((cur / prev1 - 1) * 100, 2),
            "rsi":        round(rsi, 1),
            "vol_ratio":  round(vol_ratio, 2),
            "consec_red": crd,
        })

    print("Fetching fundamental data...")
    fund = _load_fundamentals([s["ticker"] for s in stocks])

    for s in stocks:
        f   = fund.get(s["ticker"], {})
        roe = f.get("roe")
        nm  = f.get("net_margin")
        s.update({
            "sector":         f.get("sector") or "—",
            "pe":             _r(f.get("pe")),
            "forward_pe":     _r(f.get("forward_pe")),
            "roe":            _r((roe or 0) * 100, 1) if roe is not None else None,
            "net_margin":     _r((nm  or 0) * 100, 1) if nm  is not None else None,
            "debt_to_equity": _r(f.get("debt_to_equity")),
            "analyst_rating": _r(f.get("analyst_rating")),
        })
        s["score"] = compute_score(s)

    return sorted(stocks, key=lambda x: x["score"], reverse=True)


# ── HTML Email builder ────────────────────────────────────────────────────────

def _score_color(score: int) -> str:
    if score >= 70: return "#22c55e"
    if score >= 50: return "#f59e0b"
    return "#ef4444"


def _rsi_color(rsi) -> str:
    if rsi is None:  return "#6b7280"
    if rsi <= 25:    return "#ef4444"
    if rsi <= 35:    return "#f97316"
    if rsi <= 45:    return "#f59e0b"
    return "#6b7280"


def _chg_color(chg: float) -> str:
    return "#22c55e" if chg >= 0 else "#ef4444"


_ANALYST_LABEL = {1: "Strong Buy", 2: "Buy", 3: "Hold", 4: "Sell", 5: "Strong Sell"}


def _table_row(rank: int, s: dict) -> str:
    score = s.get("score", 0)
    rsi   = s.get("rsi")
    chg   = s.get("change_1d", 0)
    ar    = s.get("analyst_rating")
    ar_str = _ANALYST_LABEL.get(round(ar), "—") if ar else "—"

    return f"""
      <tr style="border-bottom:1px solid #1e293b;">
        <td style="padding:10px 8px;color:#64748b;font-size:13px;">{rank}</td>
        <td style="padding:10px 8px;font-weight:700;font-size:15px;color:#f1f5f9;">{s['ticker']}</td>
        <td style="padding:10px 8px;color:#f1f5f9;">${s['price']}</td>
        <td style="padding:10px 8px;color:{_chg_color(chg)};font-weight:600;">{'+' if chg >= 0 else ''}{chg}%</td>
        <td style="padding:10px 8px;color:{_rsi_color(rsi)};font-weight:700;">{rsi if rsi is not None else '—'}</td>
        <td style="padding:10px 8px;">
          <span style="background:{_score_color(score)};color:#000;padding:3px 10px;border-radius:12px;font-weight:700;font-size:13px;">{score}</span>
        </td>
        <td style="padding:10px 8px;color:#94a3b8;font-size:13px;">{_fmt(s.get('forward_pe'))}</td>
        <td style="padding:10px 8px;color:#94a3b8;font-size:13px;">{_fmt(s.get('roe'), '%')}</td>
        <td style="padding:10px 8px;color:#94a3b8;font-size:13px;">{_fmt(s.get('net_margin'), '%')}</td>
        <td style="padding:10px 8px;color:#94a3b8;font-size:13px;">{s.get('sector', '—')}</td>
        <td style="padding:10px 8px;color:#94a3b8;font-size:13px;">{ar_str}</td>
      </tr>"""


def build_html(stocks: list, today: str) -> str:
    top      = stocks[:TOP_N]
    oversold = [s for s in stocks if (s.get("rsi") or 99) < 30]
    n_total  = len(stocks)

    rows_html = "\n".join(_table_row(i + 1, s) for i, s in enumerate(top))

    alert_html = ""
    if oversold:
        items = ", ".join(
            f"<strong>{s['ticker']}</strong>&nbsp;(RSI&nbsp;{s['rsi']})"
            for s in oversold[:10]
        )
        more = f" +{len(oversold)-10} more" if len(oversold) > 10 else ""
        alert_html = f"""
    <div style="background:#450a0a;border-left:4px solid #ef4444;padding:16px;border-radius:8px;margin-bottom:20px;">
      <div style="color:#fca5a5;font-weight:700;margin-bottom:8px;">
        🚨 EXTREME OVERSOLD — RSI &lt; 30 &nbsp;({len(oversold)} stocks)
      </div>
      <div style="color:#fecaca;font-size:14px;line-height:1.8;">{items}{more}</div>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="el">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Stock Report {today}</title>
</head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#f1f5f9;">
  <div style="max-width:960px;margin:0 auto;padding:24px 16px;">

    <!-- Header -->
    <div style="background:linear-gradient(135deg,#1e3a5f 0%,#1e293b 100%);border-radius:12px;padding:24px 28px;margin-bottom:24px;">
      <div style="font-size:24px;font-weight:700;color:#60a5fa;margin-bottom:6px;">📊 Daily Stock Report</div>
      <div style="color:#94a3b8;font-size:14px;">
        {today}
        &nbsp;&nbsp;|&nbsp;&nbsp;
        {n_total} stocks scanned
        &nbsp;&nbsp;|&nbsp;&nbsp;
        Top {TOP_N} by composite score
      </div>
    </div>

    {alert_html}

    <!-- Score legend -->
    <div style="background:#1e293b;border-radius:8px;padding:12px 16px;margin-bottom:20px;font-size:12px;color:#64748b;line-height:1.7;">
      <strong style="color:#94a3b8;">Score (0–100) =</strong>
      RSI oversold 25pts &nbsp;+&nbsp;
      Consec. red days 10pts &nbsp;+&nbsp;
      Volume spike 5pts &nbsp;+&nbsp;
      Forward P/E 15pts &nbsp;+&nbsp;
      ROE 15pts &nbsp;+&nbsp;
      Net Margin 15pts &nbsp;+&nbsp;
      Low Debt 15pts
      &nbsp;&nbsp;|&nbsp;&nbsp;
      <span style="background:#22c55e;color:#000;padding:1px 6px;border-radius:4px;">≥70</span> strong &nbsp;
      <span style="background:#f59e0b;color:#000;padding:1px 6px;border-radius:4px;">50-69</span> moderate &nbsp;
      <span style="background:#ef4444;color:#fff;padding:1px 6px;border-radius:4px;">&lt;50</span> weak
    </div>

    <!-- Table -->
    <div style="overflow-x:auto;border-radius:12px;">
      <table style="width:100%;border-collapse:collapse;background:#1e293b;">
        <thead>
          <tr style="background:#0f172a;">
            <th style="padding:12px 8px;text-align:left;color:#64748b;font-size:11px;font-weight:600;letter-spacing:.05em;">#</th>
            <th style="padding:12px 8px;text-align:left;color:#64748b;font-size:11px;font-weight:600;letter-spacing:.05em;">TICKER</th>
            <th style="padding:12px 8px;text-align:left;color:#64748b;font-size:11px;font-weight:600;letter-spacing:.05em;">PRICE</th>
            <th style="padding:12px 8px;text-align:left;color:#64748b;font-size:11px;font-weight:600;letter-spacing:.05em;">1D %</th>
            <th style="padding:12px 8px;text-align:left;color:#64748b;font-size:11px;font-weight:600;letter-spacing:.05em;">RSI</th>
            <th style="padding:12px 8px;text-align:left;color:#64748b;font-size:11px;font-weight:600;letter-spacing:.05em;">SCORE</th>
            <th style="padding:12px 8px;text-align:left;color:#64748b;font-size:11px;font-weight:600;letter-spacing:.05em;">FWD P/E</th>
            <th style="padding:12px 8px;text-align:left;color:#64748b;font-size:11px;font-weight:600;letter-spacing:.05em;">ROE</th>
            <th style="padding:12px 8px;text-align:left;color:#64748b;font-size:11px;font-weight:600;letter-spacing:.05em;">NET MAR</th>
            <th style="padding:12px 8px;text-align:left;color:#64748b;font-size:11px;font-weight:600;letter-spacing:.05em;">SECTOR</th>
            <th style="padding:12px 8px;text-align:left;color:#64748b;font-size:11px;font-weight:600;letter-spacing:.05em;">ANALYST</th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
    </div>

    <!-- Footer -->
    <div style="margin-top:24px;padding:16px;background:#1e293b;border-radius:8px;font-size:12px;color:#475569;line-height:1.8;">
      <div>⚠️ Αυτό το report είναι για ενημερωτικούς σκοπούς μόνο. Δεν αποτελεί επενδυτική συμβουλή.</div>
      <div>Data: Yahoo Finance &nbsp;|&nbsp; Engine: backtest-screener</div>
    </div>

  </div>
</body>
</html>"""


# ── Email sender ──────────────────────────────────────────────────────────────

def send_email(html: str, today: str):
    gmail_user = os.environ["GMAIL_USER"]
    gmail_pass = os.environ["GMAIL_APP_PASSWORD"]
    to_addr    = os.environ.get("REPORT_TO", gmail_user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📊 Stock Report – {today}"
    msg["From"]    = gmail_user
    msg["To"]      = to_addr
    msg.attach(MIMEText(html, "html", "utf-8"))

    print(f"Sending email to {to_addr}...")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, to_addr, msg.as_string())
    print("Email sent!")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    today_str  = date.today().strftime("%A, %d %B %Y")
    today_subj = date.today().strftime("%d/%m/%Y")

    print(f"\n{'='*55}")
    print(f"  Daily Stock Report  —  {today_str}")
    print(f"{'='*55}\n")

    stocks = run_screener()

    print(f"\nTop 5 stocks by score:")
    for s in stocks[:5]:
        print(f"  {s['ticker']:6s}  RSI={s['rsi']:5.1f}  Score={s['score']:3d}  {s.get('sector','—')}")

    oversold_count = sum(1 for s in stocks if (s.get("rsi") or 99) < 30)
    print(f"\nOversold (RSI < 30): {oversold_count} stocks")

    html = build_html(stocks, today_str)

    if os.environ.get("GMAIL_USER"):
        send_email(html, today_subj)
    else:
        out = Path("report.html")
        out.write_text(html, encoding="utf-8")
        print(f"\nNo GMAIL_USER set — saved preview to {out.resolve()}")

    print("\nDone.")
