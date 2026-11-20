"""Build one prompt's answer set.

    python build_prompt.py minerals                  live run (needs internet)
    python build_prompt.py minerals --refresh        re-query Wikidata too
    python build_prompt.py minerals --fixtures tests/fixtures   offline test data

Writes results/<prompt>.json (for the game) and results/<prompt>_review.csv (for
your hand pass), then prints a summary and the automatic prompt checks.

This file is the story of a build, start to finish. The real work happens
in sources.py (fetching), scoring.py (the rarity model) and aliases.py
(spellings). The stages in build() are numbered to match the README.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
import math
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

import config
from structure import has_benzene_ring
from aliases import build_alias_index, descriptor_base, normalise, spelled_forms, suffix_base
from prompts import PROMPTS, ids_with_status
from scoring import (
    TIER_ORDER, calibrate_beta, familiarity, predicted_shares, tiers_with_floors, zscores,
)
from sources import FixtureSource, IncompleteDownload, LiveSource, QueryTooSlow, pageview_window

# Looks answers up by their exact English Wikipedia title: the extras in
# extras.csv, and every article of a prompt built from a Wikipedia category.
# Matching on the title (schema:name) rather than building article URLs
# avoids guessing how Wikidata encodes characters like apostrophes in URLs.
# {{ and }} are literal braces in a Python format string.
TITLES_QUERY = """
SELECT ?item ?label ?title ?sitelinks
       (GROUP_CONCAT(DISTINCT ?alias; separator="|") AS ?aliases)
WHERE {{
  VALUES ?title {{ {titles} }}
  ?article schema:name ?title ;
           schema:isPartOf <https://en.wikipedia.org/> ;
           schema:about ?item .
  ?item wikibase:sitelinks ?sitelinks ;
        rdfs:label ?label .
  FILTER(LANG(?label) = "en")
  OPTIONAL {{ ?item skos:altLabel ?alias . FILTER(LANG(?alias) = "en") }}
}}
GROUP BY ?item ?label ?title ?sitelinks
"""


# ---- Reading the hand-edited CSVs ---------------------------------------
# Hand edits live in CSVs rather than in code so a non-programmer (or you,
# in a spreadsheet app) can curate prompts, and so every judgement call is
# visible in one place and tracked by version control.

def read_csv(path: Path) -> list[dict]:
    """Rows of a CSV as dicts. Lines starting with # are comments.

    A missing file is the same as an empty one: every CSV is optional.
    """
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        lines = [line for line in f if line.strip() and not line.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def sparql_string(text: str) -> str:
    """A Python string as an English-tagged SPARQL literal, quotes escaped."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"@en'


# Prompts built from Wikipedia lists can have tens of thousands of articles,
# most of which the filters then throw away (no benzene ring, too few
# languages, too few views). So the lookup is done in two passes
# (DECISIONS.md, 36):
#   1. a light one for every article: just the item, its sitelinks and, for
#      prompts defined by structure, its SMILES (canonical P233 or isomeric
#      P2017; structure.py reads them);
#   2. names and aliases, only for the answers that survive the filters.
# Aliases are what make a query heavy: a common drug can have dozens of
# brand names in English alone.
TITLES_LIGHT_QUERY = """
SELECT ?item ?title ?sitelinks
WHERE {{
  VALUES ?title {{ {values} }}
  ?article schema:name ?title ;
           schema:isPartOf <https://en.wikipedia.org/> ;
           schema:about ?item .
  ?item wikibase:sitelinks ?sitelinks .
}}
"""

TITLES_LIGHT_SMILES_QUERY = """
SELECT ?item ?title ?sitelinks (SAMPLE(?smi) AS ?smiles)
WHERE {{
  VALUES ?title {{ {values} }}
  ?article schema:name ?title ;
           schema:isPartOf <https://en.wikipedia.org/> ;
           schema:about ?item .
  ?item wikibase:sitelinks ?sitelinks .
  OPTIONAL {{ {{ ?item wdt:P233 ?smi }} UNION {{ ?item wdt:P2017 ?smi }} }}
}}
GROUP BY ?item ?title ?sitelinks
"""

NAMES_QUERY = """
SELECT ?item ?label (GROUP_CONCAT(DISTINCT ?alias; separator="|") AS ?aliases)
WHERE {{
  VALUES ?item {{ {values} }}
  OPTIONAL {{ ?item rdfs:label ?label . FILTER(LANG(?label) = "en") }}
  OPTIONAL {{ ?item skos:altLabel ?alias . FILTER(LANG(?alias) = "en") }}
}}
GROUP BY ?item ?label
"""

# Which of these items have at least one of some properties (prompt.toml,
# require_properties). {path} is filled in first, e.g. "wdt:P274|wdt:P662".
HAS_PROPERTIES_QUERY = """
SELECT DISTINCT ?item
WHERE {{
  VALUES ?item {{ {values} }}
  ?item {path} ?value .
}}
"""

BATCH_SIZE = 200   # titles or items per query; batches that are too slow split themselves


def lookup_batches(source, name: str, values: list[str], template: str) -> list[dict]:
    """Run a query over a long list of values in batches, and combine the rows.

    values are already SPARQL terms ('"Benzene"@en', 'wd:Q2270'). Each batch
    is its own cached query, named <name>_<n>, so a stopped run keeps the
    batches it finished and carries on from the next one.

    If Wikidata gives up on a batch (QueryTooSlow: past its 60-second limit),
    the batch is split in half and each half tried, splitting again if need
    be. The halves' rows are then also cached as the whole batch, so the next
    run doesn't try the slow version again.
    """
    def run(batch_name: str, batch: list[str]) -> list[dict]:
        query = template.format(values=" ".join(batch))
        try:
            return source.sparql(batch_name, query)
        except QueryTooSlow:
            if len(batch) == 1:
                raise
            half = len(batch) // 2
            print(f"  {batch_name} was too slow for Wikidata; splitting it in two")
            rows = run(batch_name + "a", batch[:half]) + run(batch_name + "b", batch[half:])
            source.remember(batch_name, query, rows)
            return rows

    batches = [values[i:i + BATCH_SIZE] for i in range(0, len(values), BATCH_SIZE)]
    names = [name if len(batches) == 1 else f"{name}_{n}" for n in range(1, len(batches) + 1)]
    saved = [source.is_cached(nm, template.format(values=" ".join(b)))
             for nm, b in zip(names, batches)]
    if len(batches) > 9:
        # Say up front what an earlier, stopped run already fetched, so a
        # rerun visibly carries on rather than seeming to start again.
        if all(saved):
            print(f"  Wikidata lookups: all {len(batches)} batches saved from an earlier run")
        elif any(saved):
            print(f"  Wikidata lookups: {sum(saved)} of {len(batches)} batches saved from "
                  f"an earlier run; carrying on from batch {saved.index(False) + 1}")
    rows: list[dict] = []
    for n, (batch_name, batch, done) in enumerate(zip(names, batches, saved), 1):
        if len(batches) > 9 and not done and (n == 1 or n % 10 == 0):
            print(f"  Wikidata lookups: batch {n} of {len(batches)}...")
        rows += run(batch_name, batch)
    return rows


