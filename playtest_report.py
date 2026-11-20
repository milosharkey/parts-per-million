"""Summarise playtests: what people actually typed, against what the model predicted.

    python playtest_report.py            (or the "Playtest report" run configuration)
    python playtest_report.py some/folder/or/file.json ...

Reads every playtest record in playtests/: files saved there by serve.py,
downloaded files dropped in by hand, and the Google Sheet's "Games" tab
downloaded as CSV (File > Download > Comma-separated values) into the same
folder. A game sent twice (once when it ended, once with feedback) counts
once. For each prompt it prints:

- each answer people gave, how many players gave it, and the tier and share
  the model predicted for it. With a handful of players these are hints,
  not measurements: an Analytical grade answer that two of five people gave
  is worth a look, one that nobody gave proves nothing.
- everything typed that wasn't on the list, grouped and counted. These are
  the most useful lines: missing answers, missing aliases and spellings.
- how often corrections were suggested, accepted and undone, and how many
  prompts were passed or ran out of time.

Then the players' feedback: most fun and most annoying prompts, whether
they'd play daily, and everything they wrote about bugs and ideas.

It also writes results/playtest_report.csv (one row per answer given) for a
closer look in a spreadsheet. See DECISIONS.md, 35 and 41.
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

import config
from aliases import normalise
from prompts import PROMPTS

FOLDER = config.ROOT / "playtests"


FORMATS = ("ppm-playtest-1", "ppm-playtest-2")


def records_in_csv(path: Path) -> list[dict]:
    """Records from the Sheet's Games tab, downloaded as CSV (the record column)."""
    csv.field_size_limit(10_000_000)
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if rows and "record" not in rows[0]:
        print(f"  ! {path.name} has no 'record' column (download the Games tab), skipped")
        return []
    found = []
    for n, row in enumerate(rows, 2):
        try:
            found.append(json.loads(row["record"]))
        except ValueError:
            print(f"  ! {path.name} row {n}: the record isn't readable JSON, skipped")
    return found


def load_records(paths: list[Path]) -> list[dict]:
    """Every playtest record found at these paths (files or folders), one per game."""
    files: list[Path] = []
    for p in paths:
        files += sorted([*p.glob("*.json"), *p.glob("*.csv")]) if p.is_dir() else [p]
    records = []
    for f in files:
        if f.suffix == ".csv":
            records += records_in_csv(f)
            continue
        try:
            record = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            print(f"  ! {f.name} isn't valid JSON, skipped")
            continue
        records.append(record)
    games: dict[str, dict] = {}
    for n, record in enumerate(records):
        if record.get("format") not in FORMATS:
            print("  ! skipped something that isn't a playtest record")
            continue
        # A game is sent when it ends and again with feedback: keep the one
        # with feedback (the later), whichever order the files come in.
        key = record.get("gameId") or f"old-{n}"
        kept = games.get(key)
        if kept is None or (record.get("feedback") and not kept.get("feedback")):
            games[key] = record
    return list(games.values())


