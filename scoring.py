"""The rarity model: familiarity signals -> predicted share -> tiers -> ppm.

All the maths from the design doc's rarity and scoring sections lives here,
as small pure functions (numbers in, numbers out, no files or network), so
each one can be tested on its own and reused by other prompts.

The model in one paragraph: every answer gets a familiarity score from its
page views, how many language Wikipedias cover it, and how early people
meet it at school. A softmax turns those scores into a predicted share of
players giving each answer. Answers are ranked by share, and each one's
tier depends on how much share sits above it. DECISIONS.md explains why
each step works the way it does.
"""
from __future__ import annotations

import math

import config


def zscores(values: list[float]) -> list[float]:
    """Standardise a list: mean 0, standard deviation 1.

    Signals come in wildly different units (millions of views, dozens of
    sitelinks). Z-scoring puts them on a common scale so the weights in
    config.WEIGHTS mean what they say. If every value is the same, the
    signal can't tell answers apart, so it contributes zero.
    """
    n = len(values)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / n)
    if sd == 0:
        return [0.0] * n
    return [(v - mean) / sd for v in values]


def familiarity(
    signals: dict[str, list[float] | None],
    weights: dict[str, float],
    prescaled: tuple[str, ...] = (),
) -> list[float]:
    """Weighted sum of standardised signals: one familiarity score per answer.

    Most signals are z-scored within the answer set. Names listed in
    `prescaled` are already on a standard-deviation scale and are used as
    they are. That's for curriculum level: only a few dozen of several
    thousand minerals are tagged, and z-scoring such a lopsided signal turns
    a tag into a huge number (more than +10 SD), letting any tagged mineral beat
    even the most-read untagged one. A fixed scale keeps the boost sensible.

    Signals that are missing (None) or identical for every answer carry no
    information, so they're dropped and the remaining weights rescaled to
    sum to 1.
    """
    active = {
        name: values for name, values in signals.items()
        if values is not None and name in weights and len(set(values)) > 1
    }
    if not active:
        n = len(next(v for v in signals.values() if v is not None))
        return [0.0] * n
    total = sum(weights[name] for name in active)
    std = {
        name: (list(values) if name in prescaled else zscores(values))
        for name, values in active.items()
    }
    n = len(next(iter(active.values())))
    return [sum(weights[name] / total * std[name][i] for name in active) for i in range(n)]


def predicted_shares(scores: list[float], beta: float) -> list[float]:
    """Softmax: the predicted share of players giving each answer.

    share_i = exp(beta * score_i) / sum of exp(beta * score_j)

    This is the standard model for "pick one option from many" (a
    conditional logit), which means it can later be fitted directly to real
    player answers. Beta sets how strongly players pile onto the familiar
    answers: 0 makes every answer equally likely, large values hand nearly
    everything to the top answer.

    Subtracting the top score before exponentiating doesn't change the result
    but stops exp() overflowing for large beta.
    """
    top = max(scores)
    weights = [math.exp(beta * (s - top)) for s in scores]
    total = sum(weights)
    return [w / total for w in weights]


def calibrate_beta(scores: list[float], target_top_share: float,
                   trusted: list[bool] | None = None) -> float:
    """Find the beta that gives the most familiar answer `target_top_share`.

    trusted (optional, one flag per score) leaves some answers out of the
    calibration: those marked set:<tier> in flags.csv, whose page views come
    mostly from people who aren't players. Without this, one answer read by a different audience
    sets the scale for everything. On the first named-reactions build, the
    Haber process (GCSE revision and history traffic) sat so far above the
    rest that holding it to 15% squashed the other 520 reactions into one
    huge middle tier (DECISIONS.md, 33).

    The top answer's share only ever grows as beta grows, so a simple
    bisection search works: try the midpoint, keep the half that contains
    the target, repeat. 80 rounds is far more precision than we need and
    still takes no noticeable time.
    """
    if trusted is not None:
        scores = [s for s, t in zip(scores, trusted) if t] or scores
    lo, hi = 0.0, 50.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if max(predicted_shares(scores, mid)) < target_top_share:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def assign_tiers(shares_desc: list[float]) -> list[str]:
    """Tier for each answer, given shares sorted from most to least common.

    An answer's tier depends on its "share above": how much of all predicted
    answers is taken by answers ranked higher. With the default cut-offs,
    the answers that make up the first 50% of what players say are Tap
    water, the next 30% Lab grade, the next 15% Reagent grade, and the long
    tail Analytical grade. This is what makes tiers comparable across
    prompts with five answers or fifty thousand.

    The guard rails from config.py then fix degenerate cases, without ever
    making a rarer answer land in a more common tier than one above it.
    """
    tiers: list[str] = []
    above = 0.0
    for share in shares_desc:
        for name, cut in config.TIER_CUTS:
            if above < cut:
                tiers.append(name)
                break
        above += share

    n = len(tiers)
    tap = tiers.count("tap_water")
    if tap < config.TAP_WATER_MIN:
        # One huge answer covered half on its own: promote the next one(s).
        for i in range(min(config.TAP_WATER_MIN, n)):
            tiers[i] = "tap_water"
    elif tap > config.TAP_WATER_MAX:
        # Very flat prompt: cap Tap water and push the rest down a tier.
        for i in range(config.TAP_WATER_MAX, tap):
            tiers[i] = "lab_grade"

    # Make sure the answers straight after Tap water include enough Lab grade.
    tap = tiers.count("tap_water")
    for i in range(tap, min(tap + config.LAB_GRADE_MIN, n)):
        tiers[i] = "lab_grade"
    return tiers