def lookup_titles(source, name: str, titles: list[str], smiles: bool = False) -> list[dict]:
    """Light rows (item, title, sitelinks, and smiles if asked) for article titles."""
    template = TITLES_LIGHT_SMILES_QUERY if smiles else TITLES_LIGHT_QUERY
    return lookup_batches(source, name, [sparql_string(t) for t in titles], template)


def lookup_names(source, name: str, item_ids: list[str]) -> dict[str, dict]:
    """English label and aliases for Wikidata items: {id: {"label", "aliases"}}."""
    rows = lookup_batches(source, f"{name}_names", [f"wd:{q}" for q in item_ids], NAMES_QUERY)
    return {r["item"].rsplit("/", 1)[-1]: r for r in rows}


# ---- The build ------------------------------------------------------------

def display_name(title: str) -> str:
    """The name shown to players: the Wikipedia title minus any disambiguation.

    Titles read better than Wikidata's labels ("Garnet" rather than "garnet
    group", "Mercury" rather than "mercury"), and dropping the bracketed
    disambiguation turns "Mercury (element)" into "Mercury". Only brackets
    after a space are dropped, so "Monazite-(Ce)" keeps its suffix.
    """
    return re.sub(r"\s+\([^)]*\)$", "", title).strip()


GREEK_CAPITALS = "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"


def fix_first_letter(name: str, label: str) -> str:
    """Undo Wikipedia's capital first letter where the real name has none.

    Every Wikipedia title starts with a capital, so the pH meter is titled
    "PH meter", mTOR "MTOR", p-xylene "P-Xylene" and α-amylase "Α-Amylase"
    (a Greek capital alpha, which reads as an A). Wikidata's labels keep the
    real case. The first letter is lowered when:
      - it's a Greek capital followed by something that isn't a letter
        ("Α-Amylase", "Δ13C"): Greek prefixes in chemistry are lower case;
      - or the label starts with the same letter in lower case and isn't an
        ordinary word: its next character is a capital or not a letter
        ("pH meter", "mTOR", "p-xylene", "n-butyllithium"). Ordinary words
        ("microscope") keep the title's capital, and so do labels that are
        capitalised themselves ("N-bromosuccinimide").
    """
    if not name or not name[0].isupper():
        return name
    if name[0] in GREEK_CAPITALS and (len(name) == 1 or not name[1].isalpha()):
        return name[0].lower() + name[1:]
    if (label and len(label) > 1 and label[0] == name[0].lower()
            and (label[1].isupper() or not label[1].isalpha())
            and normalise(label) == normalise(name)):
        return label[0] + name[1:]
    return name


def keep_with_properties(spec: dict, source, answers: dict[str, dict]) -> dict[str, dict]:
    """Only the answers whose Wikidata item has one of spec["require_properties"].

    Categories mix things with ideas: "Category:Fatty acids" holds stearic
    acid but also beta oxidation, and "Category:Sugars" a sweet called Pop
    Rocks. For a prompt about substances, requiring a chemical formula (P274),
    SMILES (P233), CAS number (P231) or PubChem id (P662) keeps the
    substances and drops the processes, diseases, people and products in one
    go. Extras always stay, so a mixture like turpentine can be added back by
    hand. The lookup is cached like any other.
    """
    props = spec.get("require_properties")
    if not props:
        return answers
    bad = [p for p in props if not re.fullmatch(r"P\d+", p)]
    if bad:
        raise ValueError(f"require_properties: {', '.join(bad)} aren't Wikidata property ids (like P274)")
    template = HAS_PROPERTIES_QUERY.replace("{path}", "|".join(f"wdt:{p}" for p in props))
    rows = lookup_batches(source, f"{spec['id']}_has_properties", [f"wd:{q}" for q in answers], template)
    has = {r["item"].rsplit("/", 1)[-1] for r in rows}
    kept = {q: a for q, a in answers.items() if q in has or a["extra"]}
    dropped = sorted((a for q, a in answers.items() if q not in kept), key=lambda a: -a["sitelinks"])
    if dropped:
        print(f"  {len(dropped)} have none of {', '.join(props)} on Wikidata, so they're left out "
              f"(e.g. {', '.join(a['title'] for a in dropped[:4])})")
    return kept


def clean_aliases(spec: dict, label: str, aliases: str) -> tuple[list[str], int]:
    """An answer's aliases, minus any matching the prompt's drop_alias_patterns.

    Wikidata's own label comes first as an alias, so "garnet group" still
    counts. Returns the aliases kept and how many were dropped.
    """
    drop = [re.compile(p) for p in spec.get("drop_alias_patterns", [])]
    names = [n for n in [label, *aliases.split("|")] if n]
    kept = [n for n in names if not any(p.match(normalise(n)) for p in drop)]
    return kept, len(names) - len(kept)


# Titles that are never an answer, whatever the prompt (collect_answers).
NOT_ANSWERS = (
    r"^(?:Lists?|Index|Outline|Timeline|Glossary|History|Bibliography) of ",
    r"\(disambiguation\)$",
)


