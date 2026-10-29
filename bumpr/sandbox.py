"""Isolated copy of the target repo with its own virtualenv, managed by uv."""
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import config

IGNORED_DIRS = {".venv", "venv", ".git", "__pycache__", ".pytest_cache", "node_modules", ".mypy_cache"}
TEXT_SUFFIXES = {".py", ".txt", ".ini", ".cfg", ".toml", ".md", ".json", ".yaml", ".yml", ".csv", ".sql"}


@dataclass
class TestRun:
    passed: int
    failed: int
    errors: int
    output: str
    duration: float

    @property
    def green(self):
        return self.failed == 0 and self.errors == 0 and self.passed > 0

    def as_event(self):
        return {"passed": self.passed, "failed": self.failed, "errors": self.errors, "green": self.green,
                "duration": self.duration}


def uv_cmd():
    found = shutil.which("uv")
    return [found] if found else [sys.executable, "-m", "uv"]


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def pypi_versions(package):
    data = httpx.get(f"https://pypi.org/pypi/{package}/json", timeout=20).json()
    releases = [v for v, files in data["releases"].items()
                if files and re.fullmatch(r"\d+(\.\d+)*", v)]
    releases.sort(key=lambda v: [int(x) for x in v.split(".")])
    return data["info"]["version"], releases


def resolve_target(package, target):
    """'latest' -> newest release, '3' -> newest 3.x, '2.1' -> newest 2.1.x, exact versions pass through."""
    latest, releases = pypi_versions(package)
    if not target or target == "latest":
        return latest
    if target in releases and target.count(".") >= 2:
        return target
    prefix = target.rstrip(".") + "."
    matching = [v for v in releases if v.startswith(prefix) or v == target]
    if not matching:
        raise ValueError(f"No release of {package} matches {target!r}")
    return matching[-1]


class Workspace:
    def __init__(self, source: Path, job_id: str):
        self.source = Path(source)
        self.root = config.WORKDIR / job_id
        self.repo = self.root / "repo"
        self.venv = self.root / ".venv"
        bindir = "Scripts" if os.name == "nt" else "bin"
        self.python = self.venv / bindir / ("python.exe" if os.name == "nt" else "python")

    # ---- setup -------------------------------------------------------------------------
    def create(self):
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)
        shutil.copytree(self.source, self.repo, ignore=shutil.ignore_patterns(*IGNORED_DIRS))
        self._run(uv_cmd() + ["venv", "-q", "--python", config.WORKSPACE_PYTHON, str(self.venv)], cwd=self.root)

    def install(self):
        reqs = self.repo / "requirements.txt"
        args = ["-r", str(reqs)] if reqs.exists() else ["-e", str(self.repo)]
        proc = self._run(uv_cmd() + ["pip", "install", "-q", "--python", str(self.python), *args, "pytest"],
                         cwd=self.repo, check=False, timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(f"dependency install failed:\n{proc.stdout[-3000:]}{proc.stderr[-3000:]}")

    def installed_version(self, package):
        proc = self._run(uv_cmd() + ["pip", "show", "--python", str(self.python), package],
                         cwd=self.repo, check=False)
        m = re.search(r"^Version:\s*(\S+)", proc.stdout, re.M)
        return m.group(1) if m else None

    def pin(self, package, version):
        """Point requirements.txt at the new version (the 'bump')."""
        reqs = self.repo / "requirements.txt"
        lines = reqs.read_text(encoding="utf-8").splitlines() if reqs.exists() else []
        out, replaced = [], False
        for line in lines:
            name = re.split(r"[<>=!~\[;\s]", line.strip(), maxsplit=1)[0]
            if name and normalize(name) == normalize(package):
                out.append(f"{name}=={version}")
                replaced = True
            else:
                out.append(line)
        if not replaced:
            out.append(f"{package}=={version}")
        reqs.write_text("\n".join(out) + "\n", encoding="utf-8")

    # ---- tests -------------------------------------------------------------------------
    def run_tests(self, timeout=300):
        proc = self._run([str(self.python), "-m", "pytest", "-q", "--tb=short", "--color=no",
                          "-p", "no:cacheprovider"], cwd=self.repo, check=False, timeout=timeout)
        return parse_pytest((proc.stdout or "") + (proc.stderr or ""))

    # ---- files -------------------------------------------------------------------------
    def files(self):
        result = []
        for path in sorted(self.repo.rglob("*")):
            rel = path.relative_to(self.repo)
            if path.is_file() and not any(part in IGNORED_DIRS for part in rel.parts):
                result.append(rel.as_posix())
        return result

    def snapshot(self):
        snap = {}
        for rel in self.files():
            p = self.repo / rel
            if p.suffix in TEXT_SUFFIXES:
                snap[rel] = p.read_text(encoding="utf-8", errors="replace")
        return snap

    def resolve(self, rel):
        root = self.repo.resolve()
        path = (root / rel).resolve()
        if root not in path.parents:
            raise ValueError(f"path escapes the repository: {rel}")
        return path

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    # ---- helpers -----------------------------------------------------------------------
    def _run(self, cmd, cwd, check=True, timeout=300):
        env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH")}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        if check and proc.returncode != 0:
            raise RuntimeError(f"command failed: {' '.join(map(str, cmd))}\n"
                               f"{proc.stdout[-2000:]}{proc.stderr[-2000:]}")
        return proc


def parse_pytest(output):
    lines = output.strip().splitlines()
    tail = lines[-1] if lines else ""

    def count(word):
        m = re.findall(rf"(\d+) {word}\b", tail)
        return int(m[-1]) if m else 0

    passed, failed, errors = count("passed"), count("failed"), count("errors?")
    if not (passed or failed or errors):
        errors = 1  # collection crashed or pytest never ran
    duration = re.search(r"in ([\d.]+)s", tail)
    return TestRun(passed, failed, errors, output, float(duration.group(1)) if duration else 0.0)


def key_lines(output, limit=40):
    """Deterministic, cheap extraction of the lines that matter in a pytest log."""
    keep, seen = [], set()
    for line in output.splitlines():
        s = line.rstrip()
        if (s.startswith(("E ", "FAILED ", "ERROR ")) or re.match(r"^\S+\.py:\d+: ", s)
                or "Error:" in s or "Warning:" in s):
            if s not in seen:
                seen.add(s)
                keep.append(s)
    return "\n".join(keep[:limit])
