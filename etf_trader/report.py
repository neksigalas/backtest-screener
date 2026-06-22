"""
Daily report builder and email sender for 3 Best ETF Paper Trading.
Generates a rich HTML email with equity curve (QuickChart.io) and sends via Gmail SMTP.
"""

import json
import os
import smtplib
import sys
import urllib.parse
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yfinance as yf
from etf_trader.engine import load_state, portfolio_stats, TAKE_PROFIT_PCT, STOP_LOSS_PCT
from etf_trader.universe import LEVERAGE

TITLE = "3 Best ETF Paper Trading"


# ── Current prices ────────────────────────────────────────────────────────────

def _current_price(ticker: str) -> float | None:
    try:
        info  = yf.Ticker(ticker).fast_info
        p     = info.get("last_price") or info.get("previous_close")
        return float(p) if p else None
    except Exception:
        return None


# ── Equity curve chart (QuickChart.io) ────────────────────────────────────────

def _build_chart_url(trades: list) -> str:
    if not trades:
        config = {
            "type": "line",
            "data": {"labels": ["No trades yet"], "datasets": [{"data": [0], "borderColor": "#475569", "fill": False}]},
            "options": {
                "plugins": {"legend": {"display": False}},
                "scales": {
                    "x": {"ticks": {"color": "#64748b"}, "grid": {"color": "#1e293b"}},
                    "y": {"ticks": {"color": "#64748b"}, "grid": {"color": "#334155"}},
                },
            },
        }
    else:
        sorted_trades = sorted(trades, key=lambda t: t["exit_date"])
        labels, data, running = [], [], 0.0
        for t in sorted_trades:
            running += t["pnl"]
            labels.append(t["exit_date"])
            data.append(round(running, 2))

        color = "#22c55e" if running >= 0 else "#ef4444"
        config = {
            "type": "line",
            "data": {
                "labels": labels,
                "datasets": [{
                    "label": "Cumulative P&L ($)",
                    "data":  data,
                    "borderColor":     color,
                    "backgroundColor": color + "26",
                    "fill":            True,
                    "tension":         0.3,
                    "pointRadius":     3,
                    "borderWidth":     2,
                }],
            },
            "options": {
                "plugins": {"legend": {"labels": {"color": "#94a3b8"}}},
                "scales": {
                    "x": {"ticks": {"color": "#94a3b8"}, "grid": {"color": "#1e293b"}},
                    "y": {"ticks": {"color": "#94a3b8"}, "grid": {"color": "#334155"}},
                },
            },
        }

    encoded = urllib.parse.quote(json.dumps(config, separators=(",", ":")))
    return f"https://quickchart.io/chart?c={encoded}&width=800&height=280&backgroundColor=%231e293b"


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _pnl_color(val: float) -> str:
    return "#22c55e" if val >= 0 else "#ef4444"


def _lev_badge(ticker: str) -> str:
    lev = LEVERAGE.get(ticker, 1)
    if lev >= 3:
        return '<span style="background:#4c1d95;color:#c4b5fd;padding:1px 7px;border-radius:8px;font-size:11px;">3x</span>'
    if lev == 2:
        return '<span style="background:#1e3a5f;color:#93c5fd;padding:1px 7px;border-radius:8px;font-size:11px;">2x</span>'
    return '<span style="background:#1e293b;color:#64748b;padding:1px 7px;border-radius:8px;font-size:11px;">1x</span>'


def _reason_badge(reason: str) -> str:
    if reason == "take_profit":
        return '<span style="background:#14532d;color:#86efac;padding:2px 8px;border-radius:10px;font-size:11px;">✅ Take Profit +10%</span>'
    if reason == "stop_loss":
        return '<span style="background:#450a0a;color:#fca5a5;padding:2px 8px;border-radius:10px;font-size:11px;">🛑 Stop Loss -10%</span>'
    return f'<span style="background:#1e293b;color:#94a3b8;padding:2px 8px;border-radius:10px;font-size:11px;">{reason}</span>'


def _stat_box(label: str, value: str, color: str = "#f1f5f9") -> str:
    return f"""
    <div style="background:#1e293b;border-radius:10px;padding:16px 20px;text-align:center;flex:1;min-width:120px;">
      <div style="font-size:22px;font-weight:700;color:{color};">{value}</div>
      <div style="font-size:11px;color:#64748b;margin-top:4px;text-transform:uppercase;letter-spacing:.05em;">{label}</div>
    </div>"""


