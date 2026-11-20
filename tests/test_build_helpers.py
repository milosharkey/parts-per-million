"""Small pieces of the build: shown names, the property filter, view weights."""
import json

from build_prompt import fix_first_letter, keep_with_properties, read_flags
from sources import FixtureSource


def test_first_letter_follows_wikidata_where_the_case_matters():
    assert fix_first_letter("PH meter", "pH meter") == "pH meter"
    assert fix_first_letter("MTOR", "mTOR") == "mTOR"
    assert fix_first_letter("P-Xylene", "p-xylene") == "p-Xylene"
    assert fix_first_letter("N-Butyllithium", "n-butyllithium") == "n-Butyllithium"


def test_greek_prefixes_are_lower_case():
    assert fix_first_letter("Α-Amylase", "") == "α-Amylase"            # Greek capital alpha
    assert fix_first_letter("Δ13C", "") == "δ13C"
    assert fix_first_letter("Α,β-Unsaturated carbonyl compound", "") == "α,β-Unsaturated carbonyl compound"


def test_ordinary_names_keep_their_capital():
    assert fix_first_letter("Microscope", "microscope") == "Microscope"      # an ordinary word
    assert fix_first_letter("N-Bromosuccinimide", "N-bromosuccinimide") == "N-Bromosuccinimide"
    assert fix_first_letter("L-DOPA", "levodopa") == "L-DOPA"               # label is another name
    assert fix_first_letter("Iron(III) chloride", "iron(III) chloride") == "Iron(III) chloride"
    assert fix_first_letter("Gold", "") == "Gold"


def _answers(*specs):
    return {q: {"id": q, "title": t, "sitelinks": n, "extra": extra} for q, t, n, extra in specs}


def test_property_filter_keeps_substances_and_extras(tmp_path, capsys):
    (tmp_path / "fatty_acids_has_properties.json").write_text(json.dumps([
        {"item": "http://www.wikidata.org/entity/Q1"}]))
    answers = _answers(("Q1", "Stearic acid", 40, False),
                       ("Q2", "Beta oxidation", 30, False),
                       ("Q3", "Omega−3 fatty acid", 50, True))   # an extra: always kept
    spec = {"id": "fatty_acids", "require_properties": ["P274", "P662"]}
    kept = keep_with_properties(spec, FixtureSource(tmp_path), answers)
    assert sorted(kept) == ["Q1", "Q3"]
    assert "1 have none of P274, P662 on Wikidata" in capsys.readouterr().out


def test_property_filter_is_off_unless_asked(tmp_path):
    answers = _answers(("Q2", "Beta oxidation", 30, False))
    assert keep_with_properties({"id": "x"}, FixtureSource(tmp_path), answers) == answers


def test_views_flag_scales_one_answer(tmp_path, capsys):
    (tmp_path / "flags.csv").write_text("name,flag\nMargaret Thatcher,views:0.02\nRobert Boyle,views:2\n")
    thatcher = {"id": "Q7416", "view_weight": 1.0}
    boyle = {"id": "Q43393", "view_weight": 1.0}
    read_flags(tmp_path / "flags.csv", {"margaret thatcher": thatcher, "robert boyle": boyle})
    assert thatcher["view_weight"] == 0.02
    assert boyle["view_weight"] == 1.0                     # 2 isn't a discount: refused
    assert "unknown flag 'views:2'" in capsys.readouterr().out


def test_an_extra_the_source_also_lists_stays_an_extra(tmp_path):
    from build_prompt import collect_answers
    (tmp_path / "antibiotics_category.json").write_text(json.dumps(["Penicillin", "Amoxicillin"]))
    (tmp_path / "antibiotics.json").write_text(json.dumps([
        {"item": "http://www.wikidata.org/entity/Q12190", "title": "Penicillin", "sitelinks": "90"},
        {"item": "http://www.wikidata.org/entity/Q199199", "title": "Amoxicillin", "sitelinks": "60"}]))
    (tmp_path / "antibiotics_extras.json").write_text(json.dumps([
        {"item": "http://www.wikidata.org/entity/Q12190", "title": "Penicillin", "sitelinks": "90",
         "label": "penicillin", "aliases": ""}]))
    data = tmp_path / "data"
    data.mkdir()
    (data / "extras.csv").write_text("title,kind\nPenicillin,antibiotic\n")
    spec = {"id": "antibiotics", "category": "Category:Antibiotics"}
    answers = collect_answers(spec, FixtureSource(tmp_path), data)
    assert answers["Q12190"]["extra"] is True          # listed by both: still hand-picked
    assert answers["Q199199"]["extra"] is False


def test_pyramid_growth_rises_with_set_size():
    from build_prompt import pyramid_growth
    assert pyramid_growth(53) == pyramid_growth(100) == 1.0
    assert abs(pyramid_growth(200) - 1.30) < 0.01
    assert pyramid_growth(1000) == pyramid_growth(3299) == 2.0


def test_pyramid_needs_each_tier_at_least_the_one_above():
    from build_prompt import pyramid_check
    assert pyramid_check([8, 17, 41, 50], 118)[0]           # the elements: small, fine
    assert pyramid_check([7, 7, 36, 162], 213)[0]           # tap and lab only need to be equal
    assert not pyramid_check([10, 100, 78, 11], 200)[0]     # too few rare answers
    assert not pyramid_check([7, 23, 129, 200], 1226)[0]    # big sets need 2x
    assert "1.30x+ below lab grade for 200 answers" in pyramid_check([10, 100, 78, 11], 200)[1]


def test_a_prompt_can_set_its_own_top_share(tmp_path, monkeypatch):
    from pathlib import Path
    from build_prompt import build
    import build_prompt
    fixtures = Path(__file__).parent / "fixtures"
    spec = dict(build_prompt.PROMPTS["minerals"], target_top_share=0.25)
    monkeypatch.setitem(build_prompt.PROMPTS, "minerals", spec)
    result = build("minerals", FixtureSource(fixtures), tmp_path)
    trusted = [a for a in result["answers"] if a["tier"] not in ("single_crystal", "fools_gold")]
    assert abs(max(a["share"] for a in trusted) - 0.25) < 0.01
