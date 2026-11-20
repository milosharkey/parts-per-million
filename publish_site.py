"""Publish the game to docs/, the folder GitHub Pages serves.

    python publish_site.py           (or the "Publish site" run configuration)
    python publish_site.py --check   only check that the coming days are ready

What it writes:
  docs/index.html, *.js, style.css     the game, copied from game/
  docs/config.json                     the site settings from config.py
  docs/data/bank.json                  the prompts a playtest game deals from
  docs/data/prompts/<id>-<hash>.json   each built prompt, named by its contents
  docs/data/days/<date>.json           each scheduled day, from data/schedule.csv
  docs/.nojekyll                       tells GitHub Pages to serve the files as they are

Prompt files are named by a hash of their contents, so a rebuild adds a new
file instead of changing an old one. A day that has started is never
rewritten: it keeps pointing at the files it was published with, so everyone
playing it is scored against the same list however often you rebuild.
Nothing in docs/data/ is ever deleted (DECISIONS.md, 41 and 42).

The readiness check looks at the next config.READINESS_DAYS days and lists
anything that would stop a day being played as planned: a day with nothing
scheduled, a prompt that isn't built, isn't marked ready, has no gem or is
failing its prompt checks. Run it whenever you change the schedule.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import config
from prompts import PROMPTS

GAME_DIR = config.ROOT / "game"
GAME_FILES = ["index.html", "game.js", "deal.js", "matching.js", "scoring.js", "style.css"]
SCHEDULE = config.DATA_DIR / "schedule.csv"
# Fields of each answer the game doesn't use, left out of the published files
# to keep them small (they stay in results/ for debugging tiers).
UNUSED_FIELDS = ("views", "sitelinks", "curriculum")


def game_date(now: datetime | None = None) -> date:
    """Today's game date: the day changes at DAY_STARTS_UTC_HOUR UTC for everyone."""
    now = now or datetime.now(timezone.utc)
    return (now.astimezone(timezone.utc) - timedelta(hours=config.DAY_STARTS_UTC_HOUR)).date()


def load_result(prompt_id: str) -> dict | None:
    """A prompt's latest build from results/, or None if it hasn't been built."""
    path = config.OUT_DIR / f"{prompt_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def site_settings(local: bool = False) -> dict:
    """What the page reads from config.json. serve.py passes local=True."""
    return {
        "mode": config.SITE_MODE,
        "promptsPerGame": config.PROMPTS_PER_GAME,
        "timeLimit": config.TIME_LIMIT_SECONDS,
        # Locally, games are saved by serve.py into playtests/.
        "resultsUrl": "/playtest" if local else config.SITE_RESULTS_URL,
        "contact": config.SITE_CONTACT,
        "dayStartsUtcHour": config.DAY_STARTS_UTC_HOUR,
        "local": local,   # only locally can ?date=... preview a future day
    }


def publish_prompt(prompt_id: str, result: dict, site: Path) -> str:
    """Write one built prompt under its content hash; return its path in the site."""
    slim = {**result, "answers": [{k: v for k, v in a.items() if k not in UNUSED_FIELDS}
                                  for a in result["answers"]]}
    slim.pop("checks", None)
    body = json.dumps(slim, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:10]
    rel = f"data/prompts/{prompt_id}-{digest}.json"
    path = site / rel
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return rel


def read_schedule(path: Path | None = None) -> dict[date, list[str]]:
    """data/schedule.csv as {date: [prompt ids]}. Lines starting with # are comments."""
    path = path or SCHEDULE
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        lines = [line for line in f if line.strip() and not line.lstrip().startswith("#")]
    days: dict[date, list[str]] = {}
    for row in csv.DictReader(lines):
        day = date.fromisoformat(row["date"].strip())
        days[day] = [p.strip() for p in row["prompts"].split(";") if p.strip()]
    return dict(sorted(days.items()))


def problems_for_day(day: date, schedule: dict[date, list[str]]) -> list[str]:
    """Why a scheduled day couldn't be played as planned (empty if it's fine)."""
    if day not in schedule:
        return ["nothing scheduled"]
    problems = []
    for pid in schedule[day]:
        spec = PROMPTS.get(pid)
        if spec is None:
            problems.append(f"{pid}: no such prompt")
            continue
        if spec["status"] != "ready":
            problems.append(f"{pid}: status is {spec['status']}, not ready")
        result = load_result(pid)
        if result is None:
            problems.append(f"{pid}: not built")
            continue
        if not any(a["tier"] == "single_crystal" for a in result["answers"]):
            problems.append(f"{pid}: no gem in flags.csv")
        failing = [c["check"] for c in result.get("checks", []) if not c["ok"]]
        if failing:
            problems.append(f"{pid}: failing {'; '.join(failing)}")
    if len(set(schedule[day])) < len(schedule[day]):
        problems.append("the same prompt twice")
    return problems