def load_predictions(prompt_id: str) -> dict | None:
    """The current build of a prompt from results/, or None if it isn't built."""
    path = config.OUT_DIR / f"{prompt_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def report(records: list[dict]) -> list[list]:
    """Print the summary; return the CSV rows."""
    players = [r.get("player", "anonymous") for r in records]
    print(f"Playtest report: {len(records)} games ({', '.join(players)})\n")
    rows: list[list] = []

    prompt_ids = list(dict.fromkeys(p for r in records for p in r.get("day", [])))
    for prompt_id in prompt_ids:
        games = [r for r in records if prompt_id in r.get("day", [])]
        text = PROMPTS.get(prompt_id, {}).get("text", prompt_id)
        built = load_predictions(prompt_id)
        by_id = {a["id"]: a for a in built["answers"]} if built else {}
        print(f"{text}  ({len(games)} games)")
        if built and any(r.get("builds", {}).get(prompt_id) != built["generated"] for r in games):
            print("  (some games were played on an older build: predictions below are the current build's)")

        given: Counter = Counter()
        outcomes: Counter = Counter()
        refused: Counter = Counter()
        refused_spelling: dict[str, str] = {}
        events: Counter = Counter()
        times: list[float] = []
        for r in games:
            for rnd in r.get("rounds", []):
                if rnd["prompt"] != prompt_id:
                    continue
                outcomes[rnd["how"]] += 1
                if rnd.get("answerId"):
                    given[rnd["answerId"]] += 1
                    rows.append([prompt_id, rnd["answer"], r.get("player"), rnd["tier"],
                                 by_id.get(rnd["answerId"], {}).get("share", "")])
            for a in r.get("attempts", []):
                if a.get("prompt") != prompt_id:
                    continue
                events[a["event"]] += 1
                if a["event"] == "not_on_list":
                    key = normalise(a.get("typed", ""))
                    refused[key] += 1
                    refused_spelling.setdefault(key, a.get("typed", "").strip())
                if a["event"] in ("accepted", "confirmed") and a.get("ms") is not None:
                    times.append(a["ms"] / 1000)

        if given:
            print(f"  {'Given':<34}{'players':>8}   {'predicted tier':<18}{'predicted share':>15}")
            for answer_id, n in given.most_common():
                a = by_id.get(answer_id, {})
                tier = config.TIER_LABELS.get(a.get("tier", ""), "not in current build")
                share = f"{a['share'] * 100:.2f}%" if "share" in a else ""
                print(f"  {a.get('name', answer_id):<34}{f'{n}/{len(games)}':>8}   {tier:<18}{share:>15}")
            rare = [by_id[q]["name"] for q, n in given.items()
                    if n > 1 and by_id.get(q, {}).get("tier") in ("reagent_grade", "analytical_grade")]
            if rare:
                print(f"  Worth a look (rare tiers, but given more than once): {', '.join(rare)}")
        if refused:
            listed = ", ".join(f"\"{refused_spelling[k]}\"" + (f" ×{n}" if n > 1 else "")
                               for k, n in refused.most_common())
            print(f"  Not on the list: {listed}")
        print(f"  Corrections: {events['suggested']} suggested, {events['confirmed']} accepted, "
              f"{events['suggestion_undone']} undone")
        average = f", average time to answer {statistics.mean(times):.1f} s" if times else ""
        print(f"  Passed {outcomes['pass']}, ran out of time {outcomes['time']}{average}\n")
    return rows


def feedback_report(records: list[dict]) -> None:
    """Print what players said: favourite and least favourite prompts, comments."""
    given = [r for r in records if r.get("feedback")]
    if not given:
        return
    text = lambda pid: PROMPTS.get(pid, {}).get("text", pid)
    print(f"Feedback ({len(given)} of {len(records)} games)")
    for field, title in (("fun", "Most fun"), ("annoying", "Most annoying")):
        counts = Counter(r["feedback"].get(field) for r in given if r["feedback"].get(field))
        if counts:
            print(f"  {title}: " + ", ".join(f"{text(p)} ×{n}" for p, n in counts.most_common()))
    daily = Counter(r["feedback"].get("playDaily") for r in given if r["feedback"].get("playDaily"))
    if daily:
        print("  Would play daily: " + ", ".join(f"{k} ×{n}" for k, n in daily.most_common()))
    for field, title in (("broke", "Bugs and oddities"), ("comments", "Other comments")):
        notes = [(r.get("player", "anonymous"), r["feedback"][field]) for r in given if r["feedback"].get(field)]
        if notes:
            print(f"  {title}:")
            for player, note in notes:
                print(f"    - {note.strip()}  ({player})")
    print()


def main(argv: list[str] | None = None) -> None:
    args = [Path(a) for a in (argv if argv is not None else sys.argv[1:])] or [FOLDER]
    records = load_records(args)
    if not records:
        print(f"No playtests found in {', '.join(str(a) for a in args)}. Play the demo through "
              f"serve.py, or drop downloaded playtest files (or the Sheet's Games tab as CSV) into playtests/.")
        return
    rows = report(records)
    feedback_report(records)
    config.OUT_DIR.mkdir(exist_ok=True)
    out = config.OUT_DIR / "playtest_report.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["prompt", "answer", "player", "tier", "predicted_share"])
        w.writerows(rows)
    print(f"Wrote {out.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
