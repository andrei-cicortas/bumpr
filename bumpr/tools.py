"""Tools the planner model can call. Every tool returns a string that goes back into the conversation."""
import difflib
import fnmatch
import json
import re

from . import config, docs
from .sandbox import key_lines

MAX_TOOL_OUTPUT = 12000
MODULE_NAMES = {"scikit-learn": "sklearn", "pyyaml": "yaml", "beautifulsoup4": "bs4", "pillow": "PIL"}

# The test suite is the specification; the agent may not weaken it or pin back the dependency.
PROTECTED = ["tests/*", "test_*.py", "*/tests/*", "conftest.py", "*/conftest.py", "requirements*.txt",
             "pytest.ini", "setup.cfg", "pyproject.toml", "tox.ini"]
# Shortcuts that make tests pass without really migrating.
FORBIDDEN_SNIPPETS = {
    "*": ["warnings.filterwarnings", "warnings.simplefilter", "pytest.mark.skip", "pytest.skip("],
    "pydantic": ["pydantic.v1"],
    "sqlalchemy": ["sqlalchemy.future", "SQLALCHEMY_WARN_20"],
}

SCHEMAS = [
    {"type": "function", "function": {
        "name": "list_files", "description": "List all files in the repository.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a text file from the repository.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "replace_in_file",
        "description": "Replace an exact snippet in a file. `old` must match the file exactly (including "
                       "indentation) and occur exactly once. Prefer this for targeted edits.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
            "required": ["path", "old", "new"]}}},
    {"type": "function", "function": {
        "name": "write_file", "description": "Overwrite a whole file with new content.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "run_tests", "description": "Run the test suite and get a triaged summary of failures.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "search_docs",
        "description": "Search the live web (current official docs, changelogs, migration guides) and get the "
                       "raw result snippets with URLs. Use it whenever you are not certain how an API works in "
                       "the NEW version: your training data may predate it.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_docs_page",
        "description": "Read a full documentation page. A retriever returns the passages relevant to `focus`, "
                       "verbatim. Use it on official docs URLs from search_docs.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
            "focus": {"type": "string", "description": "the APIs / errors you need, e.g. 'each_item, root_validator'"}},
            "required": ["url", "focus"]}}},
    {"type": "function", "function": {
        "name": "finish", "description": "Call when all tests pass. Summarize what you changed and why.",
        "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}},
]



class Toolbox:
    def __init__(self, ws, llm, emit, package, old_version, new_version):
        self.ws, self.llm, self.emit = ws, llm, emit
        self.package, self.old_version, self.new_version = package, old_version, new_version
        self.sources = []
        self.last_run = None
        self.edits_since_run = 0

    def call(self, name, args):
        try:
            handler = getattr(self, f"t_{name}", None)
            if handler is None:
                return f"Unknown tool {name}"
            out = handler(**args)
        except TypeError as e:
            out = f"Bad arguments for {name}: {e}"
        except Exception as e:  # tool errors are fed back to the model, not raised
            out = f"{type(e).__name__}: {e}"
        return out if len(out) <= MAX_TOOL_OUTPUT else out[:MAX_TOOL_OUTPUT] + "\n...[truncated]"

    # ---- tools -------------------------------------------------------------------------
    def t_list_files(self):
        return "\n".join(self.ws.files())

    def files_using_package(self, budget=60000):
        """Application source for the planner: files importing the package first, then the rest that fit."""
        module = MODULE_NAMES.get(self.package.lower(), self.package.lower().replace("-", "_"))
        pattern = re.compile(rf"^\s*(import|from)\s+{re.escape(module)}\b", re.M | re.I)
        sources = {}
        for rel in self.ws.files():
            if rel.endswith(".py") and not (rel.startswith("tests/") or "/tests/" in rel or
                                            rel.split("/")[-1].startswith("test_")):
                sources[rel] = (self.ws.repo / rel).read_text(encoding="utf-8", errors="replace")
        ordered = sorted(sources, key=lambda r: (not pattern.search(sources[r]), r))
        chunks, used = [], 0
        for rel in ordered:
            text = sources[rel]
            if text.strip() and used + len(text) <= budget:
                chunks.append(f"--- {rel} ---\n{text}")
                used += len(text)
        return "\n".join(chunks)

    def t_read_file(self, path):
        p = self.ws.resolve(path)
        if not p.exists():
            return f"File not found: {path}"
        return p.read_text(encoding="utf-8", errors="replace")

    def t_replace_in_file(self, path, old, new):
        self._guard(path, new)
        p = self.ws.resolve(path)
        text = p.read_text(encoding="utf-8")
        hits = text.count(old)
        if hits != 1:
            return (f"`old` occurs {hits} times in {path}; it must occur exactly once. "
                    "Read the file and copy the snippet exactly, with more context if needed.")
        return self._write(path, p, text, text.replace(old, new, 1))

    def t_write_file(self, path, content):
        self._guard(path, content)
        p = self.ws.resolve(path)
        before = p.read_text(encoding="utf-8") if p.exists() else ""
        p.parent.mkdir(parents=True, exist_ok=True)
        return self._write(path, p, before, content)

    def t_run_tests(self):
        run = self.ws.run_tests()
        self.last_run = run
        self.edits_since_run = 0
        self.emit({"type": "tests", **run.as_event()})
        if run.green:
            return f"All {run.passed} tests pass."
        lines = key_lines(run.output)
        self.emit({"type": "triage", "digest": lines})
        summary = "\n".join(l for l in run.output.splitlines() if l.startswith(("FAILED", "ERROR")))[:3000]
        return (f"{run.passed} passed, {run.failed} failed, {run.errors} errors.\n\n"
                f"Key lines from the log:\n{lines}\n\nShort summary:\n{summary}")

    def t_search_docs(self, query):
        out, urls = docs.search(query, self.emit)
        self.sources.extend(u for u in urls if u not in self.sources)
        return out

    def t_read_docs_page(self, url, focus):
        if url not in self.sources:
            self.sources.append(url)
        return docs.read_page(self.llm, url, focus, self.emit)

    # ---- helpers -----------------------------------------------------------------------
    def _guard(self, path, content):
        rel = path.replace("\\", "/").lstrip("./")
        if any(fnmatch.fnmatch(rel, pat) for pat in PROTECTED):
            raise PermissionError(f"{path} is protected: the tests and pinned requirements are the "
                                  "specification. Change the application code instead.")
        for key in ("*", self.package.lower()):
            for snippet in FORBIDDEN_SNIPPETS.get(key, []):
                if snippet in content:
                    raise PermissionError(f"`{snippet}` is a shortcut, not a migration. Use the idiomatic "
                                          f"{self.package} {self.new_version} API instead.")

    def _write(self, rel, path, before, after):
        path.write_text(after, encoding="utf-8")
        self.edits_since_run += 1
        diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                            f"a/{rel}", f"b/{rel}", n=1))
        self.emit({"type": "edit", "path": rel, "diff": diff})
        return f"Edited {rel}:\n{diff}"


def tool_args(raw):
    try:
        args = json.loads(raw or "{}")
        return args if isinstance(args, dict) else {}
    except json.JSONDecodeError:
        return {}
