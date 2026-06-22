"""
Backtest runner — downloads 3 years of data, runs all 4 strategies,
builds a comparison HTML email and sends it via Gmail SMTP.

Usage:
    python -m backtest.run
    GMAIL_USER=... GMAIL_APP_PASSWORD=... REPORT_TO=... python -m backtest.run

Known limitations (noted in email):
  - Stock/crypto fundamentals (P/E, ROE, market-cap rank …) use TODAY's live
    data — look-ahead bias for those signals only.  Technical signals are
    fully causal.
"""

from __future__ import annotations

import json
import math
import os
import smtplib
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))

from universe import SP100, NASDAQ100, SP500_SAMPLE
from etf_trader.universe import ETF_UNIVERSE, LEVERAGE
from backtest.engine import precompute_signals, simulate, calc_stats, SimResult

# ── Constants ─────────────────────────────────────────────────────────────────

BACKTEST_START = "2022-01-03"   # first NYSE trading day of 2022
DOWNLOAD_START = "2021-07-01"   # extra warm-up for rolling windows

STOCK_UNIVERSE = list(dict.fromkeys(SP100 + NASDAQ100 + SP500_SAMPLE))
CRYPTO_UNIVERSE = [
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
    "ADA-USD", "AVAX-USD", "DOGE-USD", "TRX-USD", "DOT-USD",
    "LINK-USD", "MATIC-USD", "SHIB-USD", "UNI-USD", "LTC-USD",
    "BCH-USD", "ATOM-USD", "XLM-USD", "NEAR-USD", "ICP-USD",
    "ETC-USD", "APT-USD", "FIL-USD", "HBAR-USD", "ALGO-USD",
    "GRT-USD", "AAVE-USD", "MKR-USD", "SNX-USD", "CRV-USD",
]


# ── Scoring functions (mirrors each strategy's live scoring) ──────────────────

def _r(val):
    try:
        v = float(val)
        return None if math.isnan(v) else v
    except Exception:
        return None


def score_original_stocks(row: dict, fund: dict) -> int:
    score = 0
    rsi = _r(row.get("rsi", 50)) or 50
    if rsi <= 40:
        score += round(25 * (40 - rsi) / 40)
    score += {0: 0, 1: 3, 2: 6}.get(int(row.get("consec_red", 0)), 10)
    vr = _r(row.get("vol_ratio", 1)) or 1
    if vr >= 2.0: score += 5
    elif vr >= 1.5: score += 3
    elif vr >= 1.2: score += 1
    fpe = _r(fund.get("forward_pe"))
    if fpe and fpe > 0:
        if 5 <= fpe <= 15: score += 15
        elif 15 < fpe <= 25: score += 10
        elif 25 < fpe <= 35: score += 5
    roe = _r(fund.get("roe"))
    if roe is not None:
        if roe >= 25: score += 15
        elif roe >= 15: score += 10
        elif roe >= 5: score += 5
    nm = _r(fund.get("net_margin"))
    if nm is not None:
        if nm >= 20: score += 15
        elif nm >= 10: score += 10
        elif nm >= 5: score += 5
    de = _r(fund.get("debt_to_equity"))
    if de is None: score += 5
    elif de <= 30: score += 15
    elif de <= 70: score += 10
    elif de <= 150: score += 5
    return min(100, score)


