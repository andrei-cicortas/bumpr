"""The upgrade loop: bump -> test -> read the current docs (Tavily + Nano) -> think & rewrite (Ultra) -> test -> repeat."""
import difflib
import json
import re
import time
import uuid
from pathlib import Path

from . import config, docs
from .llm import LLM, BudgetExceeded
from .sandbox import Workspace, resolve_target
from .tools import Toolbox

MAX_ATTEMPTS = 5
MAX_DOC_REQUESTS = 2
MAX_AUTO_LOOKUPS = 2

PLANNER_SYSTEM = """You are Bumpr, a senior Python engineer who migrates codebases to new major versions of a dependency.

The dependency `{package}` was just upgraded from {old} to {new}. The test suite is the specification:
make every test pass by changing APPLICATION code only, using idiomatic, non-deprecated {package} {new} APIs.

Rules:
- Your training data may predate {package} {new}. Trust the fetched documentation excerpts over your memory.
  Behaviour can change silently (defaults, dtypes, copy semantics), not only names.
- Never edit tests, requirements or test config. No compatibility shims, legacy modes, or warning filters.
- Errors surface one layer at a time: migrate EVERY use of {package} in a file, not only the line that failed.
- When you rewrite a file, keep every existing function, class and behaviour; change only what the migration needs.

Answer in exactly this format:
PLAN:
1. old -> new (file) -- why it broke, per the docs ...
Then every file you change, complete, each as:
### FILE: relative/path.py
```python
<entire new file content>
```
If you cannot fix something without more documentation, instead output only the PLAN and up to two lines
`NEED_DOCS: <precise search query>`; you will get the docs and can then write the files."""

REPORT_SYSTEM = (
    "You write the pull request description for an automated dependency upgrade. Markdown. Sections: "
    "'## Summary' (2 sentences), '## Breaking changes handled' (one bullet per change: old -> new, file, and "
    "why it broke), '## Verification' (test results before/after), '## Sources' (the URLs given). "
    "Take every reason from the migration plan; never invent reasons or attribute claims to sources. "
    "Be factual and concise; only describe changes that are in the diff."
)

FILE_BLOCK = re.compile(r"^###\s*FILE:\s*`?([^\s`]+)`?\s*\n```[a-zA-Z0-9_+-]*\n(.*?)\n```", re.M | re.S)


