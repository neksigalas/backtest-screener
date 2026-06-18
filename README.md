# Testing Screener

A web-based stock screener backtester. Set your screening criteria, choose a lookback period, and see what results you would have gotten — Win Rate, EV per trade, total P&L, and a full equity curve.

## Features

- **Finviz-style filters**: RSI, Sector, Market Cap, Consecutive Red Days, Market Regime, and more
- **Historical simulation**: Runs your screener every trading day over 3 months, 6 months, or 1 year
- **Full backtest stats**: Win Rate, EV/trade, Total P&L, SL rate, by-sector breakdown
- **Equity curve chart** with cumulative P&L over time
- **Export CSV** of all simulated trades
- **Universe**: S&P 100, S&P 500, or NASDAQ 100

## Quick Start

```bash
# 1. Create virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Mac/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run
python -m uvicorn main:app --host 0.0.0.0 --port 8000

# 4. Open http://localhost:8000
```

Or on Windows just double-click `run.bat`.

## How It Works

1. Set your screener filters (RSI threshold, sector, market regime, etc.)
2. Set backtest parameters (TP%, SL%, max hold days, investment per trade)
3. Choose lookback period and stock universe
4. Click **Run Backtest**
5. The engine simulates running your screener on every trading day in the period, opens virtual positions for matching stocks, and closes them when TP/SL/MaxHold is reached

## Stack

- **Backend**: FastAPI (Python)
- **Frontend**: HTML + Tailwind CSS + Chart.js
- **Data**: yfinance (free historical data)

## License

MIT