def check_days(schedule: dict[date, list[str]], start: date, days: int) -> int:
    """Print the readiness of the next `days` days; return how many have problems."""
    bad = 0
    print(f"Readiness of the next {days} days (game date today: {start}):")
    for i in range(days):
        day = start + timedelta(days=i)
        problems = problems_for_day(day, schedule)
        bad += bool(problems)
        status = "ok" if not problems else "; ".join(problems)
        print(f"  {day:%a %d %b}  {status}")
    return bad


def publish(site: Path = config.SITE_DIR, quiet: bool = False) -> dict:
    """Write the whole site. Returns a summary (used by serve.py and the tests)."""
    say = (lambda *a: None) if quiet else print
    site.mkdir(parents=True, exist_ok=True)
    for name in GAME_FILES:
        shutil.copy2(GAME_DIR / name, site / name)
    (site / ".nojekyll").touch()
    (site / "config.json").write_text(json.dumps(site_settings(), indent=1), encoding="utf-8")

    # Every built prompt gets a file (days may use any of them); the bank is
    # the ready ones.
    files: dict[str, str] = {}
    bank = []
    for pid, spec in PROMPTS.items():
        result = load_result(pid)
        if result is None:
            if spec["status"] == "ready":
                say(f"  ! {pid} is ready but not built, so it's left out (build it first)")
            continue
        files[pid] = publish_prompt(pid, result, site)
        if spec["status"] == "ready":
            meta = result["prompt"]
            bank.append({
                "id": pid, "text": meta["text"], "label": meta.get("label", pid),
                "colour": meta.get("colour", ""), "subject": meta.get("subject", ""),
                "audience": meta.get("audience", ""), "file": files[pid],
                "answers": len(result["answers"]), "generated": result["generated"],
            })
    (site / "data").mkdir(exist_ok=True)
    (site / "data" / "bank.json").write_text(json.dumps(bank, ensure_ascii=False, indent=1), encoding="utf-8")

    # Days: written from the schedule until they start, then left alone.
    schedule = read_schedule()
    today = game_date()
    first = min(schedule) if schedule else today
    written = frozen = skipped = 0
    for day, ids in schedule.items():
        path = site / "data" / "days" / f"{day.isoformat()}.json"
        if day <= today and path.exists():
            frozen += 1
            continue
        missing = [p for p in ids if p not in files]
        if missing:
            if day >= today:
                say(f"  ! {day}: not published, {', '.join(missing)} not built")
            skipped += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "date": day.isoformat(), "lot": f"{(day - first).days + 1:03d}",
            "prompts": [{"id": p, "file": files[p]} for p in ids],
        }, indent=1), encoding="utf-8")
        written += 1

    say(f"Published to {site.relative_to(config.ROOT) if site.is_relative_to(config.ROOT) else site}/: "
        f"{len(bank)} prompts in the bank, {len(files)} prompt files, "
        f"{written} days written, {frozen} days already live and left as they were"
        + (f", {skipped} days skipped" if skipped else ""))
    if not config.SITE_RESULTS_URL:
        say("  Note: SITE_RESULTS_URL in config.py is empty, so the published page offers a "
            "download at the end instead of sending results (README.md, \"Hosting the playtest\").")
    return {"bank": bank, "files": files, "written": written, "frozen": frozen, "skipped": skipped}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only check the coming days")
    args = parser.parse_args(argv)
    schedule = read_schedule()
    if not args.check:
        publish()
        print()
    if config.SITE_MODE == "daily" or schedule:
        bad = check_days(schedule, game_date(), config.READINESS_DAYS)
        if bad:
            print(f"\n{bad} of the next {config.READINESS_DAYS} days need attention.")
            if args.check:
                raise SystemExit(1)
    else:
        print("No schedule yet (data/schedule.csv): fine for playtests, which deal from the bank.")


if __name__ == "__main__":
    main()
