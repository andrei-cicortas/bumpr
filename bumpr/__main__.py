"""CLI: python -m bumpr <repo-dir> <package> [--to VERSION]"""
import argparse
from pathlib import Path
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


def setup():
    """Ask for the two API keys once and keep them in ~/.bumpr/.env on this computer only."""
    from getpass import getpass

    import httpx

    print("Bumpr needs two API keys. They are saved only on this computer, in", config.USER_ENV)
    print("  Nebius Token Factory:  https://tokenfactory.nebius.com  (API keys)")
    print("  Tavily (free tier):    https://app.tavily.com\n")
    nebius = getpass("Nebius Token Factory API key (typing is hidden): ").strip()
    tavily = getpass("Tavily API key (typing is hidden): ").strip()
    try:
        ok = httpx.get(config.BASE_URL.rstrip("/") + "/models", headers={"Authorization": f"Bearer {nebius}"},
                       timeout=20).status_code == 200
    except httpx.HTTPError:
        ok = False
    if not ok:
        sys.exit("That Nebius key was rejected by Token Factory. Nothing was saved; run `bumpr setup` again.")
    if not tavily.startswith("tvly-"):
        sys.exit("Tavily keys start with `tvly-`. Nothing was saved; run `bumpr setup` again.")
    config.USER_ENV.parent.mkdir(parents=True, exist_ok=True)
    config.USER_ENV.write_text(f"NEBIUS_API_KEY={nebius}\nTAVILY_API_KEY={tavily}\n", encoding="utf-8")
    try:
        config.USER_ENV.chmod(0o600)
    except OSError:
        pass
    print(f"Saved. Now run, inside a project:  bumpr . <package> --to <version>")


def main():
    if sys.argv[1:2] == ["setup"]:
        return setup()
    p = argparse.ArgumentParser(
        prog="bumpr", description="Upgrade a dependency and fix the breakage.",
        epilog="First time? Run `bumpr setup` to store your API keys. Example: bumpr . pandas --to 3 --apply")
    p.add_argument("repo", help="path to a Python project with tests and a requirements.txt (use . for here)")
    p.add_argument("package", help="dependency to upgrade, e.g. pandas")
    p.add_argument("--to", default="latest", help="target version: latest, a major (3), or exact (3.1.0)")
    p.add_argument("--apply", action="store_true",
                   help="write the fixed files into the repo when every test passes (review with `git diff`)")
    p.add_argument("--out", default=".", help="folder for the .patch and the pull request description")
    if len(sys.argv) == 1:
        p.print_help()
        return 0
    args = p.parse_args()
    if not config.NEBIUS_API_KEY or not config.TAVILY_API_KEY:
        sys.exit("No API keys found. Run `bumpr setup` once (or set NEBIUS_API_KEY and TAVILY_API_KEY).")
    if not Path(args.repo, "requirements.txt").exists():
        sys.exit(f"{Path(args.repo).resolve()} has no requirements.txt. Bumpr needs one, plus a pytest suite.")
    run = Run(args.repo, args.package, args.to, on_event=show)
    result = run.execute()
    if result.get("report"):
        print("\n" + result["report"])

    if result.get("diff"):
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        stem = f"bumpr-{result['package']}-{result['new']}"
        (out / f"{stem}.patch").write_text(result["diff"], encoding="utf-8")
        (out / f"{stem}.md").write_text(result.get("report") or result.get("summary") or "", encoding="utf-8")
        print(f"\npatch:          {out / (stem + '.patch')}")
        print(f"PR description: {out / (stem + '.md')}")
        if args.apply and result.get("success"):
            repo = Path(args.repo)
            for rel, content in run.changed_files.items():
                target = repo / rel
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
            print(f"applied to {repo} ({len(run.changed_files)} files). Review with `git diff`, then commit.")
        elif args.apply:
            print("not applied: some tests still fail. The patch above shows how far Bumpr got.")
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
