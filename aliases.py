"""Turning names into the forms players actually type.

The core idea: instead of fuzzy-comparing what the player typed against
thousands of names at play time, we reduce both sides to a "normalised"
form once, here, and the game just looks the result up in a dictionary.
That makes checking an answer instant, and the matching rules live in one
place you can read and test.

normalise() is that single rule. The game's answer checker MUST use exactly
the same rule (it'll be ported to JavaScript in step 2), or answers that
look right won't match. tests/test_aliases.py pins its behaviour down, and
the JavaScript port should pass the same cases.

Typo tolerance ("quatz" -> quartz) is deliberately NOT done here. It belongs
in the game, where it can be limited to the current prompt's answers.
"""
from __future__ import annotations

import re
import unicodedata

# Greek letters are spelled out because players can't easily type them:
# "α-pinene" and "alpha-pinene" must match.
GREEK = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon",
    "ζ": "zeta", "η": "eta", "θ": "theta", "κ": "kappa", "λ": "lambda",
    "μ": "mu", "ν": "nu", "ξ": "xi", "π": "pi", "ρ": "rho", "σ": "sigma",
    "τ": "tau", "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
}
# Sub/superscript digits become plain digits, so H₂O and H2O match.
SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹", "01234567890123456789")

# British / American spelling pairs, applied in both directions. Wikidata
# mostly uses American spellings ("hematite", "sulfur") and players here
# will often type British ones.
#
# Patterns starting with \b only match at the start of a word. Without that,
# "hem" -> "haem" would also turn "chemistry" into "chaemistry". Short
# fragments like these are risky, so add new pairs carefully and add a test.
SPELLING_PAIRS = [
    (r"sulph", "sulf"),
    (r"aluminium", "aluminum"),
    (r"caesium", "cesium"),
    (r"\bhaem", "hem"),
    (r"\boestr", "estr"),
    (r"colour", "color"),
    # Endings and words British players type differently (added 2026-11-20,
    # DECISIONS.md, 44): "recrystallisation", "spectrum analyser",
    # "homogeniser", "carbon fibre", "grey cast iron".
    (r"isation", "ization"),
    (r"iser", "izer"),
    (r"lyse", "lyze"),
    (r"fibre", "fiber"),
    (r"\bgrey", "gray"),
]


