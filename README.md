# Backtest Screener

A mean-reversion stock screener and backtester for US equities. Find oversold stocks using RSI and 30+ Finviz-style filters, then simulate historical performance with configurable TP/SL/MaxHold exit rules.

**Live app:** https://web-production-93d1e.up.railway.app/


---

<img width="1374" height="868" alt="screenshot" src="https://github.com/user-attachments/assets/b4c024db-1097-47ba-89e2-b95a813f59f6" />


## Features

### Live Screener
- Scan S&P 100, S&P 500, or NASDAQ 100 in real time
- 30+ filters: RSI, P/E, Fwd P/E, PEG, P/S, P/B, EV/EBITDA, ROE, ROA, Gross/Net/Operating Margin, Debt/Equity, Current Ratio, Beta, SMA 20/50/200, Dividend Yield, Analyst Rating, 52W High, and more
- Sortable results table with 16 columns
- Parallel fundamentals fetching (8 threads) — first daily scan ~20–30 sec, subsequent scans instant via daily JSON cache
- CSV export

### Backtest
- Historical simulation over 3, 6, or 12 months
- Exit rules: Take Profit %, Stop Loss %, Max Hold Days
- Stats: Win Rate, EV/Trade, Total P&L, Avg Win/Loss, SL Rate
- Cumulative P&L chart (Chart.js)
- Sector breakdown table
- Market regime filter (SPY above SMA200 = bull only)
- Avoid specific months (e.g. February historically weak)
- CSV export of all trades

### UI
- Finviz-style full-width filter bar — basic filters always visible, "All Filters" expands 30+ advanced options
- Mobile-responsive: splash screen on first visit to choose Mobile or Desktop view, preference saved in localStorage
- Mobile view: card-based results, simplified filter panel with expandable advanced section
- Dark theme (navy/slate palette)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, FastAPI, uvicorn |
| Data | yfinance (bulk + per-ticker), daily JSON cache |
| Parallelism | concurrent.futures.ThreadPoolExecutor |
| Frontend | Vanilla JS, Tailwind CSS (CDN), Chart.js |
| Deploy | Railway (auto-deploy on GitHub push) |

---

## How It Works

### Performance
- **Price/RSI/SMA data**: `yf.download()` bulk call — all tickers at once, very fast
- **Fundamental data** (P/E, ROE, margins...): `ThreadPoolExecutor(max_workers=8)` fetches 8 tickers in parallel
- **Caching**: fundamentals cached to `cache/fund_YYYY-MM-DD.json` — only fetched once per day

### Entry Signal
RSI <= threshold (default 30) + optional: consecutive red days, last-day-red, volume ratio, SMA position, sector, market cap, and all fundamental filters.

### Exit Rules
| Rule | Default |
|------|---------|
| Take Profit | +8% |
| Stop Loss | -5% |
| Max Hold | 14 days |

---

## Local Setup

```bash
pip install fastapi uvicorn yfinance pandas numpy
python main.py
# Visit http://localhost:8000
```

---

## Project Structure

```
backtest-screener/
├── main.py              # FastAPI app, API routes
├── screener_live.py     # Live screener logic + parallel fundamentals
├── screener_engine.py   # Backtester engine
├── backtester.py        # Backtest runner
├── universe.py          # SP100 / SP500 / NASDAQ100 ticker lists
├── cache/               # Daily fundamentals JSON cache
└── static/
    └── index.html       # Frontend (single-page, no framework)
```

---

## Deployment

Auto-deploys to Railway on every push to `main`. No configuration needed.

---

> **Disclaimer**: Past performance does not guarantee future results. This tool is for educational and research purposes only, not financial advice.
