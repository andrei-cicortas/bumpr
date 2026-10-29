"""Build the static, recordings-only site into docs/ (served by GitHub Pages).

    python -m web.build_static
"""
import json
import shutil
from pathlib import Path

from web.server import REPLAYS, WEB, samples

OUT = WEB.parent / "docs"


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    (OUT / "data" / "replays").mkdir(parents=True)
    shutil.copytree(WEB / "static", OUT / "static", ignore=shutil.ignore_patterns("index.html"))
    html = (WEB / "static" / "index.html").read_text(encoding="utf-8")
    (OUT / "index.html").write_text(html.replace('<html lang="en">', '<html lang="en" data-static="1">', 1),
                                    encoding="utf-8")
    (OUT / "data" / "samples.json").write_text(json.dumps(samples()), encoding="utf-8")
    for replay in REPLAYS.glob("*.json"):
        shutil.copy2(replay, OUT / "data" / "replays" / replay.name)
    (OUT / ".nojekyll").write_text("")
    print(f"built {OUT}")


if __name__ == "__main__":
    main()
