import json
import re
import threading
import time

from openai import OpenAI

from . import config

_ledger_lock = threading.Lock()


class BudgetExceeded(RuntimeError):
    pass


def total_spent():
    try:
        return float(json.loads(config.SPEND_LEDGER.read_text())["usd"])
    except Exception:
        return 0.0


def _add_spend(usd):
    with _ledger_lock:
        config.SPEND_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        config.SPEND_LEDGER.write_text(json.dumps({"usd": total_spent() + usd}))


class LLM:
    """OpenAI-compatible client for Nebius Token Factory that keeps per-model usage, cost and budget."""

    def __init__(self, emit):
        self.client = OpenAI(base_url=config.BASE_URL, api_key=config.NEBIUS_API_KEY,
                             timeout=180, max_retries=2)
        self.emit = emit
        self.calls = []

    def run_cost(self):
        return sum(c["cost"] for c in self.calls)

    def check_budget(self):
        if self.run_cost() >= config.MAX_RUN_COST:
            raise BudgetExceeded(f"per-run budget of ${config.MAX_RUN_COST:.2f} reached")
        if total_spent() >= config.MAX_TOTAL_COST:
            raise BudgetExceeded(f"total budget of ${config.MAX_TOTAL_COST:.2f} reached")

    def chat(self, model, messages, purpose, tools=None, max_tokens=4096, temperature=0.2, think=True):
        self.check_budget()
        kwargs = dict(model=model, messages=messages, max_tokens=max_tokens, temperature=temperature)
        if not think:
            # Nemotron 3 chat template switch: skip the reasoning trace for quick extraction tasks (~5x faster).
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        started = time.time()
        resp = self.client.chat.completions.create(**kwargs)
        self._record(model, purpose, resp.usage, time.time() - started)
        return resp.choices[0].message

    def stream(self, model, messages, purpose, max_tokens=16000, temperature=0.2):
        """Streamed call that forwards the model's reasoning live as `thinking` events."""
        self.check_budget()
        started = time.time()
        stream = self.client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, temperature=temperature, stream=True,
            stream_options={"include_usage": True})
        reasoning, content, usage = [], [], None
        pending, last_flush = [], time.time()
        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            extra = getattr(delta, "model_extra", None) or {}
            r = getattr(delta, "reasoning_content", None) or extra.get("reasoning_content") or extra.get("reasoning")
            if r:
                reasoning.append(r)
                pending.append(r)
            if delta.content:
                content.append(delta.content)
            if pending and time.time() - last_flush > 0.8:
                self.emit({"type": "thinking", "purpose": purpose, "delta": "".join(pending)})
                pending, last_flush = [], time.time()
        if pending:
            self.emit({"type": "thinking", "purpose": purpose, "delta": "".join(pending)})
        text, thought = "".join(content), "".join(reasoning)
        if usage is None:  # provider did not report usage; estimate (~4 chars per token)
            usage = type("U", (), {"prompt_tokens": sum(len(str(m.get("content", ""))) for m in messages) // 4,
                                   "completion_tokens": (len(text) + len(thought)) // 4,
                                   "prompt_tokens_details": None})()
        self._record(model, purpose, usage, time.time() - started)
        return {"content": text, "reasoning": thought}

    def _record(self, model, purpose, usage, latency):
        details = getattr(usage, "prompt_tokens_details", None)
        cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
        call = {
            "model": model,
            "purpose": purpose,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "cached_tokens": cached,
            "latency": round(latency, 2),
            "cost": config.cost(model, usage.prompt_tokens, usage.completion_tokens, cached),
        }
        self.calls.append(call)
        _add_spend(call["cost"])
        self.emit({"type": "llm", **call, "model_short": config.short_name(model)})

    def text(self, model, system, user, purpose, max_tokens=4096, think=False):
        msg = self.chat(model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                        purpose, max_tokens=max_tokens, think=think)
        return (msg.content or "").strip()

    def summary(self):
        """Usage per model plus what the same calls would have cost on the planner model."""
        per_model = {}
        for c in self.calls:
            m = per_model.setdefault(c["model"], {"model_short": config.short_name(c["model"]), "calls": 0,
                                                  "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0})
            m["calls"] += 1
            m["prompt_tokens"] += c["prompt_tokens"]
            m["completion_tokens"] += c["completion_tokens"]
            m["cost"] += c["cost"]
        actual = sum(c["cost"] for c in self.calls)
        all_planner = sum(config.cost(config.PLANNER_MODEL, c["prompt_tokens"], c["completion_tokens"],
                                      c["cached_tokens"]) for c in self.calls)
        return {"per_model": per_model, "total_cost": actual, "all_planner_cost": all_planner,
                "calls": len(self.calls)}


def parse_json(raw):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def reasoning_of(message):
    extra = getattr(message, "model_extra", None) or {}
    return getattr(message, "reasoning_content", None) or extra.get("reasoning_content") or extra.get("reasoning") or ""
