"""CLI: python -m bumpr <repo-dir> <package> [--to VERSION]"""
import argparse
import sys

from . import config
from .agent import Run

COLORS = {"phase": "\033[1;36m", "tests": "\033[1m", "search": "\033[35m", "edit": "\033[33m",
          "error": "\033[31m", "llm": "\033[2m", "tool": "\033[34m"}
RESET = "\033[0m"


def show(e):
    kind = e["type"]
    c = COLORS.get(kind, "")
    if kind == "phase":
        line = f"\n== {e['phase'].upper()}: {e['detail']}"
    elif kind == "versions":
        line = f"   {e['package']}: {e['old']} -> {e['new']}"
    elif kind == "tests":
        mark = "\033[32mGREEN\033[0m" if e["green"] else "\033[31mRED\033[0m"
        line = f"   tests {e.get('label', '')}: {e['passed']} passed, {e['failed']} failed, {e['errors']} errors [{mark}]"
    elif kind == "llm":
        line = (f"   {e['model_short']:<28} {e['purpose']:<12} {e['prompt_tokens']:>6} in {e['completion_tokens']:>5} out "
                f"{e['latency']:>5.1f}s ${e['cost']:.4f}")
    elif kind == "tool":
        arg = next(iter(e["args"].values()), "") if e["args"] else ""
        line = f"   -> {e['name']}({str(arg)[:80]})"
    elif kind == "search":
        line = f"   tavily: {e['query']}\n" + "\n".join(f"      {r['url']}" for r in e["results"])
    elif kind == "edit":
        line = f"   edited {e['path']}\n" + "\n".join("      " + ln for ln in e["diff"].splitlines()[2:40])
    elif kind == "thinking":
        print(f"\033[2m{e['delta']}\033[0m", end="", flush=True)
        line = ""
    elif kind == "plan":
        line = f"\n   ultra plan (attempt {e['attempt']}):\n" + "\n".join("      " + ln for ln in e["text"].splitlines()[:25])
    elif kind == "thought":
        line = f"   ultra: {(e['text'] or e['reasoning'])[:200]}" if (e["text"] or e["reasoning"]) else ""
    elif kind == "triage":
        line = "   key lines:\n" + "\n".join("      " + ln for ln in e["digest"].splitlines()[:8])
    elif kind == "docs":
        line = (f"   nano read {e['url']} ({e['page_chars']} chars) -> {e['passages']}/{e['checked']} "
                f"verbatim passages verified")
    elif kind == "error":
        line = f"   ERROR: {e['message']}"
    elif kind == "done":
        u = e["usage"]
        line = (f"\n== DONE success={e.get('success')} in {e['elapsed']}s | {u['calls']} LLM calls | "
                f"cost ${u['total_cost']:.4f} (all-Ultra would be ${u['all_planner_cost']:.4f})")
    else:
        line = ""
    if line:
        print(f"{c}{line}{RESET}", flush=True)


def main():
    p = argparse.ArgumentParser(prog="bumpr", description="Upgrade a dependency and fix the breakage.")
    p.add_argument("repo", help="path to a Python project with tests")
    p.add_argument("package", help="dependency to upgrade, e.g. pandas")
    p.add_argument("--to", default="latest", help="target version: latest, a major (3), or exact (3.1.0)")
    args = p.parse_args()
    if not config.NEBIUS_API_KEY or not config.TAVILY_API_KEY:
        sys.exit("Set NEBIUS_API_KEY and TAVILY_API_KEY in .env")
    result = Run(args.repo, args.package, args.to, on_event=show).execute()
    if result.get("report"):
        print("\n" + result["report"])
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
