"""The rarity model and ppm maths: pure functions, so tested with small
hand-checkable numbers."""
import math

import pytest

import config
from scoring import (
    assign_tiers, calibrate_beta, familiarity, format_ppm, ppm_from_points,
    predicted_shares, purity_percent, tiers_with_floors, zscores,
)


def test_zscores_mean_zero_sd_one():
    z = zscores([1.0, 2.0, 3.0, 4.0])
    assert sum(z) == pytest.approx(0)
    assert math.sqrt(sum(v * v for v in z) / len(z)) == pytest.approx(1)
    assert zscores([5.0, 5.0]) == [0.0, 0.0]


def test_familiarity_drops_missing_and_flat_signals():
    signals = {"views": [1.0, 2.0, 3.0], "pubmed": None, "sitelinks": [4.0, 4.0, 4.0]}
    scores = familiarity(signals, config.WEIGHTS)
    assert scores == pytest.approx(zscores([1.0, 2.0, 3.0]))


def test_prescaled_signal_is_not_zscored():
    # One tagged answer among many must not dwarf the other signals.
    n = 1000
    signals = {
        "views": [float(i) for i in range(n)],
        "curriculum": [0.0] * (n - 1) + [2.0],
    }
    weights = {"views": 0.5, "curriculum": 0.5}
    scores = familiarity(signals, weights, prescaled=("curriculum",))
    assert scores[-1] - scores[-2] == pytest.approx(0.5 * 2.0, abs=0.01)


def test_shares_sum_to_one_and_beta_hits_target():
    scores = [3.0 - i * 0.1 for i in range(60)]
    beta = calibrate_beta(scores, 0.30)
    shares = predicted_shares(scores, beta)
    assert sum(shares) == pytest.approx(1)
    assert max(shares) == pytest.approx(0.30, abs=1e-6)


def test_tiers_follow_share_above():
    shares = [0.30, 0.15, 0.10, 0.10, 0.10, 0.10, 0.05, 0.04, 0.03, 0.02, 0.01]
    tiers = assign_tiers(shares)
    assert tiers[:2] == ["tap_water", "tap_water"]          # 0%, 30% above
    assert tiers[2] == "tap_water"                           # 45% above
    assert tiers[3:6] == ["lab_grade"] * 3                   # 55-75% above
    assert tiers[6] == "reagent_grade"                       # 85% above
    assert tiers[-1] == "analytical_grade"


def test_tier_guard_rails():
    # One giant answer: it alone covers 90%, so the rules must force a
    # second Tap water and three Lab grade answers.
    shares = [0.90] + [0.01] * 10
    tiers = assign_tiers(shares)
    assert tiers.count("tap_water") == config.TAP_WATER_MIN
    assert tiers.count("lab_grade") >= config.LAB_GRADE_MIN
    # Tiers never go back up as answers get rarer.
    order = [t for t, _ in config.TIER_CUTS]
    assert [order.index(t) for t in tiers] == sorted(order.index(t) for t in tiers)


def test_ppm_scale():
    assert ppm_from_points(0) == pytest.approx(1_000_000)
    assert ppm_from_points(355) == pytest.approx(281.8, rel=1e-3)
    assert ppm_from_points(700) == pytest.approx(0.1)
    assert purity_percent(300) == pytest.approx(99.9)


def test_format_ppm():
    assert format_ppm(ppm_from_points(0)) == "1,000,000 ppm"
    assert format_ppm(ppm_from_points(145)) == "35,500 ppm"
    assert format_ppm(ppm_from_points(355)) == "282 ppm"
    assert format_ppm(ppm_from_points(580)) == "1.58 ppm"
    assert format_ppm(ppm_from_points(700)) == "100 ppb"


def test_floored_answers_get_their_floor_and_dont_push_others_down():
    shares = [0.30, 0.25, 0.15, 0.10, 0.08, 0.05, 0.04, 0.03]
    floors = [None, "reagent_grade", None, None, None, None, None, None]
    tiers, above = tiers_with_floors(shares, floors)
    assert tiers[1] == "reagent_grade"
    # Without the floored 25%, the third answer sits at 0.30 / 0.75 = 40% above,
    # so it's still Tap water rather than being pushed into Lab grade.
    assert above[2] == pytest.approx(0.30 / 0.75)
    assert tiers[2] == "tap_water"


def test_floor_never_makes_an_answer_more_common():
    shares = [0.5, 0.3, 0.1, 0.05, 0.03, 0.02]
    floors = [None, None, None, None, None, "lab_grade"]
    tiers, _ = tiers_with_floors(shares, floors)
    assert tiers[-1] != "lab_grade"   # its position already makes it rarer


def test_untrusted_answers_dont_set_the_scale():
    # An outlier marked untrusted (set:<tier> in flags.csv) must calibrate
    # exactly as if it weren't there.
    scores = [9.0, 2.0, 1.5, 1.0, 0.5, 0.0]
    trusted = [False, True, True, True, True, True]
    assert calibrate_beta(scores, 0.3, trusted) == calibrate_beta(scores[1:], 0.3)
    assert calibrate_beta(scores, 0.3) < calibrate_beta(scores[1:], 0.3)   # without the mask it flattens
