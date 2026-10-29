"""Live documentation: Tavily finds and fetches the current docs, Nemotron Nano reads them in full and hands the
planner only the passages that matter, copied verbatim and checked against the source."""
import re

import httpx

from . import config

TAVILY = "https://api.tavily.com"
MAX_PAGE_CHARS = 150_000

EXTRACT_SYSTEM = (
    "You are a documentation retriever for a coding agent. From the DOCUMENT, copy verbatim every passage "
    "(including code examples) that is relevant to the FOCUS: any API, parameter, config option, decorator, "
    "default or behaviour change it mentions or that the code uses. Copy text exactly as written. Do not "
    "paraphrase, summarize, or add words of your own. Separate passages with a line containing only ---. "
    "If nothing is relevant, output NONE."
)


def _post(path, payload, timeout=60):
    resp = httpx.post(f"{TAVILY}/{path}", json=payload, timeout=timeout,
                      headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"})
    resp.raise_for_status()
    return resp.json()


def tavily_search(query, max_results=4):
    data = _post("search", {"query": query, "search_depth": "advanced", "max_results": max_results,
                            "chunks_per_source": 3, "include_answer": False})
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
            for r in data.get("results", [])]


def tavily_extract(url):
    data = _post("extract", {"urls": [url], "extract_depth": "basic", "format": "markdown"}, timeout=90)
    results = data.get("results") or []
    return (results[0].get("raw_content") or "") if results else ""


def search(query, emit):
    """Raw search snippets for the planner (no paraphrasing in between)."""
    results = tavily_search(query)
    emit({"type": "search", "query": query, "results": [{"title": r["title"], "url": r["url"]} for r in results]})
    if not results:
        return "No results.", []
    body = "\n\n".join(f"[{i + 1}] {r['title']}\n{r['url']}\n{r['content'][:2000]}" for i, r in enumerate(results))
    return body + "\n\nUse read_docs_page(url, focus) to read a full page.", [r["url"] for r in results]


def read_page(llm, url, focus, emit):
    """Fetch a full docs page and let the fast model pull out the relevant passages, verbatim."""
    page = tavily_extract(url)[:MAX_PAGE_CHARS]
    if not page:
        return f"Could not fetch {url}."
    raw = llm.text(config.FAST_MODEL, EXTRACT_SYSTEM, f"FOCUS:\n{focus}\n\nDOCUMENT ({url}):\n{page}",
                   purpose="read docs", max_tokens=12000, think=True)
    passages, checked = grounded_passages(raw, page)
    emit({"type": "docs", "url": url, "page_chars": len(page), "passages": len(passages), "checked": checked,
          "excerpt": "\n---\n".join(passages)[:4000]})
    if not passages:
        return f"{url}: no passages relevant to the focus."
    return f"Verbatim excerpts from {url} ({len(passages)} passages):\n\n" + "\n---\n".join(passages)


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[`*_>#]", "", s)).strip().lower()


def grounded_passages(raw, page):
    """Keep only passages whose lines really occur in the source page (guards against invented 'docs')."""
    if raw.strip().upper().startswith("NONE"):
        return [], 0
    doc = _norm(page)
    kept, candidates = [], [p.strip() for p in re.split(r"^\s*---\s*$", raw, flags=re.M) if p.strip()]
    for p in candidates:
        lines = [_norm(l) for l in p.splitlines() if len(_norm(l)) > 12]
        if not lines:
            continue
        hits = sum(1 for l in lines if l in doc)
        if hits / len(lines) >= 0.6:
            kept.append(p)
    return kept, len(candidates)


def pick_guide(results, package):
    """Prefer the package's own migration / what's-new page among search results."""
    def score(r):
        u, t = r["url"].lower(), r["title"].lower()
        s = 0
        if any(k in u or k in t for k in ("migration", "migrating", "whatsnew", "what's new", "upgrad", "changelog",
                                           "release")):
            s += 2
        if package.lower() in u.split("/")[2]:
            s += 2
        if any(k in u for k in ("docs.", "/docs", "readthedocs", ".pydata.org")):
            s += 1
        if any(k in u for k in ("stackoverflow", "medium.com", "reddit", "towardsdatascience")):
            s -= 2
        return s
    return max(results, key=score) if results else None


def initial_research(llm, package, new, code, emit):
    """Find the official migration guide for the new major version and extract what this code needs."""
    major = new.split(".")[0]
    query = f"{package} {major}.0 migration guide what's new breaking changes"
    results = tavily_search(query, max_results=5)
    emit({"type": "search", "query": query, "results": [{"title": r["title"], "url": r["url"]} for r in results]})
    guide = pick_guide(results, package)
    if not guide:
        return "No migration guide found.", []
    focus = f"Migrating this code to {package} {new}:\n{code[:20000]}"
    return read_page(llm, guide["url"], focus, emit), [r["url"] for r in results]