def collect_answers(spec: dict, source, data_dir: Path) -> dict[str, dict]:
    """Candidates from the main query plus extras.csv, keyed by Wikidata id.

    Keying by Wikidata id (Q-number) rather than by name means two answers
    that share a name are never merged by accident, and one that appears in
    both queries is only counted once.
    """
    if "query" in spec:
        query = (config.QUERY_DIR / spec["query"]).read_text()
        rows = source.sparql(spec["id"], query)
    else:
        # A prompt built from Wikipedia: list the articles in some categories
        # or using some infoboxes through Wikipedia's API, then look them up
        # on Wikidata by title.
        if "category" in spec:
            titles = source.category_members(spec["id"], spec["category"],
                                             spec.get("category_depth", 0),
                                             tuple(spec.get("skip_categories", ())))
        else:
            titles = source.template_users(spec["id"], spec["templates"])
        print(f"  {len(titles)} articles listed on Wikipedia")
        rows = lookup_titles(source, spec["id"], titles, smiles=spec.get("require_benzene", False))

    # A prompt defined by structure keeps only compounds whose SMILES has a
    # benzene ring (structure.py). No SMILES on Wikidata means no answer.
    if spec.get("require_benzene"):
        before = len(rows)
        rows = [r for r in rows if has_benzene_ring(r.get("smiles", ""))]
        print(f"  {len(rows)} of {before} have a benzene ring")

    extras = read_csv(data_dir / "extras.csv")
    kinds = {row["title"]: row.get("kind", "extra") for row in extras}
    if extras:
        titles = " ".join(sparql_string(row["title"]) for row in extras)
        extra_rows = source.sparql(f"{spec['id']}_extras", TITLES_QUERY.format(titles=titles))
        # Report titles that didn't resolve, so a typo in the CSV is visible
        # rather than silently dropping an answer.
        found = {r["title"] for r in extra_rows}
        for title in kinds:
            if title not in found:
                print(f"  ! extras.csv: no Wikipedia article titled '{title}' found")
        for r in extra_rows:
            r["kind"] = kinds.get(r["title"], "extra")
            r["extra"] = True   # hand-picked: exempt from the notability floors
        rows = rows + extra_rows

    # exclude.csv drops titles the query returns that don't belong (a reagent
    # filed under "Name reactions", say). Unmatched titles are reported.
    excluded = {row["title"] for row in read_csv(data_dir / "exclude.csv")}
    for title in excluded - {r["title"] for r in rows}:
        print(f"  ! exclude.csv: '{title}' isn't in the query results")

    # Wikipedia's lists, outlines, timelines and glossaries are never an
    # answer ("List of inorganic pigments"), nor are disambiguation pages.
    # drop_title_patterns (prompt.toml) adds a prompt's own, such as
    # "^Isotopes of " for the isotopes prompt. Applied after the lookups, so
    # the cached lookups stay valid.
    patterns = [re.compile(p) for p in (*NOT_ANSWERS, *spec.get("drop_title_patterns", []))]
    not_answers = {r["title"] for r in rows if not r.get("extra") and any(p.search(r["title"]) for p in patterns)}
    if not_answers:
        print(f"  left out {len(not_answers)} list, outline or similar pages "
              f"(e.g. {', '.join(sorted(not_answers)[:3])})")
    excluded |= not_answers

    via = source.category_sources(spec["id"]) if "category" in spec else {}

    # drop_alias_patterns (prompts.py) removes aliases that shouldn't count,
    # such as the placeholder names of elements ("ununoctium").
    dropped = 0

    answers: dict[str, dict] = {}
    for r in rows:
        qid = r["item"].rsplit("/", 1)[-1]   # ".../entity/Q43010" -> "Q43010"
        if r["title"] in excluded:
            continue
        if qid in answers:
            # An extra the source also lists is still hand-picked, so it keeps
            # an extra's exemptions (the floors, require_properties). Without
            # this, penicillin was listed by the category, its extra skipped
            # as a repeat, and the formula filter then dropped it.
            if r.get("extra"):
                answers[qid]["extra"] = True
            continue
        # Rows from the light lookup (prompts built from Wikipedia lists) have
        # no names yet: those are fetched after the filters (build, stage 3b).
        if "label" in r:
            aliases, n = clean_aliases(spec, r["label"], r.get("aliases", ""))
            dropped += n
        else:
            aliases = None
        answers[qid] = {
            "id": qid,
            "name": display_name(r["title"]),
            "title": r["title"],
            "sitelinks": int(r["sitelinks"]),
            "kind": r.get("kind", spec.get("kind", "species")),
            "extra": r.get("extra", False),
            "raw_aliases": aliases,
            "label": r.get("label"),
            "via": "extras.csv" if r.get("extra") else via.get(r["title"], ""),
        }
    if dropped:
        print(f"  dropped {dropped} aliases matching drop_alias_patterns")
    return answers


def name_lookup(answers: dict[str, dict]) -> dict[str, dict]:
    """Normalised name -> answer, for matching the hand-edited CSVs.

    Every name an answer goes by counts (display name, Wikipedia title,
    Wikidata label and aliases), so a CSV can say "garnet" or "biotite" or
    "haematite" without knowing exactly how Wikidata spells it. Display
    names are added first and never overwritten, so an answer's own name
    always beats someone else's alias.
    """
    lookup: dict[str, dict] = {}
    for a in answers.values():
        lookup.setdefault(normalise(a["name"]), a)
    for a in answers.values():
        for other in [a["title"], *a["raw_aliases"]]:
            for form in spelled_forms(other):
                lookup.setdefault(form, a)
    return lookup


