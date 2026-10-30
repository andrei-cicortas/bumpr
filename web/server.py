"""Bumpr web UI: watch recorded runs, or start a live run (guarded by an access code and a spend cap)."""
import asyncio
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bumpr import config
from bumpr.agent import Run
from bumpr.llm import total_spent

WEB = Path(__file__).resolve().parent
REPLAYS = WEB / "replays"
LIVE_ENABLED = os.getenv("BUMPR_LIVE", "1") == "1" and bool(config.NEBIUS_API_KEY and config.TAVILY_API_KEY)
ACCESS_CODE = os.getenv("BUMPR_ACCESS_CODE", "")

SAMPLES = {
    "pandas-sales-report": {
        "package": "pandas", "target": "3", "title": "Sales report",
        "from": "pandas 2.2", "to": "pandas 3.x",
        "blurb": "Strings become a real str dtype, copy-on-write makes chained assignment silently do nothing, "
                 "and deprecated aliases are gone. Released in 2026, after most models' training data.",
    },
    "pydantic-inventory": {
        "package": "pydantic", "target": "2", "title": "Warehouse inventory API",
        "from": "pydantic 1.10", "to": "pydantic 2.x",
        "blurb": "Validators, config classes, ORM mode and serialization all changed. CI treats "
                 "DeprecationWarnings as errors, so half-migrations fail.",
    },
    "sqlalchemy-ledger": {
        "package": "sqlalchemy", "target": "2", "title": "Billing ledger",
        "from": "SQLAlchemy 1.4", "to": "SQLAlchemy 2.x",
        "blurb": "Implicit execution, select([...]), dict(row) and the legacy Query API: "
                 "the classic 2.0 migration, verified against a real SQLite database.",
    },
}

app = FastAPI(title="Bumpr")
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")

_runs: dict[str, dict] = {}
_live_lock = threading.Lock()


class StartRun(BaseModel):
    sample: str
    code: str = ""


@app.get("/")
def index():
    return FileResponse(WEB / "static" / "index.html")


@app.get("/api/config")
def get_config():
    return {"live": LIVE_ENABLED, "needs_code": bool(ACCESS_CODE), "planner": config.short_name(config.PLANNER_MODEL),
            "fast": config.short_name(config.FAST_MODEL), "busy": _live_lock.locked(),
            "budget_left": round(max(config.MAX_TOTAL_COST - total_spent(), 0), 2)}


@app.get("/api/samples")
def samples():
    out = []
    for name, meta in SAMPLES.items():
        replay = REPLAYS / f"{name}.json"
        summary = None
        if replay.exists():
            events = json.loads(replay.read_text(encoding="utf-8"))["events"]
            done = next((e for e in reversed(events) if e["type"] == "done"), None)
            if done:
                summary = {"success": done.get("success"), "elapsed": done.get("elapsed"),
                           "cost": done["usage"]["total_cost"], "tests": done.get("total")}
        out.append({"name": name, **meta, "replay": summary, "tests": test_names(name)})
    return out


def test_names(sample):
    """Test function names of a sample, shown as the human-readable 'checks' list in the UI."""
    names = []
    for path in sorted((config.SAMPLES_DIR / sample / "tests").glob("test_*.py")):
        names += re.findall(r"^def (test_\w+)", path.read_text(encoding="utf-8"), re.M)
    return names


@app.get("/api/replays/{name}")
def replay(name: str):
    path = REPLAYS / f"{name}.json"
    if name not in SAMPLES or not path.exists():
        raise HTTPException(404, "no recording")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.post("/api/runs")
def start_run(body: StartRun):
    if not LIVE_ENABLED:
        raise HTTPException(403, "Live runs are disabled on this deployment. Watch a recording instead.")
    if ACCESS_CODE and body.code != ACCESS_CODE:
        raise HTTPException(401, "Wrong access code.")
    if body.sample not in SAMPLES:
        raise HTTPException(404, "unknown sample")
    if total_spent() >= config.MAX_TOTAL_COST:
        raise HTTPException(402, "The demo's credit budget is used up. Watch a recording instead.")
    if not _live_lock.acquire(blocking=False):
        raise HTTPException(429, "Another live run is in progress. Try again in a few minutes.")

    run_id = uuid.uuid4().hex[:10]
    state = {"events": [], "done": False, "started": time.time()}
    _runs[run_id] = state
    meta = SAMPLES[body.sample]

    def work():
        try:
            Run(config.SAMPLES_DIR / body.sample, meta["package"], meta["target"],
                on_event=state["events"].append, job_id=run_id).execute()
        finally:
            state["done"] = True
            _live_lock.release()

    threading.Thread(target=work, daemon=True).start()
    return {"id": run_id}


@app.get("/api/runs/{run_id}/events")
async def stream(run_id: str):
    state = _runs.get(run_id)
    if not state:
        raise HTTPException(404, "unknown run")

    async def gen():
        sent = 0
        while True:
            events = state["events"]
            while sent < len(events):
                yield f"data: {json.dumps(events[sent])}\n\n"
                sent += 1
            if state["done"] and sent >= len(state["events"]):
                yield "event: end\ndata: {}\n\n"
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
