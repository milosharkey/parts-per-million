"""End-to-end build using the fake data in tests/fixtures (no internet)."""
import json
from pathlib import Path

import pytest

from aliases import normalise
from build_prompt import build, display_name
from sources import FixtureSource

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    out = tmp_path_factory.mktemp("results")
    build("minerals", FixtureSource(FIXTURES), out)
    data = json.loads((out / "minerals.json").read_text())
    by_name = {a["name"]: a for a in data["answers"]}
    return data, by_name, out


def test_display_names_come_from_wikipedia_titles():
    assert display_name("Mercury (element)") == "Mercury"
    assert display_name("Garnet") == "Garnet"
    assert display_name("Monazite-(Ce)") == "Monazite-(Ce)"   # suffix is part of the name


def test_writes_game_file_review_csv_and_readable_collisions(result):
    data, _, out = result
    assert data["prompt"]["text"] == "Name a mineral"
    assert (out / "minerals_review.csv").exists()
    collisions = (out / "minerals_collisions.txt").read_text()
    assert "Monazite-(Ce) (Q3860580)" in collisions   # names, not just Q-numbers
    assert len(data["answers"]) >= 25


def test_own_spellings_listed_for_the_game(result):
    # The game accepts these without asking; other names get a confirm step.
    _, by_name, _ = result
    assert by_name["Hematite"]["spellings"] == ["haematite", "hematite"]
    assert by_name["Monazite-(Ce)"]["spellings"] == ["monazite ce"]   # not plain "monazite"
    for answer in by_name.values():
        assert normalise(answer["name"]) in answer["spellings"]


def test_answers_sorted_most_common_first(result):
    data, _, _ = result
    shares = [a["share"] for a in data["answers"]]
    assert shares == sorted(shares, reverse=True)
    assert data["answers"][0]["tier"] == "tap_water"


def test_flags_override_the_model(result):
    _, by_name, _ = result
    assert by_name["Jadarite"]["tier"] == "single_crystal"
    assert by_name["Jadarite"]["points"] == 100
    assert by_name["Cummingtonite"]["tier"] == "fools_gold"
    assert by_name["Cummingtonite"]["points"] == 15
    assert by_name["Painite"]["tier"] not in ("fools_gold", "single_crystal")


def test_minimum_tiers_hold_even_with_huge_page_views(result):
    _, by_name, _ = result
    # Gold has a top-5 predicted share, but flags.csv says min:reagent_grade.
    for name in ["Gold", "Silver", "Sulfur", "Mercury"]:
        assert by_name[name]["tier"] == "reagent_grade", name
    # A floor is a minimum: native copper's own position already makes it rarer.
    assert by_name["Native copper"]["tier"] == "analytical_grade"


def test_hand_edits_match_any_name(result):
    _, by_name, _ = result
    assert by_name["Garnet"]["curriculum"] == 2    # Wikidata calls it "garnet group"
    assert by_name["Biotite"]["curriculum"] == 1


def test_aliases_resolve_to_the_right_answer(result):
    data, by_name, _ = result
    ids = {name: a["id"] for name, a in by_name.items()}
    aliases = data["aliases"]
    assert aliases[normalise("Haematite")] == ids["Hematite"]       # British spelling
    assert aliases[normalise("rock salt")] == ids["Halite"]         # aliases.csv
    assert aliases[normalise("Sulphur")] == ids["Sulfur"]           # spelling + Wikidata alias
    assert aliases[normalise("Fool's gold")] == ids["Pyrite"]       # the mineral, not the tier
    assert aliases[normalise("garnet group")] == ids["Garnet"]      # Wikidata label
    assert aliases[normalise("copper")] == ids["Native copper"]     # Wikidata alias
    assert aliases[normalise("Monazite-(La)")] == ids["Monazite-(La)"]
    assert aliases[normalise("monazite")] == ids["Monazite-(Ce)"]   # plain name -> more familiar


def test_extras_are_included(result):
    _, by_name, _ = result
    assert by_name["Feldspar"]["kind"] == "group"
    assert by_name["Ruby"]["kind"] == "variety"
    assert by_name["Gold"]["kind"] == "native element"