def read_flags(path: Path, lookup: dict[str, dict]) -> None:
    """Apply flags.csv: gem, Fool's gold, minimum or fixed tiers, view weights.

    Sets a["flag"] (single_crystal or fools_gold), a["min_tier"], and for
    set:<tier> both a["min_tier"] and a["set_tier"]. Stage 6 explains the
    difference between the two kinds of hand-set tier. views:<factor>
    (between 0 and 1) scales the answer's page views for the familiarity
    score, as view_discounts does for a template (stage 2c).
    """
    for r in read_csv(path):
        a = lookup.get(normalise(r["name"]))
        flag = r["flag"].strip()
        if a is None:
            print(f"  ! flags.csv: '{r['name']}' isn't in the answer set")
        elif flag in ("single_crystal", "fools_gold"):
            a["flag"] = flag
        elif flag.startswith("min:") and flag[4:] in TIER_ORDER:
            a["min_tier"] = flag[4:]
        elif flag.startswith("set:") and flag[4:] in TIER_ORDER:
            a["min_tier"] = a["set_tier"] = flag[4:]
        elif re.fullmatch(r"views:(0(\.\d+)?|1(\.0+)?|\.\d+)", flag) and float(flag[6:]) > 0:
            # One answer's page views count for less, like view_discounts
            # does for a whole template: Margaret Thatcher's article is read
            # for her politics, not her chemistry.
            a["view_weight"] = min(a.get("view_weight", 1.0), float(flag[6:]))
        else:
            print(f"  ! flags.csv: unknown flag '{flag}' for '{r['name']}' (use single_crystal, "
                  f"fools_gold, min:<tier>, set:<tier> or views:<factor>, e.g. min:reagent_grade "
                  f"or views:0.1)")


PARTICLES = {"van", "von", "de", "der", "den", "du", "da", "di", "le", "la", "'t", "ter", "ten", "dos", "del"}
SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}


def surname(name: str) -> str | None:
    """A person's surname with any particles: "van 't Hoff", "Curie". None for one word."""
    words = re.sub(r"\s\([^)]*\)$", "", name).replace(",", "").split()
    while words and words[-1].lower() in SUFFIXES:
        words.pop()
    if len(words) < 2:
        return None
    i = len(words) - 1
    while i > 1 and words[i - 1].lower() in PARTICLES:
        i -= 1
    return " ".join(words[i:])


def counted_views(a: dict) -> float:
    """Page views as the familiarity score sees them, after any discount (2c)."""
    return a["views"] * a.get("view_weight", 1.0)


def tell_apart(ordered: list[dict]) -> int:
    """Rename answers whose name a more familiar answer already has.

    Wikidata labels six different enzymes "alcohol dehydrogenase", and both
    the lab condenser and the heat-transfer one "condenser". Their Wikipedia
    titles carry the bracketed qualifier, so the less familiar ones are shown
    by those ("Alcohol dehydrogenase (quinone)"). The most familiar keeps the
    plain name, and with it the plain spelling: matching ignores bracketed
    qualifiers, so "condenser" means the more familiar one either way. This
    only changes what the results list shows. ordered must be most familiar
    first. Returns how many were renamed.
    """
    taken: set[str] = set()
    renamed = 0
    for a in ordered:
        key = normalise(a["name"])
        if key in taken and a["title"] != a["name"]:
            a["name"] = a["title"]
            renamed += 1
        taken.add(key)
    return renamed


