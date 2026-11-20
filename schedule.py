"""Plan the daily puzzles: data/schedule.csv, one row per day.

    python schedule.py                      show the next few weeks
    python schedule.py fill 28              plan the next 28 unplanned days
    python schedule.py fill 28 --from 2026-12-01

Each row is a date and that day's prompts, separated by semicolons:

    date,prompts
    2026-12-01,elements;minerals;named_reactions;lab_equipment;aromatics;enzymes;pigments

Edit it by hand as much as you like: fill only adds days that aren't planned
yet, and never changes a planned one. It picks from the bank (status =
"ready"), mixing subjects the same way a playtest deal does (no more than 3
of one subject or 2 specialist prompts a day, and one prompt per family:
prompt.toml, family) and not repeating a prompt within
config.SCHEDULE_MIN_GAP_DAYS days when the bank is big enough to avoid it;
when it isn't, the prompts used longest ago come first. A family isn't
played on two days running either, so minerals on Monday means no
gemstones on Tuesday.

Then run Publish site, which writes each day's file and checks the next two
weeks are ready (DECISIONS.md, 42).
"""
from __future__ import annotations

import argparse
import random
from datetime import date, timedelta

import config
from prompts import PROMPTS, ids_with_status
from publish_site import SCHEDULE, game_date, read_schedule

MAX_PER_SUBJECT = 3     # same rules as game/deal.js
MAX_SPECIALIST = 2


def plan_day(bank: list[str], last_used: dict[str, date], day: date, n: int, rng: random.Random,
             family_used: dict[str, date] | None = None) -> list[str]:
    """Pick n prompts for one day: rested ones first, then a good mix.

    family_used: the last day each family was played, so a family isn't
    played two days running.
    """
    family_used = family_used or {}
    gap = config.SCHEDULE_MIN_GAP_DAYS
    def rest(pid: str) -> int:
        used = last_used.get(pid)
        return 10_000 if used is None else (day - used).days
    shuffled = bank[:]
    rng.shuffle(shuffled)
    # Prompts rested for the full gap, in random order, then the rest by how
    # long ago they were used.
    rested = [p for p in shuffled if rest(p) >= gap]
    tired = sorted((p for p in shuffled if rest(p) < gap), key=rest, reverse=True)
    picked, skipped, subjects, specialists, families = [], [], {}, 0, set()
    yesterday = day - timedelta(days=1)
    for pid in rested + tired:
        if len(picked) == n:
            break
        spec = PROMPTS[pid]
        subject, hard = spec.get("subject", ""), spec.get("audience") == "specialist"
        family = spec.get("family", "")
        clash = bool(family) and (family in families or family_used.get(family) == yesterday)
        if subjects.get(subject, 0) >= MAX_PER_SUBJECT or (hard and specialists >= MAX_SPECIALIST) or clash:
            skipped.append(pid)
            continue
        picked.append(pid)
        subjects[subject] = subjects.get(subject, 0) + 1
        specialists += hard
        if family:
            families.add(family)
    # Fill any gap with what was skipped, family clashes last (as deal.js).
    def clashes(pid: str) -> bool:
        family = PROMPTS[pid].get("family", "")
        return bool(family) and (family in families or family_used.get(family) == yesterday)
    for pid in [p for p in skipped if not clashes(p)] + [p for p in skipped if clashes(p)]:
        if len(picked) == n:
            break
        picked.append(pid)
        if PROMPTS[pid].get("family"):
            families.add(PROMPTS[pid]["family"])
    return picked


def fill(days: int, start: date, seed: int | None = None) -> list[tuple[date, list[str]]]:
    """Add plans for the next `days` unplanned days from `start`; return the new rows."""
    bank = ids_with_status("ready")
    if not bank:
        raise SystemExit("The bank is empty: mark some prompts ready (status in prompt.toml) first.")
    schedule = read_schedule()
    last_used: dict[str, date] = {}
    family_used: dict[str, date] = {}
    for day, ids in schedule.items():
        for pid in ids:
            last_used[pid] = max(day, last_used.get(pid, day))
            family = PROMPTS.get(pid, {}).get("family")
            if family:
                family_used[family] = max(day, family_used.get(family, day))
    rng = random.Random(seed)
    added = []
    day = start
    while len(added) < days:
        if day not in schedule:
            ids = plan_day(bank, last_used, day, config.PROMPTS_PER_GAME, rng, family_used)
            added.append((day, ids))
            for pid in ids:
                last_used[pid] = day
                if PROMPTS[pid].get("family"):
                    family_used[PROMPTS[pid]["family"]] = day
        day += timedelta(days=1)
    if added:
        new = not SCHEDULE.exists()
        with SCHEDULE.open("a", encoding="utf-8") as f:
            if new:
                f.write("# The daily puzzles: a date and its prompts, separated by semicolons.\n"
                        "# Edit freely; schedule.py fill only adds days that aren't planned yet.\n"
                        "date,prompts\n")
            for d, ids in added:
                f.write(f"{d.isoformat()},{';'.join(ids)}\n")
    return added


def show(days: int = 21) -> None:
    schedule = read_schedule()
    today = game_date()
    print(f"Game date today: {today} (the day changes at {config.DAY_STARTS_UTC_HOUR:02d}:00 UTC)")
    for i in range(days):
        d = today + timedelta(days=i)
        ids = schedule.get(d)
        print(f"  {d:%a %d %b}  {', '.join(ids) if ids else '(nothing planned)'}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    f = sub.add_parser("fill", help="plan the next N unplanned days")
    f.add_argument("days", type=int)
    f.add_argument("--from", dest="start", type=date.fromisoformat, help="first date (default: today's game date)")
    f.add_argument("--seed", type=int, help="fix the random choices, to repeat a plan")
    args = parser.parse_args(argv)
    if args.command == "fill":
        added = fill(args.days, args.start or game_date(), args.seed)
        print(f"Planned {len(added)} days in {SCHEDULE.relative_to(config.ROOT)}:")
        for d, ids in added:
            print(f"  {d:%a %d %b}  {', '.join(ids)}")
        print("\nNext: run Publish site to write the days and check they're ready.")
    else:
        show()


if __name__ == "__main__":
    main()
