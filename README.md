# Bumpr

**Upgrade a Python dependency. Bumpr reads *today's* official docs, fixes what broke, and proves it with your tests.**

Built for the Nebius x NVIDIA Global AI Hackathon (Coding and Agentic Engineering track) with
**NVIDIA Nemotron 3 Ultra** and **Nemotron 3 Nano** served by **Nebius Token Factory**, plus **Tavily** for live documentation.

| Migration | Tests after bump | After Bumpr | Wall time | Cost | Same calls on Ultra only |
|---|---|---|---|---|---|
| pandas 2.2 -> **3.1** (released 2026) | 0 / 7 | **7 / 7** | 3:38 | $0.031 | $0.121 |
| pydantic 1.10 -> 2.14 | 0 / 11 | **11 / 11** | 1:41 | $0.018 | $0.051 |
| SQLAlchemy 1.4 -> 2.1 | 0 / 5 | **5 / 5** | 5:10 | $0.040 | $0.192 |

*Numbers from the recorded runs in `web/replays/`; costs use live Token Factory prices.*

## The problem

Major-version upgrades are where codebases rot. The breaking changes are documented, but scattered across
migration guides, changelogs and "what's new" pages, and they often change *behaviour* rather than names
(pandas 3 turns strings into a new `str` dtype and makes chained assignment silently do nothing).

LLM coding agents are a poor fit out of the box: their training data predates the release, so they confidently
write the *old* API. In our own tests, both Nemotron 3 Super and Ultra rewrote a pydantic model and still left
`Field(regex=...)` in place: that keyword was removed in pydantic 2.

## How Bumpr works

```
 bump pin ─► run tests ─► find official migration guide (Tavily search)
                │                       │
                │            fetch the whole page (Tavily extract)
                │                       │
                │        Nemotron 3 Nano reads it (up to 150k chars) and copies out
                │        only the passages relevant to this code, verbatim
                │                       │
                │        every passage is checked against the source page;
                │        anything not found there is dropped
                ▼                       ▼
        Nemotron 3 Ultra thinks: numbered migration plan (old -> new, why, per the docs),
        then rewrites every affected file in full
                │
                ▼
          run tests again ── red ──► failures (+ optional NEED_DOCS lookups) go back to Ultra
                │
              green
                ▼
     Nemotron 3 Nano writes the pull request from Ultra's plan + diff ─► patch
```

1. **Isolated sandbox.** The repo is copied into a throwaway workspace with its own virtualenv (managed by `uv`).
   The baseline must be green; then the dependency is bumped and the tests run again.
2. **Live documentation, grounded.** Tavily finds the package's own migration guide; Tavily Extract fetches the
   full page. Nemotron 3 Nano (262k context, $0.06/M input tokens) reads all of it and returns verbatim passages.
   A deterministic check keeps only passages whose lines really occur in the page, so the planner never sees
   "documentation" a model invented.
3. **Think, then rewrite.** Nemotron 3 Ultra receives the code, the failing test lines and the excerpts, writes a
   migration plan, and outputs complete new files. Its reasoning streams live into the UI.
4. **Tests are the judge.** Tests, requirements and test config are write-protected; shortcuts such as
   `pydantic.v1`, warning filters or `pytest.skip` are rejected. Failures go back to Ultra (up to 5 attempts),
   and Ultra can ask for more docs with `NEED_DOCS: <query>`.
5. **Ship.** Nemotron 3 Nano writes the PR description from Ultra's plan and the diff; you get a `.patch`.

### Why two Nemotron models

| Job | Model | Why |
|---|---|---|
| Plan + rewrite code | `nvidia/Nemotron-3-Ultra-550b-a55b` | The hard reasoning; called 2-3 times per run with a compact context |
| Read whole docs pages, extract passages | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | Most tokens in a run are documentation; Nano reads them at 1/16 of Ultra's input price |
| Write the PR | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | Formatting work, no reasoning needed |

Across the three recorded runs, Nano processed most of the tokens, and the cascade cost **65-79% less** than
sending the same calls to Ultra, while Ultra's context stays small (excerpts instead of 50k-character pages).

### What we learned building it