def score_new_tactic(row: dict, fund: dict) -> int:
    score = 0
    tg = bool(row.get("today_green", False))
    rb = int(row.get("reds_before", 0))
    if tg:
        if rb >= 3: score += 20
        elif rb == 2: score += 14
        elif rb == 1: score += 8
    rsi = _r(row.get("rsi", 50)) or 50
    if rsi <= 25: score += 20
    elif rsi <= 32: score += 16
    elif rsi <= 40: score += 10
    elif rsi <= 50: score += 4
    if bool(row.get("macd_cross", False)): score += 10
    vr = _r(row.get("vol_ratio", 1)) or 1
    if vr >= 2.0: score += 5
    elif vr >= 1.5: score += 3
    elif vr >= 1.2: score += 1
    if bool(row.get("bull", True)): score += 10
    else: score -= 5
    fpe = _r(fund.get("forward_pe"))
    if fpe and fpe > 0:
        if 5 <= fpe <= 15: score += 15
        elif 15 < fpe <= 25: score += 10
        elif 25 < fpe <= 35: score += 5
    roe = _r(fund.get("roe"))
    if roe is not None:
        if roe >= 25: score += 15
        elif roe >= 15: score += 10
        elif roe >= 5: score += 5
    nm = _r(fund.get("net_margin"))
    if nm is not None:
        if nm >= 20: score += 15
        elif nm >= 10: score += 10
        elif nm >= 5: score += 5
    return min(100, max(0, score))


def score_crypto(row: dict, fund: dict) -> int:
    score = 0
    rsi = _r(row.get("rsi", 50)) or 50
    if rsi <= 30: score += 25
    elif rsi <= 40: score += 15
    elif rsi <= 50: score += 5
    score += {0: 0, 1: 3, 2: 6}.get(int(row.get("consec_red", 0)), 10)
    vr = _r(row.get("vol_ratio", 1)) or 1
    if vr >= 2.0: score += 5
    elif vr >= 1.5: score += 3
    elif vr >= 1.2: score += 1
    rank = fund.get("market_cap_rank")
    if rank:
        if rank <= 5: score += 15
        elif rank <= 10: score += 10
        elif rank <= 20: score += 6
        elif rank <= 50: score += 2
    vol_mc = _r(fund.get("vol_to_mcap"))
    if vol_mc:
        if vol_mc >= 0.10: score += 15
        elif vol_mc >= 0.05: score += 10
        elif vol_mc >= 0.02: score += 5
    chg7 = _r(row.get("chg7d"))
    if chg7 is not None:
        if -20 <= chg7 <= -5: score += 15
        elif -5 < chg7 <= 0: score += 8
        elif -40 <= chg7 < -20: score += 5
    ath = _r(row.get("ath_pct"))
    if ath is not None:
        if -50 <= ath <= -20: score += 15
        elif -70 <= ath < -50: score += 8
        elif -20 < ath <= 0: score += 5
    return min(100, score)


def score_etf(row: dict, fund: dict) -> int:
    score = 0
    rsi = _r(row.get("rsi", 50)) or 50
    if rsi <= 30: score += 25
    elif rsi <= 40: score += 15
    elif rsi <= 50: score += 5
    score += {0: 0, 1: 3, 2: 6}.get(int(row.get("consec_red", 0)), 10)
    vr = _r(row.get("vol_ratio", 1)) or 1
    if vr >= 2.0: score += 5
    elif vr >= 1.5: score += 3
    elif vr >= 1.2: score += 1
    dd = _r(row.get("ath_pct"))
    if dd is not None:
        dd_abs = abs(dd)
        if 15 <= dd_abs <= 30: score += 20
        elif 30 < dd_abs <= 50: score += 12
        elif 8 <= dd_abs < 15: score += 6
    lev = fund.get("leverage", 1)
    if lev >= 3: score += 20
    elif lev == 2: score += 12
    else: score += 4
    liq = _r(fund.get("liquidity_m"))
    if liq:
        if liq >= 100: score += 20
        elif liq >= 25: score += 14
        elif liq >= 5: score += 8
        elif liq >= 1: score += 3
    return min(100, score)


# ── Fundamentals fetchers ─────────────────────────────────────────────────────

