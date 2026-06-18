"""
Testing Screener — FastAPI backend.
Serves the frontend and handles backtest API requests.
"""

import threading
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional

from screener_engine import run_backtest

app  = FastAPI(title="Testing Screener")
jobs = {}   # job_id -> result dict


# ── Serve frontend ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html = Path("static/index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


# ── Backtest request model ────────────────────────────────────────────────────

class BacktestParams(BaseModel):
    rsi_threshold:   int           = 30
    rsi_period:      int           = 14
    sectors:         List[str]     = []
    min_market_cap:  str           = "any"
    last_day_red:    bool          = False
    min_consec_red:  int           = 0
    market_regime:   bool          = False
    avoid_months:    List[int]     = []
    tp:              float         = 8.0
    sl:              float         = 5.0
    max_hold:        int           = 14
    lookback_months: int           = 6
    universe:        str           = "sp100"
    investment:      float         = 100.0
    max_positions:   int           = 20


# ── Start backtest (background thread) ───────────────────────────────────────

@app.post("/api/backtest")
async def start_backtest(params: BacktestParams):
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "running", "progress": 0, "message": "Starting..."}

    thread = threading.Thread(
        target=run_backtest,
        args=(params.model_dump(), job_id, jobs),
        daemon=True,
    )
    thread.start()

    return {"job_id": job_id}


# ── Poll job status ───────────────────────────────────────────────────────────

@app.get("/api/backtest/{job_id}")
async def get_result(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return JSONResponse({"status": "not_found"}, status_code=404)
    return job


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