def build(prompt_id: str, source, out_dir: Path) -> dict:
    """Run every stage for one prompt and write the output files."""
    spec = dict(PROMPTS[prompt_id], id=prompt_id)
    data_dir = config.DATA_DIR / spec["data_dir"]
    print(f"Building '{spec['text']}'")

    # 1. Who counts as an answer: Wikidata query + extras.csv.
    answers = collect_answers(spec, source, data_dir)
    print(f"  {len(answers)} candidates")
    if not answers:
        print("  No answers found, so nothing was written. Check the source in prompt.toml "
              "(a category name is easiest to check on Wikipedia itself), or try: "
              f"python build_prompt.py probe {prompt_id}")
        return {}

    # 1b. Prompts about substances can require a chemical formula or
    #     similar on Wikidata (require_properties): see keep_with_properties.
    answers = keep_with_properties(spec, source, answers)

    # 2. Pre-filter for big prompts: answers covered by fewer than
    #    min_sitelinks language Wikipedias are dropped before their page views
    #    are fetched, which is the slow part (half a second or so each).
    #    Extras from extras.csv are hand-picked, so they always stay.
    links_floor = spec.get("min_sitelinks", 0)
    if links_floor:
        before = len(answers)
        answers = {q: a for q, a in answers.items() if a["sitelinks"] >= links_floor or a["extra"]}
        print(f"  {len(answers)} of {before} have articles in {links_floor}+ languages")

    # 2b. How familiar each one is: page views over the window.
    start, end = pageview_window(last_month=config.PAGEVIEW_LAST_MONTH)
    views = source.pageviews([a["title"] for a in answers.values()], start, end)
    for a in answers.values():
        a["views"] = views.get(a["title"], 0)

    # 2c. Page views from people who aren't players count for less
    #     (prompts.py, view_discounts): a medicine's article is read mostly by
    #     patients, so sertraline outread toluene four to one. Articles using
    #     the named template have their views scaled down for the familiarity
    #     score only. The views shown and the notability floor are unchanged.
    for a in answers.values():
        a["view_weight"] = 1.0
    for n, (template, factor) in enumerate(spec.get("view_discounts", {}).items(), 1):
        members = set(source.template_users(f"{spec['id']}_discount_{n}", [template]))
        hit = [a for a in answers.values() if a["title"] in members]
        for a in hit:
            a["view_weight"] = min(a["view_weight"], factor)
        print(f"  {len(hit)} answers use {template}: their views count {factor:g}x")

    # 3. Notability floor (off for most prompts, see prompts.py). Extras stay.
    floor = spec.get("min_views", 0)
    dropped = [a for a in answers.values() if a["views"] < floor and not a["extra"]]
    answers = {qid: a for qid, a in answers.items() if a["views"] >= floor or a["extra"]}
    if dropped:
        print(f"  notability floor ({floor} views) dropped {len(dropped)} answers")

    # 3b. Names and aliases for the answers that survived, if the light
    #     lookup was used (prompts built from Wikipedia lists: see
    #     TITLES_LIGHT_QUERY for why it's done in two passes).
    unnamed = [a for a in answers.values() if a["raw_aliases"] is None]
    if unnamed:
        print(f"  looking up names and aliases for {len(unnamed)} answers...")
        names = lookup_names(source, spec["id"], [a["id"] for a in unnamed])
        dropped_aliases = 0
        for a in unnamed:
            row = names.get(a["id"], {})
            a["label"] = row.get("label", "")
            a["raw_aliases"], n = clean_aliases(spec, row.get("label", ""), row.get("aliases", ""))
            dropped_aliases += n
        if dropped_aliases:
            print(f"  dropped {dropped_aliases} aliases matching drop_alias_patterns")

    # 4. Hand edits, matched by any name an answer goes by (see name_lookup).
    #    Anything that doesn't match is reported, so typos don't fail silently.
    lookup = name_lookup(answers)
    for a in answers.values():
        a["curriculum"] = 0
    for r in read_csv(data_dir / "curriculum.csv"):
        a = lookup.get(normalise(r["name"]))
        if a is None:
            print(f"  ! curriculum.csv: '{r['name']}' isn't in the answer set")
        else:
            a["curriculum"] = int(r["level"])

    manual_aliases: dict[str, list[str]] = {}
    for r in read_csv(data_dir / "aliases.csv"):
        a = lookup.get(normalise(r["name"]))
        if a is None:
            print(f"  ! aliases.csv: '{r['name']}' isn't in the answer set")
        else:
            manual_aliases.setdefault(a["id"], []).append(r["alias"])

    read_flags(data_dir / "flags.csv", lookup)

    # Short descriptions the game shows when a player finds the answer:
    # always written for the gem and Fool's gold, optional for the rest.
    for r in read_csv(data_dir / "blurbs.csv"):
        a = lookup.get(normalise(r["name"]))
        if a is None:
            print(f"  ! blurbs.csv: '{r['name']}' isn't in the answer set")
        else:
            a["blurb"] = r["blurb"].strip()

    # 5. Familiarity score. Counts are log-scaled first: the gap between 100
    #    and 1,000 views matters as much as the gap between 100,000 and
    #    1,000,000, and without logs a few famous answers would flatten
    #    everyone else into the same score. The +1 keeps log(0) out.
    ordered = list(answers.values())
    signals = {
        "views": [math.log10(1 + counted_views(a)) for a in ordered],
        "sitelinks": [math.log10(1 + a["sitelinks"]) for a in ordered],
        "pubmed": None,  # compounds only; not available for minerals
        "curriculum": [a["curriculum"] / 3 * config.CURRICULUM_BOOST_SD for a in ordered],
    }
    scores = familiarity(signals, config.WEIGHTS, prescaled=("curriculum",))
    for a, f in zip(ordered, scores):
        a["familiarity"] = f
    ordered.sort(key=lambda a: a["familiarity"], reverse=True)

    # 6. Predicted share and tiers (scoring.py has the reasoning).
    #    Hand-set tiers from flags.csv come in two strengths:
    #      min:<tier>  "the model overrates this one". It still counts when
    #                  calibrating beta, but not towards anyone else's share
    #                  above (scoring.tiers_with_floors). Gold in minerals.
    #      set:<tier>  "the model doesn't apply to this one": its page views
    #                  come mostly from people who aren't players. Left out of
    #                  calibration too (scoring.calibrate_beta), and its shown
    #                  share is capped at the top answer's, because the
    #                  model's figure is exactly what we don't believe. The
    #                  Haber process in named reactions (DECISIONS.md, 33).
    #    Shares are rescaled so the answers used for calibration sum to 1.
    familiarities = [a["familiarity"] for a in ordered]
    trusted = ["set_tier" not in a for a in ordered]
    # The top answer's share sets how steep the curve is: config.py's
    # TARGET_TOP_SHARE, or the prompt's own target_top_share for short or
    # flat lists where players pile onto a few answers (DECISIONS.md, 46).
    top_share = spec.get("target_top_share", config.TARGET_TOP_SHARE)
    beta = calibrate_beta(familiarities, top_share, trusted)
    raw = predicted_shares(familiarities, beta)
    trusted_total = sum(s for s, t in zip(raw, trusted) if t) or 1.0
    shares = [s / trusted_total if t else min(s / trusted_total, top_share)
              for s, t in zip(raw, trusted)]
    tiers, share_above = tiers_with_floors(shares, [a.get("min_tier") for a in ordered])
    for a, share, above, tier in zip(ordered, shares, share_above, tiers):
        a["share"], a["share_above"], a["tier"] = share, above, tier
        a["model_tier"] = tier   # kept for checks and review after any override
        if "set_tier" in a:
            a["tier"] = a["set_tier"]   # set:<tier> is exact, not just a minimum

    # 7. Hand-picked gem and Fool's gold override the model. These are
    #    editorial choices about what's fun, which no signal can measure.
    #    The answer keeps its rank and share; only its tier and points change.
    #    print_summary checks them against GEM_WINDOW / FOOLS_GOLD_WINDOW.
    #
    #    "Notoriety" helps find Fool's gold: English page views well above what
    #    the number of language Wikipedias would predict. Minerals famous for
    #    being rare (painite, kyawthuite) or for a funny name (cummingtonite)
    #    get read about far more than their scientific importance explains.
    z_views = zscores([math.log10(1 + counted_views(a)) for a in ordered])
    z_links = zscores([math.log10(1 + a["sitelinks"]) for a in ordered])
    for a, zv, zl in zip(ordered, z_views, z_links):
        a["notoriety"] = zv - zl
    for a in ordered:
        if "flag" in a:
            a["tier"] = a["flag"]
        a["points"] = config.POINTS[a["tier"]]

    # 7b. Answers that share a name are shown by their Wikipedia titles.
    renamed = tell_apart(ordered)
    if renamed:
        print(f"  {renamed} answers share a name with a more familiar one: "
              f"they're shown by their Wikipedia titles instead")
    #     And names shown with the case Wikipedia's titles lose: "pH meter",
    #     not "PH meter" (fix_first_letter).
    for a in ordered:
        a["name"] = fix_first_letter(a["name"], a.get("label") or "")

    # 8. Alias index: every accepted spelling -> answer id. The own name goes
    #    first because build_alias_index treats the first name specially.
    #    Short forms are added too: the plain name of a suffixed mineral
    #    ("monazite") and, for prompts with strip_descriptors, the eponym of
    #    a reaction ("Grignard" for Grignard reaction).
    descriptors = spec.get("strip_descriptors", frozenset())
    for a in ordered:
        names = [a["name"], a["title"], *a["raw_aliases"], *manual_aliases.get(a["id"], [])]
        short = [suffix_base(n) or descriptor_base(n, descriptors) for n in names]
        if spec.get("surnames"):
            # Prompts about people: "Curie" for Marie Curie, accepted outright
            # unless another answer has the same surname.
            short.append(surname(a["name"]))
        a["short_forms"] = list(dict.fromkeys(s for s in short if s))   # unique, in order
        a["names"] = names + a["short_forms"]
    index, collisions = build_alias_index(ordered, spec.get("min_alias_length", config.MIN_ALIAS_LENGTH))

    #    What the game accepts without asking (decision 27): the answer's own
    #    name and its British/American twin, plus a short form of the name if
    #    no other answer claims it. "grignard" is accepted outright; "wittig"
    #    (Wittig reaction or Wittig rearrangement?) and "monazite" are shown
    #    to the player to confirm.
    contested = {form for form, _, _ in collisions}
    for a in ordered:
        own = spelled_forms(a["name"])
        for s in a["short_forms"]:
            own |= {f for f in spelled_forms(s)
                    if index.get(f) == a["id"] and f not in contested}
        a["spellings"] = sorted(own)

    # 8b. Answers players will definitely say (must_accept.csv) must be
    #     accepted, outright or after a confirm. Each one missing is a player
    #     told "not on the list" for a right answer, so it's a prompt check.
    must = [r["name"] for r in read_csv(data_dir / "must_accept.csv")]
    missing = [n for n in must if normalise(n) not in index]
    for n in missing:
        print(f"  ! must_accept.csv: '{n}' isn't accepted (add an extra, an alias, or fix exclude.csv)")
    spec["_must_accept"] = (len(must), missing)

    # 9. Write the outputs. The JSON holds only what the game needs, plus a
    #    few numbers (views, sitelinks, curriculum) handy for debugging tiers.
    #    The review CSV and collisions file are for people, so they use names.
    #    "spellings" (see stage 8) are what the game accepts straight away;
    #    anything else that resolves to an answer (another name, a typo) is
    #    shown to the player to confirm first (DECISIONS.md, decision 27).
    result = {
        "prompt": {"id": prompt_id, "text": spec["text"],
                   "label": spec.get("label", prompt_id.replace("_", " ").capitalize()),
                   "subject": spec.get("subject", ""), "audience": spec.get("audience", ""),
                   "colour": spec.get("colour", "")},
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pageview_window": [start.isoformat(), end.isoformat()],
        "beta": round(beta, 4),
        "answers": [
            {
                "id": a["id"], "name": a["name"], "tier": a["tier"], "points": a["points"],
                "share": round(a["share"], 6), "kind": a["kind"],
                "spellings": a["spellings"],
                "views": a["views"], "sitelinks": a["sitelinks"], "curriculum": a["curriculum"],
                **({"blurb": a["blurb"]} if a.get("blurb") else {}),
            }
            for a in ordered
        ],
        "aliases": dict(sorted(index.items())),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{prompt_id}.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    write_review_csv(out_dir / f"{prompt_id}_review.csv", ordered)
    collisions_path = out_dir / f"{prompt_id}_collisions.txt"
    write_collisions(collisions_path, collisions, answers)

    print_summary(spec, ordered, beta, collisions, json_path)
    # The prompt checks go into the game file too, for the readiness check
    # before a prompt is scheduled (publish_site.py).
    result["checks"] = [{"ok": ok, "check": message, "required": required}
                        for ok, message, required in spec["_checks"]]
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    return result


def write_collisions(path: Path, collisions: list, answers: dict[str, dict]) -> None:
    """Spellings claimed by more than one answer, and who got each one."""
    def who(qid: str) -> str:
        return f"{answers[qid]['name']} ({qid})"

    lines = [
        "Spellings that more than one answer could claim. Each one counts for",
        "the answer named first: an answer's own name beats someone else's",
        "alias, then the more familiar answer wins. Most are expected (a plain",
        "name like \"monazite\" shared by monazite-(Ce), -(La) and so on). If one",
        "is wrong, the fix is on Wikidata: remove the alias from the wrong item.",
        "",
    ]
    if not collisions:
        lines.append("(none)")
    for form, winner, losers in sorted(collisions):
        lines.append(f'"{form}" counts as {who(winner)}')
        lines.append(f"    also claimed by: {', '.join(who(q) for q in losers)}")
    path.write_text("\n".join(lines) + "\n")


def write_review_csv(path: Path, ordered: list[dict]) -> None:
    """Every answer, most common first, with the numbers behind its tier."""
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "name", "tier", "model_tier", "points", "share_%", "share_above_%",
                    "views_24m", "view_weight", "sitelinks", "curriculum", "kind", "via",
                    "wikidata", "wikipedia"])
        for rank, a in enumerate(ordered, 1):
            w.writerow([
                rank, a["name"], config.TIER_LABELS[a["tier"]], config.TIER_LABELS[a["model_tier"]], a["points"],
                f"{a['share'] * 100:.3f}", f"{a['share_above'] * 100:.1f}",
                a["views"], f"{a.get('view_weight', 1.0):g}", a["sitelinks"], a["curriculum"],
                a["kind"], a.get("via", ""), a["id"],
                "https://en.wikipedia.org/wiki/" + a["title"].replace(" ", "_"),
            ])


