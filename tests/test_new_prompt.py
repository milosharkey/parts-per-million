"""new_prompt.py: starting a prompt from a template (DECISIONS.md, 43)."""
import pytest

import config
import new_prompt
from prompts import load_prompt


@pytest.fixture
def folders(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "QUERY_DIR", tmp_path / "queries")
    (tmp_path / "data").mkdir()
    (tmp_path / "queries").mkdir()
    return tmp_path


def test_category_template_makes_a_loadable_draft(folders):
    folder = new_prompt.scaffold("alloys", "category", "Name an alloy", category="Category:Alloys", kind="alloy")
    spec = load_prompt(folder)
    assert spec["status"] == "draft" and spec["category"] == "Category:Alloys"
    assert spec["label"] == "Alloys" and spec["colour"] == new_prompt.PALETTE[0]
    assert (folder / "must_accept.csv").read_text().strip().endswith("name")
    # The next prompt gets the next colour, and an existing id is refused.
    other = new_prompt.scaffold("acids", "category", "Name an acid")
    assert load_prompt(other)["colour"] == new_prompt.PALETTE[1]
    with pytest.raises(SystemExit, match="already exists"):
        new_prompt.scaffold("alloys", "category", "Name an alloy")


def test_query_template_writes_a_query(folders):
    folder = new_prompt.scaffold("chemists", "query", "Name a chemist", kind="person")
    assert load_prompt(folder)["query"] == "chemists.rq"
    assert "wd:Q12345" in (folders / "queries" / "chemists.rq").read_text()


def test_ids_must_be_simple(folders):
    with pytest.raises(SystemExit, match="lower case"):
        new_prompt.scaffold("Bad Id", "category", "Name a thing")