# ---- The other prompts ------------------------------------------------------

def built(prompt_id, tmp_path_factory):
    out = tmp_path_factory.mktemp(prompt_id)
    build(prompt_id, FixtureSource(FIXTURES), out)
    data = json.loads((out / f"{prompt_id}.json").read_text())
    return data, {a["name"]: a for a in data["answers"]}


@pytest.fixture(scope="module")
def elements(tmp_path_factory):
    return built("elements", tmp_path_factory)


@pytest.fixture(scope="module")
def reactions(tmp_path_factory):
    return built("named_reactions", tmp_path_factory)


def test_element_symbols_count_despite_being_short(elements):
    data, by_name = elements
    assert data["aliases"]["fe"] == by_name["Iron"]["id"]
    assert data["aliases"]["o"] == by_name["Oxygen"]["id"]       # one letter
    assert data["aliases"]["wolfram"] == by_name["Tungsten"]["id"]
    assert by_name["Iron"]["spellings"] == ["iron"]              # "fe" asks to confirm


def test_element_placeholder_names_dont_count(elements):
    data, by_name = elements
    for placeholder in ("ununoctium", "uuo", "element 118", "eka radon"):
        assert placeholder not in data["aliases"], placeholder
    assert data["aliases"]["og"] == by_name["Oganesson"]["id"]    # the real symbol still counts


def test_element_names_drop_wikipedia_disambiguation(elements):
    _, by_name = elements
    assert "Mercury" in by_name                                   # from "Mercury (element)"
    assert by_name["Aluminium"]["spellings"] == ["aluminium", "aluminum"]


def test_reaction_short_forms(reactions):
    data, by_name = reactions
    # Uncontested short forms are accepted outright...
    assert "grignard" in by_name["Grignard reaction"]["spellings"]
    assert "diels alder" in by_name["Diels–Alder reaction"]["spellings"]
    assert "haber" in by_name["Haber process"]["spellings"]
    # ...a shared one goes to the more familiar answer, but asks to confirm.
    assert data["aliases"]["wittig"] == by_name["Wittig reaction"]["id"]
    assert "wittig" not in by_name["Wittig reaction"]["spellings"]


def test_titles_are_looked_up_in_batches():
    from build_prompt import lookup_titles

    class Recorder:
        def __init__(self): self.calls = []
        def is_cached(self, name, query): return False
        def sparql(self, name, query):
            self.calls.append((name, query.count('"@en')))
            return []
    rec = Recorder()
    lookup_titles(rec, "big", [f"Reaction {i}" for i in range(450)])
    assert rec.calls == [("big_1", 200), ("big_2", 200), ("big_3", 50)]


def test_reaction_extras_and_exclusions(reactions):
    data, by_name = reactions
    assert by_name["Haber process"]["kind"] == "process"           # from extras.csv
    assert "Devarda's alloy" not in by_name                        # exclude.csv: an alloy
    assert sum(a["name"] == "Grignard reaction" for a in data["answers"]) == 1   # in both queries


def test_set_tiers_are_exact_and_dont_set_the_scale(reactions):
    # Haber is set:tap_water in flags.csv: its page views are mostly from
    # non-chemists, so it gets its tier by hand and a capped share.
    import config
    _, by_name = reactions
    haber = by_name["Haber process"]
    assert haber["tier"] == "tap_water"
    assert haber["share"] <= config.TARGET_TOP_SHARE


# ---- Lab equipment, aromatics, enzymes, pigments -------------------------------

@pytest.fixture(scope="module")
def aromatics(tmp_path_factory):
    return built("aromatics", tmp_path_factory)


def test_aromatics_keep_only_benzene_rings(aromatics):
    data, by_name = aromatics
    for kept in ("Benzene", "Aspirin", "Serotonin", "Morphine", "Estradiol", "Naphthalene"):
        assert kept in by_name, kept
    for not_benzene in ("Caffeine", "Pyridine", "Cyclohexane", "Glucose", "Sodium chloride"):
        assert not_benzene not in by_name, not_benzene
    assert data["aliases"]["prozac"] == by_name["Fluoxetine"]["id"]          # brand name
    assert data["aliases"]["oestradiol"] == by_name["Estradiol"]["id"]       # British spelling