def pyramid_growth(n: int) -> float:
    """How many times bigger each tier below lab grade should be than the one
    above, for a set of n answers: 1 for PYRAMID_SMALL_SET answers or fewer,
    MIN_TIER_GROWTH for PYRAMID_LARGE_SET or more, on a log scale between."""
    lo, hi = math.log10(config.PYRAMID_SMALL_SET), math.log10(config.PYRAMID_LARGE_SET)
    t = (math.log10(max(n, 1)) - lo) / (hi - lo)
    return 1 + (config.MIN_TIER_GROWTH - 1) * min(1.0, max(0.0, t))


def pyramid_check(sizes: list[int], n: int) -> tuple[bool, str]:
    """The tier pyramid: tap water, lab, reagent and analytical counts.

    Lab grade needs at least as many answers as tap water; reagent and
    analytical each need pyramid_growth(n) times the tier above. Tap water
    and lab grade are both a handful of answers by design (the answers that
    make up the first half and the next 30% of predicted share), so their
    ratio says little about the set.
    """
    growth = pyramid_growth(n)
    tap, lab, reagent, analytical = sizes
    ok = lab >= tap and reagent >= growth * lab and analytical >= growth * reagent
    return ok, (f"tier pyramid {tap} / {lab} / {reagent} / {analytical} (each tier at least the one "
                f"above, and {growth:.2f}x+ below lab grade for {n} answers)")


