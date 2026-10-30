"""Fast, offline tests for Bumpr's deterministic parts (no API keys, no network)."""
from pathlib import Path

import pytest

from bumpr.agent import FILE_BLOCK
from bumpr.docs import grounded_passages, official, pick_guide
from bumpr.sandbox import Workspace, key_lines, parse_pytest
from bumpr.tools import Toolbox


def test_parse_pytest_counts():
    run = parse_pytest("....\n2 failed, 5 passed, 1 error in 0.42s")
    assert (run.passed, run.failed, run.errors, run.duration) == (5, 2, 1, 0.42)
    assert not run.green
    assert parse_pytest("7 passed, 3 warnings in 1.10s").green


def test_parse_pytest_collection_crash_counts_as_error():
    run = parse_pytest("ImportError while importing test module\n!!! Interrupted: 1 error during collection !!!")
    assert run.errors == 1 and not run.green


def test_key_lines_keeps_only_signal():
    log = "noise\nE   TypeError: boom\napp/report.py:12: in clean\nFAILED tests/test_x.py::test_a - TypeError\nmore noise"
    assert key_lines(log).splitlines() == ["E   TypeError: boom", "app/report.py:12: in clean",
                                           "FAILED tests/test_x.py::test_a - TypeError"]


def test_pin_rewrites_only_the_target(tmp_path):
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "requirements.txt").write_text("Pandas==2.2.3\npytest==8.3.5\n")
    ws = Workspace(tmp_path / "src", "job")
    ws.repo = tmp_path / "repo"
    ws.pin("pandas", "3.1.0")
    assert (tmp_path / "repo" / "requirements.txt").read_text().splitlines() == ["Pandas==3.1.0", "pytest==8.3.5"]


def test_grounding_drops_invented_passages():
    page = "Use `ffill()` instead of fillna(method='ffill').\nThe 'M' alias was removed; use 'ME'."
    raw = "Use `ffill()` instead of fillna(method='ffill').\n---\neach_item=True is replaced by mode='before' everywhere"
    kept, checked = grounded_passages(raw, page)
    assert checked == 2 and len(kept) == 1 and "ffill" in kept[0]


def test_pick_guide_prefers_official_migration_page():
    results = [{"url": "https://medium.com/x/pandas-3-migration", "title": "Pandas 3 migration"},
               {"url": "https://pandas.pydata.org/docs/whatsnew/v3.0.0.html", "title": "What's new in 3.0.0"}]
    assert pick_guide(results, "pandas")["url"].startswith("https://pandas.pydata.org")
    assert official("https://docs.sqlalchemy.org/en/20/", "sqlalchemy")
    assert pick_guide(results[:1], "pandas", official_only=True) is None


@pytest.mark.parametrize("path, content", [
    ("tests/test_report.py", "x = 1"),
    ("requirements.txt", "pandas==2.2.3"),
    ("app/models.py", "from pydantic.v1 import BaseModel"),
    ("app/report.py", "import warnings\nwarnings.filterwarnings('ignore')"),
])
def test_guard_blocks_shortcuts(path, content):
    tools = Toolbox(ws=None, llm=None, emit=lambda e: None, package="pydantic", old_version="1.10", new_version="2.14")
    with pytest.raises(PermissionError):
        tools._guard(path, content)


def test_file_blocks_are_parsed_from_the_planner_answer():
    answer = "PLAN:\n1. a -> b\n### FILE: app/report.py\n```python\nprint('new')\n```\n"
    assert FILE_BLOCK.findall(answer) == [("app/report.py", "print('new')")]