def test_medicines_page_views_count_for_less(aromatics):
    # Diazepam (900,000 views, 70 languages) outranks DDT (600,000, 70) on raw
    # views, but its drug infobox puts its views at a quarter (view_discounts).
    data, by_name = aromatics
    order = [a["name"] for a in data["answers"]]
    assert order.index("DDT") < order.index("Diazepam")


def test_review_csv_shows_the_discount(tmp_path_factory):
    import csv
    out = tmp_path_factory.mktemp("aromatics_review")
    build("aromatics", FixtureSource(FIXTURES), out)
    rows = {r["name"]: r for r in csv.DictReader((out / "aromatics_review.csv").open())}
    assert rows["Diazepam"]["view_weight"] == "0.25" and rows["DDT"]["view_weight"] == "1"
    assert rows["Diazepam"]["views_24m"] == "900000"                     # shown as read


def test_answers_sharing_a_name_are_told_apart():
    from build_prompt import tell_apart
    ordered = [{"name": "Condenser", "title": "Condenser (heat transfer)"},
               {"name": "Condenser", "title": "Condenser (laboratory)"},
               {"name": "Beaker", "title": "Beaker (laboratory equipment)"}]
    assert tell_apart(ordered) == 1
    assert [a["name"] for a in ordered] == ["Condenser", "Condenser (laboratory)", "Beaker"]


def test_aromatics_notability_floors(aromatics):
    _, by_name = aromatics
    assert "Obscurylbenzene" not in by_name        # articles in only 2 languages (min_sitelinks)
    assert "Littlereadophenone" not in by_name     # 2,000 views (min_views)


def test_enzymes_lose_ec_numbers_and_gain_families(tmp_path_factory):
    data, by_name = built("enzymes", tmp_path_factory)
    assert not [k for k in data["aliases"] if k.startswith("ec ") or k.replace(" ", "").isdigit()]
    assert "DNA polymerase" in by_name and "Protease" in by_name               # extras.csv
    assert data["aliases"]["amylase"] == by_name["Alpha-amylase"]["id"]


def test_lab_equipment_british_names_and_extras(tmp_path_factory):
    data, by_name = built("lab_equipment", tmp_path_factory)
    assert data["aliases"]["conical flask"] == by_name["Erlenmeyer flask"]["id"]
    assert data["aliases"]["bunsen"] == by_name["Bunsen burner"]["id"]
    assert sum(a["name"] == "Beaker" for a in data["answers"]) == 1           # in both lists


def test_pigments_from_two_categories(tmp_path_factory):
    data, by_name = built("pigments", tmp_path_factory)
    assert data["aliases"]["indigo"] == by_name["Indigo dye"]["id"]
    assert "Chlorophyll" in by_name and "Tyrian purple" in by_name



# ---- Big lookups ------------------------------------------------------------------

def test_slow_batches_split_in_half_and_are_remembered_whole():
    from build_prompt import lookup_batches
    from sources import QueryTooSlow

    class Picky:
        """Gives up on any batch of more than 50 values, like a busy Wikidata."""
        def __init__(self):
            self.asked, self.remembered = [], []
        def is_cached(self, name, query): return False
        def sparql(self, name, query):
            n = query.count("wd:")
            self.asked.append((name, n))
            if n > 50:
                raise QueryTooSlow("too slow")
            return [{"n": str(n)}] * n
        def remember(self, name, query, rows):
            self.remembered.append((name, len(rows)))

    src = Picky()
    rows = lookup_batches(src, "big", [f"wd:Q{i}" for i in range(250)], "SELECT {values}")
    assert len(rows) == 250                                     # nothing lost
    assert ("big_1a", 100) in src.asked and ("big_1aa", 50) in src.asked   # halved, then halved again
    assert ("big_1", 200) in src.remembered                     # cached whole for next time