def print_summary(spec, ordered, beta, collisions, json_path) -> None:
    """Tier counts, top answers, gem candidates and the prompt checks."""
    counts: dict[str, int] = {}
    for a in ordered:
        counts[a["tier"]] = counts.get(a["tier"], 0) + 1

    print(f"\n{spec['text']}: {len(ordered)} answers, beta = {beta:.3f}")
    for tier, label in config.TIER_LABELS.items():
        if tier in counts:
            print(f"  {label:<17} {counts[tier]:>6}")

    print("\nMost common answers:")
    for a in ordered[:15]:
        hand = "  (hand-set in flags.csv)" if "min_tier" in a else ""
        print(f"  {a['share'] * 100:5.1f}%  {a['name']:<28} {config.TIER_LABELS[a['tier']]}{hand}")

    # Starting points for choosing by hand. Answers with a minimum tier are
    # left out: their page views overstate them, so they say nothing here.
    unfloored = [a for a in ordered if "min_tier" not in a]
    lo, hi = config.GEM_WINDOW
    gems = [a for a in unfloored if lo <= a["share_above"] < hi]
    print(f"\nGem candidates: most-read answers in the gem window "
          f"({lo:.1%}-{hi:.1%} share above). Pick one that's fun, quirky or in the news:")
    for a in sorted(gems, key=counted_views, reverse=True)[:10]:
        print(f"  {a['name']:<28} {a['views']:>9,} views  {a['share_above']:.1%} above")

    lo, hi = config.FOOLS_GOLD_WINDOW
    fools = [a for a in unfloored if lo <= a["share_above"] < hi]
    print(f"\nFool's gold candidates: most notorious answers in Reagent grade "
          f"(read about far more than their spread across languages suggests):")
    for a in sorted(fools, key=lambda a: a["notoriety"], reverse=True)[:10]:
        print(f"  {a['name']:<28} {a['views']:>9,} views  {a['sitelinks']:>3} languages")

    top5 = sum(a["share"] for a in unfloored[:5])   # shares the model believes
    # What a player has to type is the shortest form accepted outright
    # ("Grignard", not "Grignard reaction"), so that's what's measured.
    median_len = statistics.median(min(len(s) for s in a["spellings"]) for a in ordered)
    # The tiers should form a pyramid, so a rarer tier really is harder to
    # reach: pyramid_check, with a growth factor that rises with the set's size.
    sizes = [counts.get(t, 0) for t in TIER_ORDER]
    pyramid, pyramid_message = pyramid_check(sizes, len(ordered))
    # Each check is (passed, message, required). Required checks stop a
    # prompt being scheduled (publish_site.py's readiness check): too few
    # answers, or a right answer that would be refused. The rest are advice
    # about the shape of the set, shown as NOTE when they fail: elements can
    # never make a neat pyramid with 118 answers, and that's fine
    # (DECISIONS.md, 45).
    checks = [
        (len(ordered) >= config.MIN_SET_SIZE,
         f"size {len(ordered)} (need {config.MIN_SET_SIZE}+)", True),
        (top5 >= config.MIN_TOP5_SHARE,
         f"top 5 (not hand-set) hold {top5 * 100:.0f}% of predicted share (need {config.MIN_TOP5_SHARE * 100:.0f}%+)",
         False),
        (median_len <= config.MAX_MEDIAN_NAME_LENGTH,
         f"median name length {median_len:.0f} characters (max {config.MAX_MEDIAN_NAME_LENGTH})", False),
        (pyramid, pyramid_message, False),
    ]
    total, missing = spec.get("_must_accept", (0, []))
    if total:
        checks.append((not missing,
                       f"{total - len(missing)} of {total} must-accept answers accepted"
                       + (f" (missing: {', '.join(missing)})" if missing else ""), True))
    # The hand-picked gem and Fool's gold must sit where the model puts
    # answers of that kind (config.GEM_WINDOW, FOOLS_GOLD_WINDOW).
    windows = {"single_crystal": ("gem", config.GEM_WINDOW),
               "fools_gold": ("Fool's gold", config.FOOLS_GOLD_WINDOW)}
    for a in ordered:
        if a.get("flag") in windows:
            role, (lo, hi) = windows[a["flag"]]
            checks.append((
                lo <= a["share_above"] < hi,
                f"{role} {a['name']} sits at {a['share_above']:.1%} share above "
                f"(needs {lo:.1%}-{hi:.1%}; the model alone says "
                f"{config.TIER_LABELS[a['model_tier']]})",
                False,
            ))
    spec["_checks"] = checks
    sources = Counter(a.get("via") for a in ordered if a.get("via"))
    if len(sources) > 1:
        print("\nWhere the answers came from (a whole subcategory of junk can go in "
              "skip_categories in prompt.toml):")
        for via, n in sources.most_common(12):
            print(f"  {n:>5}  {via}")
        if len(sources) > 12:
            print(f"  ...and {len(sources) - 12} more (the review CSV's via column has them all)")
    print("\nPrompt checks (NOTE is advice; only FAIL stops a prompt being scheduled):")
    for ok, message, required in checks:
        print(f"  {'PASS' if ok else 'FAIL' if required else 'NOTE'}  {message}")
    if collisions:
        print(f"  NOTE  {len(collisions)} spellings claimed by more than one answer "
              f"(see results/{json_path.stem}_collisions.txt)")
    print(f"\nWrote {json_path} and {json_path.stem}_review.csv")


# ---- Probe: a quick look at a draft before building it ------------------------