def normalise(text: str) -> str:
    """Lowercase, plain-letter version of a name, used for all matching.

    "Fool's Gold" -> "fools gold", "α-Pinene" -> "alpha pinene",
    "H₂O" -> "h2o", "Diels–Alder" -> "diels alder", "Gold (mineral)" -> "gold".

    The order of the steps matters: Greek and subscripts are converted before
    accents are stripped, and brackets are removed before punctuation (which
    would otherwise leave their contents behind).
    """
    t = text.translate(SUBSCRIPTS)
    for letter, name in GREEK.items():
        t = t.replace(letter, name).replace(letter.upper(), name)
    # NFKD splits accented letters into letter + accent mark ("ö" -> "o" + "¨"),
    # so dropping the marks leaves plain letters: "Mössbauer" -> "Mossbauer".
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower().replace("ß", "ss")
    # Wikipedia disambiguates titles with a bracketed word after a space
    # ("Gold (mineral)"); players never type those, so they're removed.
    # Brackets joined on with a hyphen are kept, because there they're part of
    # the name: mineralogy's element suffixes ("monazite-(Ce)" and
    # "monazite-(La)" are different minerals) become "monazite ce" and so on.
    t = re.sub(r"\s\([^)]*\)", " ", t)
    # Hyphens, the various dashes, underscores and slashes all become spaces,
    # so "Diels-Alder", "Diels–Alder" and "Diels Alder" are the same.
    t = re.sub(r"[‐-―_/-]", " ", t)
    # Anything else that isn't a letter, digit or space goes: apostrophes,
    # commas, full stops. "fool's" and "fools" become the same.
    t = re.sub(r"[^a-z0-9 ]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if t.startswith("the "):
        t = t[4:]
    return t


# A mineral name with an element suffix: "monazite-(Ce)", "allanite-(Ce,La)".
LEVINSON_SUFFIX = re.compile(r"^(.+?)-\([A-Z][a-z]?(?:,\s*[A-Z][a-z]?)*\)$")


def suffix_base(name: str) -> str | None:
    """The plain name behind an element suffix: "monazite-(Ce)" -> "monazite".

    Players type "monazite", not "monazite-(Ce)", so the base is added as an
    alias of each suffixed mineral. Several minerals then claim the same base
    name and the most familiar one gets it (see build_alias_index), which is
    the sensible reading of a player who just says "monazite".
    """
    match = LEVINSON_SUFFIX.match(name.strip())
    return match.group(1) if match else None


def descriptor_base(name: str, descriptors: frozenset[str]) -> str | None:
    """The eponym in a name that ends with a generic word.

    "Grignard reaction" -> "Grignard", "Haber process" -> "Haber",
    "Diels–Alder reaction" -> "Diels–Alder". Chemists usually say just the
    name, so the base is added as an alias. Only the last word is checked,
    so "Fischer indole synthesis" becomes "Fischer indole", not "Fischer":
    a bare surname is often shared by several reactions, and those short
    forms are better added by hand in aliases.csv where they're unambiguous.

    descriptors: lowercase last words to strip, from the prompt's
    "strip_descriptors" setting in prompts.py.
    """
    words = name.strip().split()
    if len(words) >= 2 and words[-1].lower() in descriptors:
        return " ".join(words[:-1])
    return None


def spelling_variants(normalised: str) -> set[str]:
    """The (already normalised) name plus its British/American spelling twins.

    Variants are generated on the answer side, which keeps the player side
    a single fast lookup: "haematite" is simply stored as another key that
    points at hematite.
    """
    variants = {normalised}
    for brit, amer in SPELLING_PAIRS:
        # Carry the word-start anchor over to the reverse direction too.
        amer_pattern = (r"\b" if brit.startswith(r"\b") else "") + amer
        brit_plain = brit.replace(r"\b", "")
        for v in list(variants):   # list() because we add to the set as we go
            variants.add(re.sub(brit, amer, v))
            variants.add(re.sub(amer_pattern, brit_plain, v))
    return variants


# Brackets joined straight on to a word, as in "Benzo(a)pyrene" or
# "Iron(III) chloride". normalise() drops the brackets and closes the gap
# ("benzoapyrene"), which is what a player gets by typing the brackets; most
# type spaces instead ("benzo a pyrene", "iron iii chloride").
JOINED_BRACKETS = re.compile(r"(?<=\S)[(\[]([^)\]]*)[)\]]")


def spelled_forms(raw: str) -> set[str]:
    """Every normalised way a player might type one name.

    normalise(raw) and its British/American twins, plus, for names with
    brackets joined on to a word, the same with spaces where the brackets
    were. Generated on the answer side only, like spelling_variants, so the
    game's matching (game/matching.js) doesn't change.
    """
    forms = {normalise(raw)}
    if JOINED_BRACKETS.search(raw):
        forms.add(normalise(JOINED_BRACKETS.sub(r" \1 ", raw)))
    variants: set[str] = set()
    for f in forms:
        if f:
            variants |= spelling_variants(f)
    return variants


def alias_forms(raw_names: list[str], min_length: int) -> set[str]:
    """Every normalised form a list of raw names produces.

    The first name is the answer's own name and is always kept, however
    short ("tin"). Later names are aliases and are dropped if shorter than
    min_length (see config.MIN_ALIAS_LENGTH for why).
    """
    forms: set[str] = set()
    for i, raw in enumerate(raw_names):
        n = normalise(raw)
        if not n:
            continue
        if i > 0 and len(n) < min_length:
            continue   # also skips short forms shorter than min_length
        forms |= spelled_forms(raw)
    return forms


def build_alias_index(answers: list[dict], min_length: int) -> tuple[dict[str, str], list[tuple[str, str, list[str]]]]:
    """Map every normalised alias to exactly one answer id.

    answers: dicts with "id", "names" (own name first) and "familiarity".

    Two answers sometimes claim the same text: Wikidata might list "lead
    glance" as an alias of both galena and another lead mineral. Each text
    must point to one answer, so ties are broken by:
      1. an answer's own name beats someone else's alias, then
      2. the more familiar answer wins (it's what the player most likely meant).
    Returns the index and the collisions as (text, winning id, losing ids),
    which build_prompt.py writes, with names, to
    results/<prompt>_collisions.txt for review.
    """
    # form -> list of (priority, familiarity, id) for every answer claiming it
    claims: dict[str, list[tuple[int, float, str]]] = {}
    for a in answers:
        own = spelled_forms(a["names"][0])
        for form in alias_forms(a["names"], min_length):
            priority = 1 if form in own else 0
            claims.setdefault(form, []).append((priority, a["familiarity"], a["id"]))

    index: dict[str, str] = {}
    collisions: list[tuple[str, str, list[str]]] = []
    for form, options in claims.items():
        # Tuples sort element by element, so this orders by priority, then
        # familiarity: the winner ends up first.
        options.sort(reverse=True)
        winner = options[0][2]
        index[form] = winner
        losers = sorted({o[2] for o in options[1:]} - {winner})
        if losers:
            collisions.append((form, winner, losers))
    return index, collisions