def test_names_are_only_fetched_for_answers_that_survive(tmp_path):
    """The two-pass lookup: light rows for everything, names for survivors."""
    import re

    class TwoPass(FixtureSource):
        def __init__(self, folder):
            super().__init__(folder)
            self.full = {r["item"].rsplit("/", 1)[-1]: r
                         for r in json.loads((Path(folder) / "aromatics.json").read_text())}
            self.named = []
        def sparql(self, name, query):
            if name.startswith("aromatics_names"):
                ids = re.findall(r"wd:(Q\d+)", query)
                self.named += ids
                return [{"item": self.full[q]["item"], "label": self.full[q]["label"],
                         "aliases": self.full[q]["aliases"]} for q in ids]
            if name.startswith("aromatics") and "extras" not in name:   # light rows only
                return [{k: v for k, v in r.items() if k not in ("label", "aliases")}
                        for r in self.full.values()]
            return super().sparql(name, query)

    src = TwoPass(FIXTURES)
    build("aromatics", src, tmp_path)
    data = json.loads((tmp_path / "aromatics.json").read_text())
    by_name = {a["name"]: a for a in data["answers"]}
    assert data["aliases"]["prozac"] == by_name["Fluoxetine"]["id"]      # names arrived
    assert sorted(src.named) == sorted(a["id"] for a in data["answers"])  # and only for survivors


def test_must_accept_answers_are_checked(tmp_path_factory, capsys):
    built("elements", tmp_path_factory)
    out = capsys.readouterr().out
    assert "must-accept answers accepted" in out
    # The offline test data has only a few elements, so some are reported.
    assert "! must_accept.csv: 'neon' isn't accepted" in out


# ---- Prompt bank (DECISIONS.md, 40) ---------------------------------------------

def test_prompt_metadata_and_blurbs_reach_the_game(elements):
    data, by_name = elements
    assert data["prompt"]["colour"].startswith("#") and data["prompt"]["subject"] == "chemistry"
    assert "Greek for 'artificial'" in by_name["Technetium"]["blurb"]
    assert "blurb" not in by_name["Hydrogen"]


def test_every_prompt_folder_loads_with_one_source():
    from prompts import PROMPTS, STATUSES
    assert len(PROMPTS) >= 7
    for pid, spec in PROMPTS.items():
        assert spec["status"] in STATUSES, pid
        assert sum(k in spec for k in ("query", "category", "templates")) == 1, pid


def test_a_prompt_with_two_sources_is_refused(tmp_path):
    from prompts import load_prompt
    (tmp_path / "prompt.toml").write_text('text = "x"\nquery = "a.rq"\ncategory = "Category:B"\n')
    with pytest.raises(ValueError, match="exactly one"):
        load_prompt(tmp_path)


# ---- Prompt designer (DECISIONS.md, 43) -----------------------------------------

def test_list_pages_are_never_answers():
    import re
    from build_prompt import NOT_ANSWERS
    hits = lambda t: any(re.search(p, t) for p in NOT_ANSWERS)
    for title in ("List of inorganic pigments", "Lists of minerals", "Outline of chemistry",
                  "History of synthetic colourants", "Mercury (disambiguation)"):
        assert hits(title), title
    for title in ("Listeria", "Historyite", "Outlier analysis", "Prussian blue"):
        assert not hits(title), title


def test_surnames_for_people_prompts():
    from build_prompt import surname
    assert surname("Marie Curie") == "Curie"
    assert surname("Jacobus Henricus van 't Hoff") == "van 't Hoff"
    assert surname("Otto Hahn (chemist)") == "Hahn"
    assert surname("Martin Luther King Jr.") == "King"
    assert surname("Plato") is None


def test_probe_reports_size_and_must_accept(capsys):
    from build_prompt import probe
    import tempfile
    with tempfile.TemporaryDirectory() as out:
        result = probe("pigments", FixtureSource(FIXTURES), Path(out))
        assert (Path(out) / "pigments_probe.csv").exists()
    text = capsys.readouterr().out
    assert result["size"] > 0 and "candidates" in text and "Must-accept:" in text