* **Agentic tool loops made Nemotron Ultra *worse* here.** Our first version gave Ultra fine-grained tools
  (`read_file`, `replace_in_file`, `run_tests`, ...). It took 30+ short steps, barely reasoned per step
  (30-100 output tokens), and never got the pydantic sample green ($0.20-0.24 per failed run). Switching to
  "think hard, rewrite whole files, let the tests judge" gave green runs in 2-3 calls for about $0.02-0.04.
* **Small models should copy, not paraphrase.** With thinking disabled, Nano summaries of the docs contained
  confident errors (e.g. "`each_item=True` is replaced by `mode='before'`") that derailed the planner. Asking
  Nano for *verbatim* passages and verifying them against the source fixed it.
* **`chat_template_kwargs: {"enable_thinking": false}`** works on Token Factory for Nemotron 3 and makes Nano about
  5x faster for pure extraction; we keep thinking on wherever accuracy matters.

## Run it

Requirements: Python 3.10+ and [uv](https://docs.astral.sh/uv/) (installed automatically from `requirements.txt`).

```bash
git clone https://github.com/andrei-cicortas/bumpr && cd bumpr
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # add NEBIUS_API_KEY and TAVILY_API_KEY
```

**CLI on any Python repo with a pytest suite and a `requirements.txt`:**

```bash
python -m bumpr samples/pandas-sales-report pandas --to 3
python -m bumpr path/to/your/repo sqlalchemy --to 2      # --to latest | 2 | 2.1 | 2.1.6
```

**Web UI** (recorded runs + live runs):

```bash
uvicorn web.server:app --port 8000
# open http://localhost:8000
```

### Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `NEBIUS_API_KEY` | - | Nebius Token Factory key |
| `TAVILY_API_KEY` | - | Tavily key (free tier is enough) |
| `NEBIUS_BASE_URL` | `https://api.tokenfactory.nebius.com/v1/` | OpenAI-compatible endpoint |
| `BUMPR_PLANNER_MODEL` | `nvidia/Nemotron-3-Ultra-550b-a55b` | Planner / code writer |
| `BUMPR_FAST_MODEL` | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | Docs reader / PR writer |
| `BUMPR_MAX_RUN_COST` | `0.50` | Hard stop per run (USD, from live prices) |
| `BUMPR_MAX_TOTAL_COST` | `3.00` | Hard stop across all runs (ledger in `runs/spend.json`) |
| `BUMPR_ACCESS_CODE` | empty | If set, live runs in the web UI require this code |
| `BUMPR_LIVE` | `1` | Set `0` to serve recordings only |

Costs are computed from the per-token prices that Token Factory's `/v1/models?verbose=true` endpoint returns.

## Project layout

```
bumpr/
  agent.py     the upgrade loop (bump -> test -> docs -> think & rewrite -> verify -> PR)
  docs.py      Tavily search/extract + Nano verbatim extraction + grounding check
  llm.py       Token Factory client: streaming reasoning, usage, live pricing, budget caps
  sandbox.py   isolated workspace + uv virtualenv + pytest runner
  tools.py     guarded file writes (tests/requirements protected, shortcuts rejected)
web/
  server.py    FastAPI: recordings, live runs over SSE
  static/      single-page UI (no build step)
  replays/     recorded event logs of real runs
samples/       three small apps pinned to old majors, each with a pytest suite
```

## Nebius and NVIDIA usage

* **Nebius Token Factory** serves every model call through its OpenAI-compatible API, including streamed
  reasoning traces, tool-free long-context calls (150k-character pages to Nano), and live per-token pricing used
  for the cost meter and spend caps.
* **NVIDIA Nemotron 3 Ultra** (`nvidia/Nemotron-3-Ultra-550b-a55b`): planning and code generation.
* **NVIDIA Nemotron 3 Nano** (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`): documentation reading and PR writing.
* **Tavily**: `search` (advanced depth) to locate official docs, `extract` to fetch full pages, at runtime on every run.

## Limitations

* Python projects with a pytest suite and a `requirements.txt`; one dependency per run.
* The tests are the specification: a migration is only as safe as the suite that checks it.
* Live runs in the hosted demo are behind an access code to protect the hackathon credits.

## License

[MIT](LICENSE)