# Tiers from most to least common, for comparing them.
TIER_ORDER = [name for name, _ in config.TIER_CUTS]


def tier_for_share_above(above: float) -> str:
    """The tier an answer gets from its share above, before guard rails."""
    for name, cut in config.TIER_CUTS:
        if above < cut:
            return name
    return TIER_ORDER[-1]


def rarer(a: str, b: str) -> str:
    """Whichever of two tiers is rarer."""
    return a if TIER_ORDER.index(a) >= TIER_ORDER.index(b) else b


def tiers_with_floors(shares_desc: list[float], floors: list[str | None]) -> tuple[list[str], list[float]]:
    """Tiers when some answers have a hand-set minimum tier (a "floor").

    A floor says "the model overrates this answer". Gold is the example: its
    page views are for the element, not the mineral, and knowing that native
    gold counts as a mineral is niche. So floored answers:
      - don't count towards anyone else's share above (we don't believe their
        predicted share, so they mustn't push other answers into rarer tiers);
        the remaining shares are rescaled to sum to 1, so the cut-offs still
        mean "half of all answers" and so on;
      - get whichever is rarer: the tier their position would give them, or
        their floor.
    Answers without a floor are tiered by assign_tiers as normal, guard rails
    included. Returns the tiers and each answer's share above.
    """
    trusted_total = sum(s for s, f in zip(shares_desc, floors) if f is None) or 1.0
    trusted_tiers = iter(assign_tiers([s / trusted_total for s, f in zip(shares_desc, floors) if f is None]))
    tiers: list[str] = []
    share_above: list[float] = []
    above = 0.0
    for share, floor in zip(shares_desc, floors):
        share_above.append(above)
        if floor is None:
            tiers.append(next(trusted_tiers))
            above += share / trusted_total
        else:
            tiers.append(rarer(tier_for_share_above(above), floor))
    return tiers, share_above


# ---- The ppm score (design doc, scoring: parts per million) -----------------
# These aren't used by the pipeline itself. They live here so the score maths
# has one tested reference implementation for the game to match.

def ppm_from_points(points: float) -> float:
    """Impurity left in the sample: 1,000,000 ppm at 0 points, 0.1 at 700.

    Every 100 points removes 90% of the remaining impurity (one more "nine"
    of purity), which is why the scale is logarithmic.
    """
    return 10 ** (6 - points / 100)


def purity_percent(points: float) -> float:
    """1,000,000 ppm is the whole sample, so 1% = 10,000 ppm."""
    return 100 - ppm_from_points(points) / 10_000


def format_ppm(ppm: float) -> str:
    """Three significant figures, switching to ppb below 1 ppm.

    "35,500 ppm", "282 ppm", "1.58 ppm", "100 ppb".
    """
    if ppm >= 1:
        rounded = float(f"{ppm:.3g}")   # round to 3 significant figures
        return f"{rounded:,.0f} ppm" if rounded >= 100 else f"{rounded:g} ppm"
    ppb = float(f"{ppm * 1000:.3g}")
    return f"{ppb:,.0f} ppb" if ppb >= 100 else f"{ppb:g} ppb"
