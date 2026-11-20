"""Start a new prompt from a template: python new_prompt.py <id> <template> "<text>"

    python new_prompt.py alloys category "Name an alloy"
    python new_prompt.py chemists query "Name a chemist"
    python new_prompt.py dyes infobox "Name a dye"

Templates (in templates/):
  category   answers are the articles in Wikipedia categories (most prompts)
  query      answers come from a Wikidata query (when Wikidata has a clean class)
  infobox    every article using an infobox, e.g. Template:Chembox (big sets)

It makes data/<id>/ with prompt.toml (status "draft") and every CSV, with
headers and a line on what each is for, plus queries/<id>.rq for the query
template. Then:

  1. Fill in prompt.toml (the category name, subject, audience) and a few
     answers players will definitely say in must_accept.csv.
  2. python build_prompt.py probe <id>: a quick look, no page views. Shows the
     size, the best-known candidates, where they came from and anything odd.
  3. Fix what the probe shows (skip_categories, exclude.csv, extras.csv) and
     probe again until it looks right.
  4. python build_prompt.py <id>: the full build. Review results/<id>_review.csv,
     pick the gem (and a Fool's gold if there's a good one), write their
     blurbs, then set status = "ready" to put it in the bank.

README.md, "Designing a prompt", has the details (DECISIONS.md, 43).
"""
from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path

import config

TEMPLATES = config.ROOT / "templates"
KINDS = {"category": "category.toml", "query": "query.toml", "infobox": "infobox.toml"}

# Label colours for new prompts, used in this order, skipping any a prompt
# already has. All dark enough for white text on the label band, and kept
# clear of the tier colours.
PALETTE = [
    "#3C6E9F", "#2E7D5B", "#7A3E2B", "#1F6F8B", "#9C3D54", "#556B8D", "#4F5D2F", "#8C5A3C",
    "#5B4B8A", "#2D6A4F", "#A0522D", "#37474F", "#00796B", "#AD1457", "#283593", "#558B2F",
    "#00838F", "#BF360C", "#4E342E", "#1565C0", "#6D4C41", "#33691E", "#880E4F", "#004D40",
]

CSV_FILES = {
    "curriculum.csv": "# How early people meet each answer: 3 = school before 16 (GCSE),\n"
                      "# 2 = A-level/IB, 1 = first-year university. name = any name it goes by.\n"
                      "name,level\n",
    "aliases.csv": "# Extra ways players type an answer, on top of Wikidata's aliases.\nname,alias\n",
    "extras.csv": "# Answers the source misses. title = exact English Wikipedia article title.\ntitle,kind\n",
    "exclude.csv": "# Articles the source lists that aren't answers. title = exact Wikipedia title.\ntitle\n",
    "flags.csv": "# Hand-set tiers: single_crystal (the gem), fools_gold, min:<tier>, set:<tier>\n"
                 "# (same as data/minerals/flags.csv). The build suggests gem candidates.\nname,flag\n",
    "blurbs.csv": "# Two sentences shown when a player finds the answer. Always write one\n"
                  "# for the gem and Fool's gold.\nname,blurb\n",
    "must_accept.csv": "# Answers players will definitely give, which must be accepted. The build\n"
                       "# checks them every time (DECISIONS.md, 39). Write them as players would.\nname\n",
}


def used_colours() -> set[str]:
    colours = set()
    for path in config.DATA_DIR.glob("*/prompt.toml"):
        with path.open("rb") as f:
            colours.add(tomllib.load(f).get("colour", "").upper())
    return colours


def next_colour() -> str:
    used = used_colours()
    return next((c for c in PALETTE if c.upper() not in used), PALETTE[len(used) % len(PALETTE)])


def default_label(text: str) -> str:
    """'Name an alloy' -> 'Alloys': a guess at the short name for the label band."""
    words = re.sub(r"^Name (?:a|an|the|any)\s+", "", text).split()
    label = " ".join(words[:3]).capitalize()
    return label if label.endswith("s") else label + "s"


def scaffold(prompt_id: str, template: str, text: str, label: str | None = None,
             category: str = "...", kind: str = "answer") -> Path:
    """Make data/<id>/ from a template. Refuses to overwrite an existing prompt."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", prompt_id):
        raise SystemExit("The id must be lower case letters, digits and underscores, e.g. ph_indicators.")
    folder = config.DATA_DIR / prompt_id
    if folder.exists():
        raise SystemExit(f"data/{prompt_id}/ already exists: pick another id, or edit that one.")
    fields = {"id": prompt_id, "text": text, "label": label or default_label(text),
              "colour": next_colour(), "category": category.removeprefix("Category:"), "kind": kind}
    body = (TEMPLATES / KINDS[template]).read_text(encoding="utf-8")
    for key, value in fields.items():
        body = body.replace("{" + key + "}", value.replace('"', '\\"'))
    folder.mkdir(parents=True)
    (folder / "prompt.toml").write_text(body, encoding="utf-8")
    for name, header in CSV_FILES.items():
        (folder / name).write_text(header, encoding="utf-8")
    if template == "query":
        query = config.QUERY_DIR / f"{prompt_id}.rq"
        if not query.exists():
            query.write_text((TEMPLATES / "query.rq").read_text(encoding="utf-8"), encoding="utf-8")
    return folder


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("id", help="short name, lower case with underscores: the folder's name")
    parser.add_argument("template", choices=sorted(KINDS))
    parser.add_argument("text", help='what the player sees, e.g. "Name an alloy"')
    parser.add_argument("--label", help="short name for the label band (default: guessed from the text)")
    parser.add_argument("--category", default="...", help="for the category template: the category name")
    args = parser.parse_args(argv)
    folder = scaffold(args.id, args.template, args.text, args.label, args.category)
    print(f"Made {folder.relative_to(config.ROOT)}/ from templates/{KINDS[args.template]}.")
    if args.template == "query":
        print(f"Write the query in queries/{args.id}.rq.")
    print(f"Next: fill in prompt.toml and must_accept.csv, then python build_prompt.py probe {args.id}")


if __name__ == "__main__":
    main()
