"""playtest_report.py: summarising what playtesters typed."""
import json

import config
import playtest_report


def test_report_counts_answers_refusals_and_corrections(tmp_path, monkeypatch, capsys):
    results = tmp_path / "results"
    results.mkdir()
    (results / "elements.json").write_text(json.dumps({
        "generated": "2026-11-19T20:00:00+00:00",
        "answers": [{"id": "Q1", "name": "Iron", "tier": "tap_water", "share": 0.06},
                    {"id": "Q2", "name": "Technetium", "tier": "analytical_grade", "share": 0.0008}]}))
    monkeypatch.setattr(config, "OUT_DIR", results)
    monkeypatch.setattr(config, "ROOT", tmp_path)

    def game(player, answer_id, answer):
        return {"format": "ppm-playtest-1", "player": player, "day": ["elements"],
                "builds": {"elements": "2026-11-19T20:00:00+00:00"},
                "rounds": [{"prompt": "elements", "how": "exact", "answerId": answer_id,
                            "answer": answer, "tier": "x", "typed": answer}],
                "attempts": [{"prompt": "elements", "event": "not_on_list", "typed": "Unobtainium"},
                             {"prompt": "elements", "event": "suggested", "typed": "irn"},
                             {"prompt": "elements", "event": "accepted", "typed": answer, "ms": 4000}]}
    folder = tmp_path / "playtests"
    folder.mkdir()
    for i, (q, name) in enumerate([("Q1", "Iron"), ("Q2", "Technetium"), ("Q2", "Technetium")]):
        (folder / f"p{i}.json").write_text(json.dumps(game(f"player{i}", q, name)))
    (folder / "junk.json").write_text("not json")

    playtest_report.main([str(folder)])
    out = capsys.readouterr().out
    assert "3 games" in out
    assert "Technetium" in out and "2/3" in out
    assert "Worth a look (rare tiers, but given more than once): Technetium" in out
    assert '"Unobtainium" ×3' in out                  # refusals grouped and counted
    assert "3 suggested, 0 accepted" in out
    assert (results / "playtest_report.csv").read_text().count("\n") == 4   # header + 3 answers


def test_sheet_export_and_feedback(tmp_path, monkeypatch, capsys):
    import csv
    results = tmp_path / "results"
    results.mkdir()
    (results / "elements.json").write_text(json.dumps({
        "generated": "g", "answers": [{"id": "Q1", "name": "Iron", "tier": "tap_water", "share": 0.06}]}))
    monkeypatch.setattr(config, "OUT_DIR", results)
    monkeypatch.setattr(config, "ROOT", tmp_path)

    def record(kind, feedback=None):
        return {"format": "ppm-playtest-2", "kind": kind, "gameId": "g1", "player": "Ada", "day": ["elements"],
                "builds": {"elements": "g"}, "rounds": [{"prompt": "elements", "how": "exact", "answerId": "Q1",
                                                         "answer": "Iron", "tier": "tap_water", "typed": "iron"}],
                "attempts": [], "feedback": feedback}
    folder = tmp_path / "playtests"
    folder.mkdir()
    with (folder / "Games.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["received", "game_id", "kind", "record"])
        w.writerow(["t1", "g1", "result", json.dumps(record("result"))])
        w.writerow(["t2", "g1", "feedback", json.dumps(record("feedback", {
            "fun": "elements", "annoying": "", "playDaily": "yes", "broke": "Iridium wasn't accepted",
            "comments": ""}))])
        w.writerow(["t3", "g2", "result", "{not json"])

    playtest_report.main([str(folder)])
    out = capsys.readouterr().out
    assert "1 games" in out                          # sent twice, counted once
    assert "Most fun: Name a chemical element ×1" in out
    assert "Would play daily: yes ×1" in out
    assert "- Iridium wasn't accepted  (Ada)" in out
    assert "row 4: the record isn't readable JSON" in out
