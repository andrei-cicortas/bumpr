import os
from functools import lru_cache
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.getenv("BUMPR_HOME", Path.home() / ".bumpr"))
IN_CHECKOUT = (ROOT / "samples").is_dir()  # running from a clone of the repo rather than a pip install
USER_ENV = HOME / ".env"  # written by `bumpr setup`

# Real environment variables win, then a .env in the checkout or the current folder, then `bumpr setup`'s file.
for _env in (ROOT / ".env", Path.cwd() / ".env", USER_ENV):
    load_dotenv(_env)

BASE_URL = os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")
NEBIUS_API_KEY = os.getenv("NEBIUS_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# Ultra does the hard reasoning (planning + code edits); Nano handles the cheap, high-volume calls.
PLANNER_MODEL = os.getenv("BUMPR_PLANNER_MODEL", "nvidia/Nemotron-3-Ultra-550b-a55b")
FAST_MODEL = os.getenv("BUMPR_FAST_MODEL", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")

_DATA = ROOT if IN_CHECKOUT else HOME
WORKDIR = Path(os.getenv("BUMPR_WORKDIR", _DATA / "workspaces"))
RUNS_DIR = Path(os.getenv("BUMPR_RUNS_DIR", _DATA / "runs"))
SAMPLES_DIR = ROOT / "samples"
WORKSPACE_PYTHON = os.getenv("BUMPR_WORKSPACE_PYTHON", "3.12")
MAX_STEPS = int(os.getenv("BUMPR_MAX_STEPS", "30"))

# Hard spending caps (USD, estimated from live prices). A run stops before any call that would exceed them.
MAX_RUN_COST = float(os.getenv("BUMPR_MAX_RUN_COST", "0.50"))
MAX_TOTAL_COST = float(os.getenv("BUMPR_MAX_TOTAL_COST", "3.00"))
SPEND_LEDGER = RUNS_DIR / "spend.json"

# USD per token, used when the live price list is unreachable.
_FALLBACK_PRICES = {
    "nvidia/Nemotron-3-Ultra-550b-a55b": (1.0e-6, 3.0e-6, 1.2e-7),
    "nvidia/nemotron-3-super-120b-a12b": (3.0e-7, 9.0e-7, 6.0e-8),
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (6.0e-8, 2.4e-7, 1.2e-8),
    "nvidia/Nemotron-3_5-Lightning": (6.0e-8, 2.4e-7, 3.0e-8),
}


@lru_cache(maxsize=1)
def prices():
    """Live per-token prices from Token Factory's model list, falling back to a static table."""
    table = dict(_FALLBACK_PRICES)
    try:
        r = httpx.get(BASE_URL.rstrip("/") + "/models", params={"verbose": "true"},
                      headers={"Authorization": f"Bearer {NEBIUS_API_KEY}"}, timeout=15)
        for m in r.json().get("data", []):
            p = m.get("pricing") or {}
            if "prompt" in p and "completion" in p:
                table[m["id"]] = (float(p["prompt"]), float(p["completion"]),
                                  float(p.get("input_cache_read") or p["prompt"]))
    except Exception:
        pass
    return table


def cost(model, prompt_tokens, completion_tokens, cached_tokens=0):
    p_in, p_out, p_cache = prices().get(model, (0.0, 0.0, 0.0))
    fresh = max(prompt_tokens - cached_tokens, 0)
    return fresh * p_in + cached_tokens * p_cache + completion_tokens * p_out


def short_name(model):
    return model.split("/")[-1]