class Run:
    def __init__(self, source, package, target="latest", on_event=None, job_id=None, record=True):
        self.source = Path(source)
        self.package = package
        self.target = target
        self.job_id = job_id or uuid.uuid4().hex[:10]
        self.on_event = on_event
        self.record = record
        self.events = []
        self.started = time.time()
        self.llm = LLM(self.emit)

    def emit(self, event):
        event = {"t": round(time.time() - self.started, 2), **event}
        self.events.append(event)
        if self.on_event:
            self.on_event(event)

    def phase(self, name, detail=""):
        self.emit({"type": "phase", "phase": name, "detail": detail})

    # ------------------------------------------------------------------------------------
    def execute(self):
        ws = Workspace(self.source, self.job_id)
        result = {"success": False}
        try:
            result = self._execute(ws)
        except BudgetExceeded as e:
            self.emit({"type": "error", "message": f"Stopped: {e}."})
        except Exception as e:
            self.emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            self.emit({"type": "done", **result, "usage": self.llm.summary(),
                       "elapsed": round(time.time() - self.started, 1)})
            self._save()
            ws.cleanup()
        return result

    def _execute(self, ws):
        self.phase("setup", f"Isolated workspace + virtualenv for {self.source.name}")
        ws.create()
        ws.install()
        old = ws.installed_version(self.package)
        if not old:
            raise RuntimeError(f"{self.package} is not a dependency of this project")
        new = resolve_target(self.package, self.target)
        self.emit({"type": "versions", "package": self.package, "old": old, "new": new})

        self.phase("baseline", f"Tests on {self.package} {old}")
        baseline = ws.run_tests()
        self.emit({"type": "tests", "label": "baseline", **baseline.as_event()})
        if not baseline.green:
            raise RuntimeError("tests do not pass before the upgrade; Bumpr needs a green baseline")
        total = baseline.passed
        before = ws.snapshot()

        self.phase("bump", f"{self.package} {old} -> {new}")
        ws.pin(self.package, new)
        ws.install()

        tools = Toolbox(ws, self.llm, self.emit, self.package, old, new)
        self.phase("break", f"Tests on {self.package} {new}")
        report = tools.t_run_tests()
        broken = tools.last_run
        if broken.green:
            self.emit({"type": "log", "message": "Nothing broke. The upgrade is safe as-is."})
            return self._finish(ws, before, old, new, total, broken, broken, tools, "No code changes needed.")

        self.phase("research", f"Reading the official {self.package} {new} migration docs")
        code = tools.files_using_package()
        guide, urls = docs.initial_research(self.llm, self.package, new, code, self.emit)
        tools.sources.extend(u for u in urls if u not in tools.sources)

        messages = [
            {"role": "system", "content": PLANNER_SYSTEM.format(package=self.package, old=old, new=new)},
            {"role": "user", "content": (
                f"Repository files:\n{tools.t_list_files()}\n\n"
                f"Application files that use {self.package} (current contents):\n{code}\n\n"
                f"Tests: {total} passed before the upgrade. After the upgrade:\n{report}\n\n"
                f"Official migration guide, fetched just now (verbatim excerpts):\n{guide}")},
        ]
        summary, final = self._iterate(messages, tools, total)

        self.phase("verify", "Final test run")
        final = ws.run_tests()
        self.emit({"type": "tests", "label": "final", **final.as_event()})
        return self._finish(ws, before, old, new, total, broken, final, tools, summary)

    def _iterate(self, messages, tools, total):
        doc_requests = auto_lookups = 0
        seen_errors = set()
        attempt = 0
        plan = ""
        plans = []
        while attempt < MAX_ATTEMPTS:
            attempt += 1
            self.phase("fix", f"Attempt {attempt}: Nemotron Ultra is thinking")
            msg = self.llm.stream(config.PLANNER_MODEL, messages, purpose=f"think & rewrite #{attempt}",
                                  max_tokens=24000)
            content = msg["content"]
            messages.append({"role": "assistant", "content": content})
            plan = content.split("### FILE:")[0].replace("PLAN:", "").strip()
            self.emit({"type": "plan", "attempt": attempt, "text": plan[:3000]})
            if plan:
                plans.append(f"Attempt {attempt}:\n{plan}")

            files = FILE_BLOCK.findall(content)
            wanted = re.findall(r"^NEED_DOCS:\s*(.+)$", content, re.M)
            if not files and wanted and doc_requests < MAX_DOC_REQUESTS:
                doc_requests += 1
                attempt -= 1  # a docs lookup is not a failed attempt
                messages.append({"role": "user", "content": self._lookup(tools, wanted[:2]) +
                                 "\n\nNow write the files."})
                continue
            if not files:
                messages.append({"role": "user", "content": "No files found in your answer. Use the exact "
                                 "### FILE: format with complete file contents."})
                continue

            rejected = []
            for path, body in files:
                out = tools.call("write_file", {"path": path, "content": body.rstrip() + "\n"})
                if out.startswith(("PermissionError", "ValueError")):
                    rejected.append(f"{path}: {out}")
            report = tools.t_run_tests()
            if tools.last_run.green:
                return "\n\n".join(plans), tools.last_run
            feedback = f"Test results after your changes:\n{report}"
            if rejected:
                feedback = "Rejected edits:\n" + "\n".join(rejected) + "\n\n" + feedback
            if wanted and doc_requests < MAX_DOC_REQUESTS:
                doc_requests += 1
                feedback += "\n\n" + self._lookup(tools, wanted[:2])
            else:
                error = self._first_error(tools.last_run.output)
                if error and error not in seen_errors and auto_lookups < MAX_AUTO_LOOKUPS:
                    # A new error the planner did not ask about: look it up in the docs rather than let it guess.
                    seen_errors.add(error)
                    auto_lookups += 1
                    feedback += "\n\n" + self._lookup(tools, [f"{self.package} {tools.new_version} {error}"])
            messages.append({"role": "user", "content": feedback + "\n\nFix the remaining failures. Output "
                             "complete files for everything you change."})
        return "\n\n".join(plans), tools.last_run

    @staticmethod
    def _first_error(output):
        for line in output.splitlines():
            if line.startswith("E ") and len(line.strip()) > 4:
                msg = re.sub(r"0x[0-9a-fA-F]+|https?://\S+", "", line[1:].strip())
                return msg[:140]
        return ""

    def _lookup(self, tools, queries):
        parts = []
        for q in queries:
            self.phase("research", f"Looking up: {q}")
            snippets, urls = docs.search(q, self.emit)
            tools.sources.extend(u for u in urls if u not in tools.sources)
            official = docs.pick_guide([{"url": u, "title": ""} for u in urls], self.package, official_only=True)
            excerpt = docs.read_page(self.llm, official["url"], q, self.emit) if official else ""
            parts.append(f"Docs for `{q}`:\n{excerpt}\n\nSearch snippets:\n{snippets}")
        return "\n\n".join(parts)

    def _finish(self, ws, before, old, new, total, broken, final, tools, summary):
        after = ws.snapshot()
        diff = []
        for rel in sorted(set(before) | set(after)):
            a, b = before.get(rel, ""), after.get(rel, "")
            if a != b:
                diff.append("".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True),
                                                         f"a/{rel}", f"b/{rel}")))
        diff_text = "\n".join(diff)
        report = ""
        if final.green and diff_text:
            self.phase("report", "Nemotron Nano is writing the pull request")
            try:
                report = self.llm.text(
                    config.FAST_MODEL, REPORT_SYSTEM,
                    f"Upgrade: {self.package} {old} -> {new}\nTests: {total} passed before; after the bump "
                    f"{broken.passed} passed, {broken.failed} failed, {broken.errors} errors; after the fix "
                    f"{final.passed} passed.\nMigration plans from every attempt (later attempts fix what "
                    f"earlier ones missed; list every change that is in the diff):\n{summary}\n\nSources:\n" +
                    "\n".join(tools.sources[:12]) + f"\n\nDiff:\n{diff_text[:20000]}",
                    purpose="PR write-up", max_tokens=6000, think=True)
            except BudgetExceeded:
                report = summary
        return {"success": final.green, "package": self.package, "old": old, "new": new, "total": total,
                "broken": broken.as_event(), "final": final.as_event(), "diff": diff_text,
                "summary": summary, "report": report, "sources": tools.sources}

    def _save(self):
        if not self.record:
            return
        config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
        path = config.RUNS_DIR / f"{self.source.name}-{self.job_id}.json"
        path.write_text(json.dumps({"sample": self.source.name, "package": self.package,
                                    "events": self.events}, indent=1), encoding="utf-8")
