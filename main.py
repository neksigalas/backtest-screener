"""
Backtest Screener — FastAPI backend.
Serves the frontend and handles backtest + live screener API requests.
"""

import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from screener_engine import run_backtest
from screener_live   import run_live_screener

app       = FastAPI(title="Backtest Screener")
jobs      = {}   # backtest jobs
live_jobs = {}   # live screener jobs


# ── Serve frontend ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html = Path("static/index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


# ── Backtest ──────────────────────────────────────────────────────────────────

class BacktestParams(BaseModel):
    rsi_threshold:   int        = 30
    rsi_period:      int        = 14
    sectors:         List[str]  = []
    min_market_cap:  str        = "any"
    last_day_red:    bool       = False
    min_consec_red:  int        = 0
    market_regime:   bool       = False
    avoid_months:    List[int]  = []
    tp:              float      = 8.0
    sl:              float      = 5.0
    max_hold:        int        = 14
    lookback_months: int        = 6
    universe:        str        = "sp100"
    investment:      float      = 100.0
    max_positions:   int        = 20


@app.post("/api/backtest")
async def start_backtest(params: BacktestParams):
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "running", "progress": 0, "message": "Starting…"}
    threading.Thread(
        target=run_backtest,
        args=(params.model_dump(), job_id, jobs),
        daemon=True,
    ).start()
    return {"job_id": job_id}


@app.get("/api/backtest/{job_id}")
async def get_backtest_result(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return JSONResponse({"status": "not_found"}, status_code=404)
    return job


# ── Live Screener ─────────────────────────────────────────────────────────────

class LiveParams(BaseModel):
    universe:          str              = "sp100"
    rsi_period:        int              = 14
    # multi-select filters (list = one of these values matches)
    sectors:           List[str]        = []
    market_cap_ranges: List[str]        = []   # mega/large/mid/small/micro/nano
    # RSI range
    rsi_min:           Optional[float]  = None
    rsi_max:           Optional[float]  = None
    # Price range
    price_min:         Optional[float]  = None
    price_max:         Optional[float]  = None
    # 1-day change %
    change_min:        Optional[float]  = None
    change_max:        Optional[float]  = None
    # Technical filters
    last_day_red:      bool             = False
    min_consec_red:    int              = 0
    vol_ratio_min:     Optional[float]  = None
    sma20_pos:         Optional[str]    = None   # "above" / "below"
    sma50_pos:         Optional[str]    = None
    sma200_pos:        Optional[str]    = None
    # Fundamental filters
    beta_min:          Optional[float]  = None
    beta_max:          Optional[float]  = None
    pe_max:            Optional[float]  = None
    pe_positive:       bool             = False
    div_min:           Optional[float]  = None   # dividend yield %
    from_52w_high_max: Optional[float]  = None   # e.g. -10 = within 10% of 52w high


@app.post("/api/live-screener")
async def start_live_screener(params: LiveParams):
    job_id = str(uuid.uuid4())[:8]
    live_jobs[job_id] = {"status": "running", "progress": 0, "message": "Starting…"}
    threading.Thread(
        target=run_live_screener,
        args=(params.model_dump(), job_id, live_jobs),
        daemon=True,
    ).start()
    return {"job_id": job_id}


@app.get("/api/live-screener/{job_id}")
async def get_live_result(job_id: str):
    job = live_jobs.get(job_id)
    if job is None:
        return JSONResponse({"status": "not_found"}, status_code=404)
    return job


# ── Dev run ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
