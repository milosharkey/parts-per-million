"""normalise() and alias rules. These cases are also the specification the
JavaScript port in the game must pass, so add a case whenever a rule changes."""
from aliases import build_alias_index, descriptor_base, normalise, spelled_forms, spelling_variants, suffix_base


def test_normalise_basics():
    assert normalise("Fool's Gold") == "fools gold"
    assert normalise("  Diels–Alder  reaction ") == "diels alder reaction"
    assert normalise("Gold (mineral)") == "gold"
    assert normalise("Monazite-(Ce)") == "monazite ce"   # suffix kept, not stripped
    assert normalise("The Quartz") == "quartz"
    assert normalise("Mössbauer spectroscopy") == "mossbauer spectroscopy"


def test_normalise_chemistry_text():
    assert normalise("α-Pinene") == "alpha pinene"
    assert normalise("H₂O") == "h2o"
    assert normalise("CH3COOH") == "ch3cooh"


def test_spelling_variants_both_ways():
    assert "sulfur" in spelling_variants("sulphur")
    assert "sulphur" in spelling_variants("sulfur")
    assert "hematite" in spelling_variants("haematite")
    assert "haematite" in spelling_variants("hematite")
    assert "aluminum oxide" in spelling_variants("aluminium oxide")


def test_haem_only_changes_word_starts():
    assert spelling_variants("chemistry") == {"chemistry"}


def test_british_endings_and_words():
    assert "recrystallisation" in spelling_variants("recrystallization")
    assert "nazarov cyclization" in spelling_variants("nazarov cyclisation")
    assert "spectrum analyser" in spelling_variants("spectrum analyzer")
    assert "homogeniser" in spelling_variants("homogenizer")
    assert "carbon fibre" in spelling_variants("carbon fiber")
    assert "grey iron" in spelling_variants("gray iron")
    assert spelling_variants("hydrolysis") == {"hydrolysis"}   # "lysi" isn't "lyse"


def test_joined_brackets_also_accept_spaces():
    # Typed with brackets, normalise() closes the gap; typed with spaces, it
    # doesn't. Both are generated for the answer.
    assert spelled_forms("Benzo(a)pyrene") == {"benzoapyrene", "benzo a pyrene"}
    assert "iron iii chloride" in spelled_forms("Iron(III) chloride")
    assert "benzo a pyrene" in spelled_forms("benzo[a]pyrene")
    # A disambiguating bracket after a space is still just dropped.
    assert spelled_forms("Mercury (element)") == {"mercury"}
    assert spelled_forms("Monazite-(Ce)") == {"monazite ce"}


def test_alias_index_accepts_spaced_brackets():
    answers = [{"id": "B", "names": ["Benzo(a)pyrene"], "familiarity": 1.0}]
    index, _ = build_alias_index(answers, min_length=3)
    assert index["benzo a pyrene"] == index["benzoapyrene"] == "B"


def test_alias_index_prefers_own_name_then_familiarity():
    answers = [
        {"id": "A", "names": ["pyrite", "fool's gold"], "familiarity": 1.0},
        {"id": "B", "names": ["fools gold"], "familiarity": 0.5},  # own name wins
        {"id": "C", "names": ["galena", "lead glance"], "familiarity": 0.2},
        {"id": "D", "names": ["cerussite", "lead glance"], "familiarity": 0.1},
    ]
    index, collisions = build_alias_index(answers, min_length=3)
    assert index["fools gold"] == "B"
    assert index["lead glance"] == "C"
    assert index["pyrite"] == "A"
    assert sorted(collisions) == [("fools gold", "B", ["A"]), ("lead glance", "C", ["D"])]


def test_suffix_base():
    assert suffix_base("Monazite-(Ce)") == "Monazite"
    assert suffix_base("allanite-(Ce,La)") == "allanite"
    assert suffix_base("Quartz") is None
    assert suffix_base("Diels-Alder") is None


def test_short_aliases_dropped_but_short_names_kept():
    answers = [{"id": "X", "names": ["tin", "sn"], "familiarity": 0.0}]
    index, _ = build_alias_index(answers, min_length=3)
    assert "tin" in index
    assert "sn" not in index


def test_descriptor_base():
    words = frozenset({"reaction", "process", "synthesis"})
    assert descriptor_base("Grignard reaction", words) == "Grignard"
    assert descriptor_base("Diels–Alder reaction", words) == "Diels–Alder"
    # Only the last word: a bare surname is often shared by several reactions.
    assert descriptor_base("Fischer indole synthesis", words) == "Fischer indole"
    assert descriptor_base("Maillard", words) is None           # nothing to strip
    assert descriptor_base("Reaction", words) is None           # nothing would be left
    assert descriptor_base("Haber process", frozenset()) is None  # prompt doesn't strip