PROBE_NAME_LIMIT = 3000   # above this, a probe skips the names (too many lookups)
# Titles worth a second look in a probe: organisations, works, places, people.
SUSPICIOUS = re.compile(
    r"\((?:company|band|album|film|novel|magazine|TV series|song|ship|horse)\)"
    r"|\b(?:Inc\.?|Ltd\.?|plc|GmbH|Corporation|Company|Laboratories|University|Society)\b"
    r"|\b1[5-9]\d\d\b|\b20\d\d\b|:", re.IGNORECASE)


def probe(prompt_id: str, source, out_dir: Path) -> dict:
    """How big a prompt's answer set is and what's in it, without page views.

    Lists the candidates (and, for a few thousand or fewer, their names),
    then prints the size, the best-known candidates, where they came from,
    titles that look like they don't belong, and which must-accept answers
    are missing. The Wikidata lookups are cached, so the full build that
    follows reuses them. Writes results/<id>_probe.csv for a closer look.
    """
    spec = dict(PROMPTS[prompt_id], id=prompt_id)
    data_dir = config.DATA_DIR / spec["data_dir"]
    print(f"Probing '{spec['text']}' (a quick look: no page views)")
    answers = collect_answers(spec, source, data_dir)
    links_floor = spec.get("min_sitelinks", 0)
    if links_floor:
        answers = {q: a for q, a in answers.items() if a["sitelinks"] >= links_floor or a["extra"]}
    if not answers:
        print("  No candidates. If the source is a category, check its exact name on Wikipedia "
              "(Category:... with the same capitals); if it's a query, try it on query.wikidata.org.")
        return {"id": prompt_id, "size": 0}
    unnamed = [a for a in answers.values() if a["raw_aliases"] is None]
    if unnamed and len(unnamed) <= PROBE_NAME_LIMIT:
        names = lookup_names(source, prompt_id, [a["id"] for a in unnamed])
        for a in unnamed:
            row = names.get(a["id"], {})
            a["raw_aliases"], _ = clean_aliases(spec, row.get("label", ""), row.get("aliases", ""))
    for a in answers.values():
        a["raw_aliases"] = a["raw_aliases"] or []

    ranked = sorted(answers.values(), key=lambda a: a["sitelinks"], reverse=True)
    size = len(ranked)
    verdict = ("too small: the tiers need " + str(config.MIN_SET_SIZE) + "+" if size < config.MIN_SET_SIZE
               else "big: consider min_sitelinks to trim it before page views" if size > 5000 and not links_floor
               else "a workable size")
    links = [a["sitelinks"] for a in ranked]
    print(f"\n  {size} candidates ({verdict}). Languages per article: "
          f"median {statistics.median(links):.0f}, top {links[0]}")
    print("  Best known: " + ", ".join(a["name"] for a in ranked[:25]))

    sources = Counter(a.get("via") for a in ranked if a.get("via"))
    if len(sources) > 1:
        print("  Came from: " + ", ".join(f"{v} ({n})" for v, n in sources.most_common(15)))
    odd = [a["name"] for a in ranked if SUSPICIOUS.search(a["title"])]
    if odd:
        print(f"  Worth a look ({len(odd)}): " + ", ".join(odd[:30]) + (" ..." if len(odd) > 30 else ""))

    must = [r["name"] for r in read_csv(data_dir / "must_accept.csv")]
    if must:
        known = set(name_lookup(answers))
        if spec.get("surnames"):
            known |= {normalise(s) for a in answers.values() if (s := surname(a["name"]))}
        missing = [n for n in must if normalise(n) not in known]
        print(f"  Must-accept: {len(must) - len(missing)} of {len(must)} found"
              + (f" (missing: {', '.join(missing)})" if missing else ""))

    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / f"{prompt_id}_probe.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "sitelinks", "via", "worth_a_look", "wikidata", "wikipedia"])
        for a in ranked:
            w.writerow([a["name"], a["sitelinks"], a.get("via", ""), "yes" if SUSPICIOUS.search(a["title"]) else "",
                        a["id"], "https://en.wikipedia.org/wiki/" + a["title"].replace(" ", "_")])
    print(f"  Wrote {out_dir.name}/{prompt_id}_probe.csv")
    return {"id": prompt_id, "size": size}


# Names that build several prompts in turn.
GROUPS = {
    "ready": lambda: ids_with_status("ready"),
    "drafts": lambda: ids_with_status("draft"),
    "all": lambda: sorted(PROMPTS),
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prompt", choices=[*sorted(PROMPTS), *GROUPS, "probe"],
                        help="which prompt to build, or a group one after another: "
                             "ready (the bank), drafts (not reviewed yet) or all. "
                             "probe <prompt or group> takes a quick look instead of building")
    parser.add_argument("target", nargs="?", choices=[*sorted(PROMPTS), *GROUPS],
                        help="with probe: which prompt or group to look at")
    parser.add_argument("--refresh", action="store_true", help="ignore cached Wikidata results")
    parser.add_argument("--fixtures", type=Path, help="read saved test data from this folder instead of the internet")
    parser.add_argument("--out", type=Path, default=config.OUT_DIR, help="output folder (default: results/)")
    args = parser.parse_args(argv)

    # Same build either way; only where the data comes from changes.
    source = FixtureSource(args.fixtures) if args.fixtures else LiveSource(refresh=args.refresh)
    probing = args.prompt == "probe"
    if probing and not args.target:
        parser.error("probe needs a prompt or group, e.g. probe drafts")
    chosen = args.target if probing else args.prompt
    prompt_ids = GROUPS[chosen]() if chosen in GROUPS else [chosen]
    if not prompt_ids:
        print(f"No prompts have status '{chosen.rstrip('s')}' yet.")
        return
    try:
        for i, prompt_id in enumerate(prompt_ids):
            if i:
                print("\n" + "-" * 72 + "\n")
            (probe if probing else build)(prompt_id, source, args.out)
    except IncompleteDownload as stopped:
        # An expected situation (usually Wikimedia's rate limit), not a bug, so
        # explain it plainly instead of printing a traceback. Exit code 2 tells
        # IntelliJ and scripts that the build didn't finish.
        print(f"\nStopped before building: {stopped}")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
