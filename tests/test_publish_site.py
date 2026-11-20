"""publish_site.py: the playtest site, the bank and frozen days."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

import config
import publish_site
from publish_site import game_date, problems_for_day, publish, read_schedule


def fake_result(pid, gem=True, ok=True, answers=30):
    tiers = ["single_crystal" if gem else "analytical_grade"] + ["tap_water"] * (answers - 1)
    return {
        "prompt": {"id": pid, "text": f"Name a {pid}", "label": pid, "colour": "#123456",
                   "subject": "chemistry", "audience": "school"},
        "generated": "2026-11-20T00:00:00+00:00",
        "answers": [{"id": f"Q{i}", "name": f"{pid} {i}", "tier": t, "points": 10, "share": 0.01,
                     "spellings": [f"{pid} {i}"], "views": 5, "sitelinks": 3, "curriculum": 0}
                    for i, t in enumerate(tiers)],
        "aliases": {}, "checks": [{"ok": ok, "check": "size 30 (need 25+)"}],
    }


@pytest.fixture
def site(tmp_path, monkeypatch):
    results = tmp_path / "results"
    results.mkdir()
    for pid in ("elements", "minerals", "enzymes"):
        (results / f"{pid}.json").write_text(json.dumps(fake_result(pid, ok=pid != "enzymes")))
    monkeypatch.setattr(config, "OUT_DIR", results)
    schedule = tmp_path / "schedule.csv"
    monkeypatch.setattr(publish_site, "SCHEDULE", schedule)
    return tmp_path / "docs", schedule


def test_day_changes_at_the_set_hour_for_everyone():
    assert game_date(datetime(2026, 12, 2, 4, 59, tzinfo=timezone.utc)) == date(2026, 12, 1)
    assert game_date(datetime(2026, 12, 2, 5, 0, tzinfo=timezone.utc)) == date(2026, 12, 2)


def test_publish_writes_game_bank_and_hashed_prompts(site, capsys):
    docs, _ = site
    summary = publish(docs)
    assert (docs / "index.html").exists() and (docs / "deal.js").exists() and (docs / ".nojekyll").exists()
    settings = json.loads((docs / "config.json").read_text())
    assert settings["mode"] == config.SITE_MODE and settings["local"] is False
    bank = json.loads((docs / "data" / "bank.json").read_text())
    assert {e["id"] for e in bank} == {"elements", "minerals", "enzymes"}      # ready and built
    published = json.loads((docs / bank[0]["file"]).read_text())
    assert "views" not in published["answers"][0] and "checks" not in published   # slimmed
    assert "is ready but not built" in capsys.readouterr().out                # the other ready ones
    # Rebuilding with different answers adds a new file; the old one stays.
    old = summary["files"]["elements"]
    (config.OUT_DIR / "elements.json").write_text(json.dumps(fake_result("elements", answers=31)))
    new = publish(docs, quiet=True)["files"]["elements"]
    assert new != old and (docs / old).exists() and (docs / new).exists()


def test_days_are_rewritten_until_they_start_then_frozen(site, monkeypatch):
    docs, schedule = site
    schedule.write_text("# a comment\ndate,prompts\n2026-12-01,elements;minerals\n2026-12-02,elements;enzymes\n")
    monkeypatch.setattr(publish_site, "game_date", lambda now=None: date(2026, 11, 30))
    publish(docs, quiet=True)
    day1 = json.loads((docs / "data/days/2026-12-01.json").read_text())
    assert day1["lot"] == "001" and [p["id"] for p in day1["prompts"]] == ["elements", "minerals"]
    assert json.loads((docs / "data/days/2026-12-02.json").read_text())["lot"] == "002"

    # Once 1 December has started, a rebuild doesn't touch it, but 2 December
    # (still to come) picks up the new build.
    monkeypatch.setattr(publish_site, "game_date", lambda now=None: date(2026, 12, 1))
    (config.OUT_DIR / "elements.json").write_text(json.dumps(fake_result("elements", answers=40)))
    publish(docs, quiet=True)
    assert json.loads((docs / "data/days/2026-12-01.json").read_text()) == day1
    day2 = json.loads((docs / "data/days/2026-12-02.json").read_text())
    assert day2["prompts"][0]["file"] != day1["prompts"][0]["file"]


def test_readiness_check_lists_what_would_go_wrong(site):
    _, schedule = site
    schedule.write_text("date,prompts\n2026-12-01,elements;enzymes;lab_equipment;nonsense\n")
    days = read_schedule(schedule)
    problems = problems_for_day(date(2026, 12, 1), days)
    assert any("enzymes: failing" in p for p in problems)          # a prompt check fails
    assert "lab_equipment: not built" in problems
    assert "nonsense: no such prompt" in problems
    assert not any(p.startswith("elements") for p in problems)
    assert problems_for_day(date(2026, 12, 2), days) == ["nothing scheduled"]


def test_schedule_fill_adds_days_with_a_mix_and_keeps_hand_edits(tmp_path, monkeypatch):
    import schedule
    path = tmp_path / "schedule.csv"
    path.write_text("date,prompts\n2026-12-02,elements;minerals\n")    # planned by hand
    monkeypatch.setattr(publish_site, "SCHEDULE", path)
    monkeypatch.setattr(schedule, "SCHEDULE", path)
    added = schedule.fill(3, date(2026, 12, 1), seed=1)
    assert [d for d, _ in added] == [date(2026, 12, 1), date(2026, 12, 3), date(2026, 12, 4)]
    days = read_schedule(path)
    assert days[date(2026, 12, 2)] == ["elements", "minerals"]          # untouched
    for d in (date(2026, 12, 1), date(2026, 12, 3)):
        ids = days[d]
        assert len(set(ids)) == len(ids) == min(config.PROMPTS_PER_GAME, len(schedule.ids_with_status("ready")))
        specialists = sum(schedule.PROMPTS[p].get("audience") == "specialist" for p in ids)
        assert specialists <= schedule.MAX_SPECIALIST


def test_rested_prompts_come_first():
    import random
    import schedule
    bank = schedule.ids_with_status("ready")
    day = date(2026, 12, 10)
    last_used = {pid: day - timedelta(days=1) for pid in bank[:-2]}   # all but two used yesterday
    picked = schedule.plan_day(bank, last_used, day, 2, random.Random(0))
    assert set(picked) == set(bank[-2:])