# ── Main HTML builder ─────────────────────────────────────────────────────────

def build_report(state: dict, today_str: str) -> str:
    trades    = state["trades"]
    positions = state["positions"]
    stats     = portfolio_stats(state)
    today     = date.today().isoformat()

    today_trades = [t for t in trades if t["exit_date"] == today]
    today_buys   = [p for p in positions if p["entry_date"] == today]

    pos_with_price = []
    for pos in positions:
        price    = _current_price(pos["ticker"])
        upnl_pct = round((price / pos["entry_price"] - 1) * 100, 2) if price else None
        upnl_usd = round((price - pos["entry_price"]) * pos["quantity"], 2) if price else None
        pos_with_price.append({**pos, "current_price": price,
                                "upnl_pct": upnl_pct, "upnl_usd": upnl_usd})

    chart_url = _build_chart_url(trades)

    # ── Today's activity ──────────────────────────────────────────────────────
    activity_rows = ""
    for p in today_buys:
        activity_rows += f"""
        <tr style="border-bottom:1px solid #0f172a;">
          <td style="padding:10px 8px;">
            <span style="background:#172554;color:#93c5fd;padding:2px 8px;border-radius:10px;font-size:11px;">🛒 BUY</span>
          </td>
          <td style="padding:10px 8px;font-weight:700;color:#f1f5f9;">{p['ticker']} {_lev_badge(p['ticker'])}</td>
          <td style="padding:10px 8px;color:#94a3b8;">{p.get('name','')}</td>
          <td style="padding:10px 8px;color:#94a3b8;">${p['entry_price']:.2f}</td>
          <td style="padding:10px 8px;color:#94a3b8;">—</td>
          <td style="padding:10px 8px;color:#94a3b8;">—</td>
          <td style="padding:10px 8px;">
            <span style="background:#1e3a5f;color:#60a5fa;padding:2px 8px;border-radius:10px;font-size:12px;">Score {p['entry_score']}</span>
          </td>
        </tr>"""

    for t in today_trades:
        pnl_c = _pnl_color(t["pnl_pct"])
        activity_rows += f"""
        <tr style="border-bottom:1px solid #0f172a;">
          <td style="padding:10px 8px;">{_reason_badge(t['exit_reason'])}</td>
          <td style="padding:10px 8px;font-weight:700;color:#f1f5f9;">{t['ticker']} {_lev_badge(t['ticker'])}</td>
          <td style="padding:10px 8px;color:#94a3b8;">{t.get('name','')}</td>
          <td style="padding:10px 8px;color:#94a3b8;">${t['entry_price']:.2f}</td>
          <td style="padding:10px 8px;color:#94a3b8;">${t['exit_price']:.2f}</td>
          <td style="padding:10px 8px;font-weight:700;color:{pnl_c};">{'+' if t['pnl_pct']>=0 else ''}{t['pnl_pct']}%</td>
          <td style="padding:10px 8px;font-weight:700;color:{pnl_c};">{'+' if t['pnl']>=0 else ''}${t['pnl']:.2f}</td>
        </tr>"""

    if not activity_rows:
        activity_rows = """
        <tr><td colspan="7" style="padding:20px;text-align:center;color:#475569;">
          Καμία κίνηση σήμερα
        </td></tr>"""

    activity_headers = """
        <tr style="background:#0f172a;">
          <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:11px;">ΚΙΝΗΣΗ</th>
          <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:11px;">TICKER</th>
          <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:11px;">ΟΝΟΜΑ</th>
          <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:11px;">ΤΙΜΗ ΑΓΟΡΑΣ</th>
          <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:11px;">ΤΙΜΗ ΠΩΛΗΣΗΣ</th>
          <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:11px;">P&L %</th>
          <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:11px;">P&L $</th>
        </tr>"""

    # ── Open positions ────────────────────────────────────────────────────────
    pos_rows = ""
    for p in pos_with_price:
        upnl_pct     = p["upnl_pct"]
        upnl_usd     = p["upnl_usd"]
        pnl_c        = _pnl_color(upnl_pct or 0)
        cur_p        = f"${p['current_price']:.2f}" if p["current_price"] else "—"
        upnl_pct_str = f"{'+' if upnl_pct>=0 else ''}{upnl_pct}%" if upnl_pct is not None else "—"
        upnl_usd_str = f"{'+' if upnl_usd>=0 else ''}${upnl_usd:.2f}" if upnl_usd is not None else "—"
        tp_price     = round(p["entry_price"] * (1 + abs(TAKE_PROFIT_PCT)), 2)
        sl_price     = round(p["entry_price"] * (1 - abs(STOP_LOSS_PCT)), 2)

        pos_rows += f"""
        <tr style="border-bottom:1px solid #0f172a;">
          <td style="padding:10px 8px;font-weight:700;color:#f1f5f9;">{p['ticker']} {_lev_badge(p['ticker'])}</td>
          <td style="padding:10px 8px;color:#94a3b8;">{p.get('name','')}</td>
          <td style="padding:10px 8px;color:#94a3b8;">{p['entry_date']}</td>
          <td style="padding:10px 8px;color:#94a3b8;">${p['entry_price']:.2f}</td>
          <td style="padding:10px 8px;color:#f1f5f9;font-weight:600;">{cur_p}</td>
          <td style="padding:10px 8px;font-weight:700;color:{pnl_c};">{upnl_pct_str}</td>
          <td style="padding:10px 8px;font-weight:700;color:{pnl_c};">{upnl_usd_str}</td>
          <td style="padding:10px 8px;color:#22c55e;font-size:12px;">${tp_price}</td>
          <td style="padding:10px 8px;color:#ef4444;font-size:12px;">${sl_price}</td>
          <td style="padding:10px 8px;">
            <span style="background:#1e3a5f;color:#60a5fa;padding:2px 6px;border-radius:8px;font-size:11px;">{p['entry_score']}</span>
          </td>
        </tr>"""

    if not pos_rows:
        pos_rows = """
        <tr><td colspan="10" style="padding:20px;text-align:center;color:#475569;">
          Δεν υπάρχουν ανοιχτές θέσεις
        </td></tr>"""

    # ── Stats boxes ───────────────────────────────────────────────────────────
    pnl_color = _pnl_color(stats["total_pnl"])
    wr_color  = "#22c55e" if stats["win_rate"] >= 50 else "#ef4444"

    stat_boxes = (
        _stat_box("Συνολικές Συναλλαγές", str(stats["total_trades"])) +
        _stat_box("Win Rate", f"{stats['win_rate']}%", wr_color) +
        _stat_box("Κέρδη / Ζημίες", f"{stats['wins']}W / {stats['losses']}L") +
        _stat_box("Μέσο Κέρδος", f"+{stats['avg_win']}%", "#22c55e") +
        _stat_box("Μέση Ζημία", f"{stats['avg_loss']}%", "#ef4444") +
        _stat_box("Συνολικό P&L", f"{'+'if stats['total_pnl']>=0 else ''}${stats['total_pnl']:.2f}", pnl_color)
    )

    best  = stats.get("best_trade")
    worst = stats.get("worst_trade")
    best_str  = f"{best['ticker']} ({'+' if best['pnl_pct']>=0 else ''}{best['pnl_pct']}%)"  if best  else "—"
    worst_str = f"{worst['ticker']} ({'+' if worst['pnl_pct']>=0 else ''}{worst['pnl_pct']}%)" if worst else "—"

    return f"""<!DOCTYPE html>
<html lang="el">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{TITLE} — {today_str}</title>
</head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#f1f5f9;">
<div style="max-width:900px;margin:0 auto;padding:24px 16px;">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#0c2340,#0f172a);border-radius:14px;padding:28px;margin-bottom:24px;border:1px solid #0ea5e922;">
    <div style="font-size:11px;color:#38bdf8;font-weight:600;letter-spacing:.1em;text-transform:uppercase;margin-bottom:6px;">Daily Report</div>
    <div style="font-size:26px;font-weight:800;color:#f1f5f9;">📊 {TITLE}</div>
    <div style="color:#94a3b8;margin-top:6px;font-size:14px;">{today_str}</div>
    <div style="margin-top:8px;font-size:12px;color:#64748b;">
      Universe: Leveraged ETFs (2x/3x) + Volatile Sectors &nbsp;|&nbsp;
      <span style="color:#c4b5fd;">3x</span> &nbsp;
      <span style="color:#93c5fd;">2x</span> &nbsp;
      <span style="color:#64748b;">1x</span>
    </div>
  </div>

  <!-- Stats grid -->
  <div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:24px;">
    {stat_boxes}
  </div>

  <!-- Best / Worst -->
  <div style="display:flex;gap:10px;margin-bottom:24px;flex-wrap:wrap;">
    <div style="background:#14532d;border-radius:10px;padding:14px 18px;flex:1;min-width:200px;">
      <div style="font-size:11px;color:#86efac;letter-spacing:.05em;text-transform:uppercase;">🏆 Καλύτερη Συναλλαγή</div>
      <div style="font-size:16px;font-weight:700;color:#f1f5f9;margin-top:4px;">{best_str}</div>
    </div>
    <div style="background:#450a0a;border-radius:10px;padding:14px 18px;flex:1;min-width:200px;">
      <div style="font-size:11px;color:#fca5a5;letter-spacing:.05em;text-transform:uppercase;">💔 Χειρότερη Συναλλαγή</div>
      <div style="font-size:16px;font-weight:700;color:#f1f5f9;margin-top:4px;">{worst_str}</div>
    </div>
  </div>

  <!-- Equity curve chart -->
  <div style="background:#1e293b;border-radius:12px;padding:20px;margin-bottom:24px;">
    <div style="font-size:14px;font-weight:600;color:#94a3b8;margin-bottom:12px;">📊 Equity Curve — Συνολικό P&L</div>
    <img src="{chart_url}"
         alt="Equity Curve"
         style="width:100%;max-width:860px;border-radius:8px;display:block;">
  </div>

  <!-- Today's activity -->
  <div style="background:#1e293b;border-radius:12px;padding:20px;margin-bottom:24px;">
    <div style="font-size:14px;font-weight:600;color:#94a3b8;margin-bottom:12px;">⚡ Κινήσεις Σήμερα</div>
    <div style="overflow-x:auto;">
      <table style="width:100%;border-collapse:collapse;">
        <thead>{activity_headers}</thead>
        <tbody>{activity_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- Open positions -->
  <div style="background:#1e293b;border-radius:12px;padding:20px;margin-bottom:24px;">
    <div style="font-size:14px;font-weight:600;color:#94a3b8;margin-bottom:12px;">
      💼 Ανοιχτές Θέσεις ({len(positions)}/3)
    </div>
    <div style="overflow-x:auto;">
      <table style="width:100%;border-collapse:collapse;">
        <thead>
          <tr style="background:#0f172a;">
            <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:11px;">TICKER</th>
            <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:11px;">ΟΝΟΜΑ</th>
            <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:11px;">ΗΜΕΡ. ΑΓΟΡΑΣ</th>
            <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:11px;">ΤΙΜΗ ΑΓΟΡΑΣ</th>
            <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:11px;">ΤΙΜΗ ΤΩΡΑ</th>
            <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:11px;">UNREALIZED %</th>
            <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:11px;">UNREALIZED $</th>
            <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:11px;">TAKE PROFIT</th>
            <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:11px;">STOP LOSS</th>
            <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:11px;">SCORE</th>
          </tr>
        </thead>
        <tbody>{pos_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- Footer -->
  <div style="padding:16px;background:#1e293b;border-radius:10px;font-size:12px;color:#475569;line-height:1.8;">
    <div>📌 Paper Trading — Προσομοίωση μόνο, χωρίς πραγματικά χρήματα.</div>
    <div>🎯 Κανόνες: Max 3 θέσεις | $100/θέση | Take Profit +10% | Stop Loss -10% | Score &gt; 70</div>
    <div>📡 Data: Yahoo Finance | Scan: καθημερινά 22:00 Αθήνα | Monitor: κάθε ώρα NYSE hours</div>
  </div>

</div>
</body>
</html>"""


# ── Email sender ──────────────────────────────────────────────────────────────

def send_report():
    state     = load_state()
    today_str = date.today().strftime("%A, %d %B %Y")
    subj_date = date.today().strftime("%d/%m/%Y")

    print("Building ETF report...")
    html = build_report(state, today_str)

    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    to_addr    = os.environ.get("REPORT_TO", gmail_user)

    if not gmail_user:
        out = Path(__file__).parent / "report_preview.html"
        out.write_text(html, encoding="utf-8")
        print(f"No GMAIL_USER set — saved preview to {out}")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📊 {TITLE} — {subj_date}"
    msg["From"]    = gmail_user
    msg["To"]      = to_addr
    msg.attach(MIMEText(html, "html", "utf-8"))

    print(f"Sending ETF report to {to_addr}...")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, to_addr, msg.as_string())
    print("ETF report sent!")


if __name__ == "__main__":
    send_report()