def _fetch_stock_fund(ticker: str) -> tuple[str, dict]:
    try:
        info = yf.Ticker(ticker).info
        roe = info.get("returnOnEquity")
        nm  = info.get("profitMargins")
        return ticker, {
            "forward_pe":     info.get("forwardPE"),
            "roe":            (roe or 0) * 100 if roe is not None else None,
            "net_margin":     (nm  or 0) * 100 if nm  is not None else None,
            "debt_to_equity": info.get("debtToEquity"),
        }
    except Exception:
        return ticker, {}


def fetch_stock_fundamentals(tickers: list[str]) -> dict[str, dict]:
    print(f"  Fetching stock fundamentals for {len(tickers)} tickers (~90s)…")
    data: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_fetch_stock_fund, t): t for t in tickers}
        done = 0
        for fut in as_completed(futs):
            tk, d = fut.result()
            data[tk] = d
            done += 1
            if done % 50 == 0:
                print(f"    {done}/{len(tickers)}…")
    return data


def fetch_crypto_fundamentals() -> dict[str, dict]:
    """Static crypto fundamentals from CoinGecko (market-cap rank, vol/mcap)."""
    print("  Fetching crypto fundamentals from CoinGecko…")
    url = (
        "https://api.coingecko.com/api/v3/coins/markets"
        "?vs_currency=usd&order=market_cap_desc&per_page=100&page=1"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            coins = json.loads(r.read().decode())
    except Exception as e:
        print(f"    CoinGecko error: {e} — using empty fundamentals")
        return {}

    stables = {"usdt", "usdc", "busd", "dai", "tusd", "usdp", "usdd", "frax", "fdusd"}
    data: dict[str, dict] = {}
    for c in coins:
        sym = c.get("symbol", "").upper()
        if sym.lower() in stables:
            continue
        mc  = c.get("market_cap") or 0
        vol = c.get("total_volume") or 0
        data[f"{sym}-USD"] = {
            "market_cap_rank": c.get("market_cap_rank"),
            "vol_to_mcap":     vol / mc if mc > 0 else None,
        }
    return data


def build_etf_fundamentals(
    close_df: pd.DataFrame,
    volume_df: pd.DataFrame,
) -> dict[str, dict]:
    """Leverage from universe dict; liquidity from downloaded price × volume."""
    data: dict[str, dict] = {}
    for tkr in ETF_UNIVERSE:
        if tkr not in close_df.columns:
            continue
        prices = close_df[tkr].dropna()
        vols   = volume_df[tkr].dropna() if tkr in volume_df.columns else pd.Series(dtype=float)
        avg_vol = float(vols.iloc[-20:].mean()) if len(vols) >= 20 else (float(vols.mean()) if len(vols) else 0)
        price   = float(prices.iloc[-1]) if len(prices) else 0
        data[tkr] = {
            "leverage":    LEVERAGE.get(tkr, 1),
            "liquidity_m": round(avg_vol * price / 1_000_000, 1),
        }
    return data


# ── Email report ──────────────────────────────────────────────────────────────

def _medal(rank: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"#{rank}")


def _pnl_color(v: float) -> str:
    return "#22c55e" if v >= 0 else "#ef4444"


def _build_chart_url(results: list[SimResult]) -> str:
    """Combined equity-curve chart: one line per strategy, monthly resampled."""
    colors = ["#3b82f6", "#22c55e", "#f59e0b", "#a855f7"]
    labels_col = {
        "3 Best Stocks (Original)": colors[0],
        "New Tactic 3 Best Stocks": colors[1],
        "3 Best Crypto":            colors[2],
        "3 Best ETF":               colors[3],
    }

    # Build monthly date range for common x-axis
    start = pd.Timestamp(BACKTEST_START)
    end   = pd.Timestamp(date.today())
    months = pd.date_range(start, end, freq="MS").strftime("%Y-%m").tolist()

    datasets = []
    for res, color in zip(results, colors):
        if not res.equity:
            monthly = [0] * len(months)
        else:
            eq_ser = pd.Series(
                {pd.Timestamp(d): v for d, v in res.equity}
            ).sort_index()
            # Forward-fill to cover every month
            idx_m  = pd.date_range(start, end, freq="MS")
            eq_monthly = eq_ser.reindex(idx_m, method="ffill").fillna(0)
            monthly = [round(v, 2) for v in eq_monthly.values]

        datasets.append({
            "label":           res.strategy_name,
            "data":            monthly,
            "borderColor":     color,
            "backgroundColor": color + "1a",
            "fill":            False,
            "tension":         0.3,
            "pointRadius":     2,
            "borderWidth":     2,
        })

    config = {
        "type": "line",
        "data": {"labels": months, "datasets": datasets},
        "options": {
            "plugins": {
                "legend": {"labels": {"color": "#94a3b8", "font": {"size": 11}}},
            },
            "scales": {
                "x": {"ticks": {"color": "#64748b", "maxTicksLimit": 12}, "grid": {"color": "#1e293b"}},
                "y": {
                    "ticks": {"color": "#94a3b8"},
                    "grid":  {"color": "#334155"},
                    "title": {"display": True, "text": "Cumulative P&L ($)", "color": "#64748b"},
                },
            },
        },
    }
    encoded = urllib.parse.quote(json.dumps(config, separators=(",", ":")))
    return f"https://quickchart.io/chart?c={encoded}&width=860&height=320&backgroundColor=%231e293b"


def _stat_cell(val: str, color: str = "#f1f5f9") -> str:
    return f'<td style="padding:10px 12px;text-align:center;font-weight:600;color:{color};">{val}</td>'


def _trade_rows(trades: list, n: int = 5, best: bool = True) -> str:
    srt = sorted(trades, key=lambda t: t.pnl_pct, reverse=best)[:n]
    rows = ""
    for t in srt:
        c = _pnl_color(t.pnl_pct)
        rows += f"""<tr>
          <td style="padding:6px 10px;color:#f1f5f9;font-weight:700;">{t.ticker}</td>
          <td style="padding:6px 10px;color:#64748b;font-size:11px;">{t.entry_date} → {t.exit_date}</td>
          <td style="padding:6px 10px;color:#64748b;font-size:11px;">{t.holding_days}d</td>
          <td style="padding:6px 10px;font-weight:700;color:{c};">{'+' if t.pnl_pct>=0 else ''}{t.pnl_pct}%</td>
          <td style="padding:6px 10px;font-weight:600;color:{c};">{'+' if t.pnl>=0 else ''}${t.pnl:.2f}</td>
          <td style="padding:6px 10px;font-size:11px;color:#475569;">{t.exit_reason}</td>
        </tr>"""
    return rows or '<tr><td colspan="6" style="padding:10px;color:#475569;">—</td></tr>'


def build_email(results: list[SimResult]) -> str:
    today_str = date.today().strftime("%d %B %Y")
    stats_all = [calc_stats(r) for r in results]
    chart_url = _build_chart_url(results)

    # Rank by total P&L
    ranked = sorted(zip(results, stats_all), key=lambda x: x[1]["total_pnl"], reverse=True)

    # ── Comparison summary table ──────────────────────────────────────────────
    headers = ["Στρατηγική", "P&L ($)", "Trades", "Win Rate", "Avg Win", "Avg Loss",
               "Max DD ($)", "Avg Hold (d)"]
    header_row = "".join(
        f'<th style="padding:10px 12px;color:#64748b;font-size:11px;text-align:center;">{h}</th>'
        for h in headers
    )

    strategy_colors = {
        "3 Best Stocks (Original)": "#3b82f6",
        "New Tactic 3 Best Stocks": "#22c55e",
        "3 Best Crypto":            "#f59e0b",
        "3 Best ETF":               "#a855f7",
    }

    table_rows = ""
    for rank, (res, st) in enumerate(ranked, 1):
        sc  = strategy_colors.get(res.strategy_name, "#94a3b8")
        pnl = st["total_pnl"]
        pc  = _pnl_color(pnl)
        wr  = st["win_rate"]
        wrc = "#22c55e" if wr >= 50 else "#ef4444"
        table_rows += f"""<tr style="border-bottom:1px solid #0f172a;">
          <td style="padding:10px 12px;">
            <span style="font-size:16px;">{_medal(rank)}</span>
            <span style="color:{sc};font-weight:700;margin-left:6px;">{res.strategy_name}</span>
          </td>
          {_stat_cell(f"{'+'if pnl>=0 else ''}${pnl:.2f}", pc)}
          {_stat_cell(str(st["total_trades"]))}
          {_stat_cell(f"{wr}%", wrc)}
          {_stat_cell(f"+{st['avg_win']}%", "#22c55e")}
          {_stat_cell(f"{st['avg_loss']}%", "#ef4444")}
          {_stat_cell(f"${st['max_drawdown']:.2f}", "#ef4444" if st["max_drawdown"] < 0 else "#64748b")}
          {_stat_cell(str(st["avg_hold"]))}
        </tr>"""

    # ── Per-strategy detail sections ──────────────────────────────────────────
    detail_sections = ""
    for res, st in zip(results, stats_all):
        sc     = strategy_colors.get(res.strategy_name, "#94a3b8")
        trades = res.trades
        detail_sections += f"""
        <div style="background:#1e293b;border-radius:12px;padding:20px;margin-bottom:20px;border-left:4px solid {sc};">
          <div style="font-size:15px;font-weight:700;color:{sc};margin-bottom:14px;">{res.strategy_name}</div>
          <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px;">
            <div style="background:#0f172a;border-radius:8px;padding:10px 16px;text-align:center;flex:1;min-width:90px;">
              <div style="font-size:18px;font-weight:700;color:{_pnl_color(st['total_pnl'])};">{'+'if st['total_pnl']>=0 else ''}${st['total_pnl']:.2f}</div>
              <div style="font-size:10px;color:#475569;">Total P&L</div>
            </div>
            <div style="background:#0f172a;border-radius:8px;padding:10px 16px;text-align:center;flex:1;min-width:90px;">
              <div style="font-size:18px;font-weight:700;color:#f1f5f9;">{st['total_trades']}</div>
              <div style="font-size:10px;color:#475569;">Trades</div>
            </div>
            <div style="background:#0f172a;border-radius:8px;padding:10px 16px;text-align:center;flex:1;min-width:90px;">
              <div style="font-size:18px;font-weight:700;color:{'#22c55e' if st['win_rate']>=50 else '#ef4444'};">{st['win_rate']}%</div>
              <div style="font-size:10px;color:#475569;">Win Rate</div>
            </div>
            <div style="background:#0f172a;border-radius:8px;padding:10px 16px;text-align:center;flex:1;min-width:90px;">
              <div style="font-size:18px;font-weight:700;color:#f1f5f9;">{st['avg_hold']}d</div>
              <div style="font-size:10px;color:#475569;">Avg Hold</div>
            </div>
          </div>

          {"" if not trades else f'''
          <div style="font-size:12px;color:#64748b;margin-bottom:6px;font-weight:600;">🏆 Top 5 Trades</div>
          <div style="overflow-x:auto;margin-bottom:12px;">
            <table style="width:100%;border-collapse:collapse;font-size:12px;">
              {_trade_rows(trades, n=5, best=True)}
            </table>
          </div>
          <div style="font-size:12px;color:#64748b;margin-bottom:6px;font-weight:600;">💔 Worst 5 Trades</div>
          <div style="overflow-x:auto;">
            <table style="width:100%;border-collapse:collapse;font-size:12px;">
              {_trade_rows(trades, n=5, best=False)}
            </table>
          </div>'''}
        </div>"""

    winner = ranked[0][0].strategy_name if ranked else "—"
    winner_pnl = ranked[0][1]["total_pnl"] if ranked else 0

    return f"""<!DOCTYPE html>
<html lang="el">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Backtest Report — {today_str}</title></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#f1f5f9;">
<div style="max-width:920px;margin:0 auto;padding:24px 16px;">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#0c1a2e,#0f172a);border-radius:14px;padding:28px;margin-bottom:24px;border:1px solid #1e40af33;">
    <div style="font-size:11px;color:#60a5fa;font-weight:600;letter-spacing:.1em;text-transform:uppercase;">3-Year Backtest Report</div>
    <div style="font-size:26px;font-weight:800;color:#f1f5f9;margin-top:6px;">📊 Paper Trading Strategy Comparison</div>
    <div style="color:#94a3b8;margin-top:6px;font-size:14px;">{BACKTEST_START} → {date.today().isoformat()} &nbsp;|&nbsp; {today_str}</div>
    <div style="margin-top:12px;background:#1e293b;border-radius:8px;padding:12px 16px;">
      <span style="color:#fbbf24;font-size:13px;">🏆 Winner: <strong style="color:#f1f5f9;">{winner}</strong> with <strong style="color:#22c55e;">{'+'if winner_pnl>=0 else ''}${winner_pnl:.2f}</strong> cumulative P&L</span>
    </div>
  </div>

  <!-- Comparison table -->
  <div style="background:#1e293b;border-radius:12px;padding:20px;margin-bottom:24px;">
    <div style="font-size:14px;font-weight:600;color:#94a3b8;margin-bottom:14px;">📋 Σύγκριση Στρατηγικών</div>
    <div style="overflow-x:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead><tr style="background:#0f172a;">{header_row}</tr></thead>
        <tbody>{table_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- Combined equity curve -->
  <div style="background:#1e293b;border-radius:12px;padding:20px;margin-bottom:24px;">
    <div style="font-size:14px;font-weight:600;color:#94a3b8;margin-bottom:12px;">📈 Equity Curves — Cumulative P&L (Jan 2022 → σήμερα)</div>
    <img src="{chart_url}" alt="Equity Curves" style="width:100%;max-width:880px;border-radius:8px;display:block;">
  </div>

  <!-- Per-strategy detail -->
  <div style="font-size:14px;font-weight:600;color:#94a3b8;margin-bottom:14px;">🔍 Ανάλυση ανά Στρατηγική</div>
  {detail_sections}

  <!-- Disclaimer -->
  <div style="padding:16px;background:#1e293b;border-radius:10px;font-size:11px;color:#475569;line-height:1.9;">
    <div style="font-weight:700;color:#64748b;margin-bottom:6px;">⚠️ Σημαντικές παρατηρήσεις για το backtest:</div>
    <div>• Τεχνικά σήματα (RSI, MACD, Volume, κλπ.) υπολογίστηκαν causally — χωρίς look-ahead bias.</div>
    <div>• Fundamentals (P/E, ROE, net margin, debt/equity, market-cap rank) χρησιμοποιούν τα <strong>σημερινά</strong> δεδομένα, όχι ιστορικά — look-ahead bias για αυτά τα σήματα μόνο.</div>
    <div>• Εκτέλεση στην τιμή κλεισίματος (όχι intraday TP/SL) — ελαφρά δυσμενές για το σύστημα.</div>
    <div>• Paper trading, χωρίς spread/slippage/φόρους. Τα πραγματικά αποτελέσματα θα διαφέρουν.</div>
    <div>• Κάθε θέση: $100 επένδυση, max 3 θέσεις ανά πάσα στιγμή. TP +5% / SL -7%.</div>
  </div>

</div>
</body>
</html>"""


# ── Main orchestrator ─────────────────────────────────────────────────────────

def main():
    end_date   = date.today()
    start_dl   = DOWNLOAD_START

    # ── 1. Download all price data ─────────────────────────────────────────────
    print("=" * 60)
    print("BACKTEST: downloading price data…")
    print("=" * 60)

    all_tickers = list(dict.fromkeys(
        ["SPY"] + STOCK_UNIVERSE + CRYPTO_UNIVERSE + ETF_UNIVERSE
    ))
    print(f"Tickers: {len(all_tickers)} total — downloading {start_dl} → {end_date}…")
    raw = yf.download(
        all_tickers,
        start=start_dl, end=str(end_date),
        auto_adjust=True, progress=False, threads=True,
    )
    if isinstance(raw.columns, pd.MultiIndex):
        close_df  = raw["Close"]
        volume_df = raw["Volume"]
    else:
        t         = all_tickers[0]
        close_df  = raw[["Close"]].rename(columns={"Close": t})
        volume_df = raw[["Volume"]].rename(columns={"Volume": t})

    spy_close = close_df["SPY"].dropna() if "SPY" in close_df.columns else pd.Series(dtype=float)
    print(f"Downloaded {len(close_df)} trading days, {close_df.shape[1]} columns.")

    # ── 2. Pre-compute signals ─────────────────────────────────────────────────
    print("\nPre-computing technical signals…")
    signals_all = precompute_signals(close_df, volume_df, spy_close)
    print(f"Signals ready for {len(signals_all)} tickers.")

    # ── 3. Fetch fundamentals ──────────────────────────────────────────────────
    print("\nFetching fundamentals…")
    stock_tickers_in_data = [t for t in STOCK_UNIVERSE if t in signals_all]
    stock_fund  = fetch_stock_fundamentals(stock_tickers_in_data)
    crypto_fund = fetch_crypto_fundamentals()
    etf_fund    = build_etf_fundamentals(close_df, volume_df)

    # ── 4. Run simulations ─────────────────────────────────────────────────────
    print("\nRunning simulations…")

    configs = [
        ("3 Best Stocks (Original)", STOCK_UNIVERSE, stock_fund,  score_original_stocks, 75),
        ("New Tactic 3 Best Stocks", STOCK_UNIVERSE, stock_fund,  score_new_tactic,      70),
        ("3 Best Crypto",            CRYPTO_UNIVERSE, crypto_fund, score_crypto,          65),
        ("3 Best ETF",               ETF_UNIVERSE,   etf_fund,    score_etf,             70),
    ]

    results: list[SimResult] = []
    for name, universe, fund, score_fn, threshold in configs:
        print(f"\n  ── {name} (threshold={threshold}) ──")
        res = simulate(
            strategy_name = name,
            universe      = universe,
            signals       = signals_all,
            fund_data     = fund,
            score_fn      = score_fn,
            threshold     = threshold,
            start_date    = BACKTEST_START,
            take_profit   = 0.05,
            stop_loss     = 0.07,
        )
        st = calc_stats(res)
        print(f"     Trades: {st['total_trades']}  |  Win rate: {st['win_rate']}%  |  P&L: ${st['total_pnl']:.2f}")
        results.append(res)

    # ── 5. Build & send email ──────────────────────────────────────────────────
    print("\nBuilding report email…")
    html = build_email(results)

    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    to_addr    = os.environ.get("REPORT_TO", gmail_user)

    if not gmail_user:
        out = Path(__file__).parent / "backtest_report.html"
        out.write_text(html, encoding="utf-8")
        print(f"No GMAIL_USER — saved to {out}")
        return

    today_str = date.today().strftime("%d/%m/%Y")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📊 Backtest 3Y TP+5%/SL-7%: {today_str} — {len(results)} Strategies Compared"
    msg["From"]    = gmail_user
    msg["To"]      = to_addr
    msg.attach(MIMEText(html, "html", "utf-8"))

    print(f"Sending backtest report to {to_addr}…")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, to_addr, msg.as_string())
    print("Backtest report sent! ✅")


if __name__ == "__main__":
    main()
