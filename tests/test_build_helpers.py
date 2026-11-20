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
