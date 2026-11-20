"""Settings shared by every prompt build.

Everything you might want to tune lives here, so the other modules contain
logic only. Values marked "design doc" come from the Parts Per Million
design doc; DECISIONS.md explains the reasoning behind each choice.

Before your first live run, put your email address in a file called
contact.txt in the project folder (see CONTACT below).
"""
from __future__ import annotations

import os
from pathlib import Path

# ---- Folders ---------------------------------------------------------------
# Paths are built from this file's location rather than the current working
# directory, so the scripts behave the same whether you run them from
# IntelliJ, from the terminal, or from inside another folder.
ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "cache"      # downloaded Wikidata results and page views
OUT_DIR = ROOT / "results"      # finished answer sets for the game
# (Not "out": IntelliJ treats a folder called out/ as its own build output,
# hides it and may wipe it on a rebuild.)
DATA_DIR = ROOT / "data"        # hand-edited CSVs (curriculum, extras, flags)
QUERY_DIR = ROOT / "queries"    # SPARQL queries, one per prompt

# ---- Talking to Wikimedia --------------------------------------------------
# Wikimedia's API rules ask every script to send a User-Agent that names the
# tool and gives a way to contact its author. Generic or missing agents can
# be blocked, unrecognised ones get the lowest rate limit, and a contact
# address lets them warn you instead of banning you if the script
# misbehaves. sources.LiveSource refuses to run while CONTACT is still the
# example address, and adds the HTTP library's name and version to the end,
# giving the format Wikimedia recommends:
#   PartsPerMillionPipeline/0.2 (you@example.com) python-requests/2.32.3
#
# Your address lives outside the code, so updates to this file never touch
# it and it never ends up on GitHub. Put it in a file called contact.txt in
# the project folder (just the address on the first line; the file is in
# .gitignore), or set an environment variable called PPM_CONTACT, which wins
# if both exist.


def _contact() -> str:
    env = os.environ.get("PPM_CONTACT", "").strip()
    if env:
        return env
    local = ROOT / "contact.txt"
    if local.exists():
        lines = local.read_text(encoding="utf-8").strip().splitlines()
        if lines and lines[0].strip():
            return lines[0].strip()
    return "your-email@example.com"


CONTACT = _contact()
USER_AGENT = f"PartsPerMillionPipeline/0.2 ({CONTACT})"

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"   # for listing a category's articles
PAGEVIEWS_API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"

# Pacing for the page-view API. Its documentation asks clients to wait for
# each request to finish before sending the next, and since 2026 every
# Wikimedia API also has per-user rate limits. So requests go one at a time,
# at most one every MIN_REQUEST_INTERVAL seconds. When Wikimedia answers
# "429 Too Many Requests" we wait as long as it asks (its Retry-After
# header, or a minute if it doesn't say), up to RATE_LIMIT_RETRIES times per
# article. If it asks for more than MAX_RATE_LIMIT_WAIT seconds, or keeps
# refusing, the download stops cleanly: everything fetched so far stays in
# the cache, and running the build again carries on from there.
MIN_REQUEST_INTERVAL = 0.25     # seconds; at most 240 requests a minute
RATE_LIMIT_RETRIES = 5
MAX_RATE_LIMIT_WAIT = 300       # seconds
SERVER_ERROR_RETRIES = 4        # for 5xx errors, with 2, 4, 8, 16 s waits

# Wikidata's query service gives each user 60 seconds of processing time per
# 60 seconds, and answers 429 ("too many requests", with a Retry-After) when
# that's used up. Long builds stay well inside it by resting after every
# query for SPARQL_REST_FACTOR times as long as the query took: 1.0 means at
# most half the budget is ever used. If the service still says 429 (it's
# shared, and busy spells happen), the build waits as long as it asks, up to
# SPARQL_RATE_LIMIT_RETRIES times per query. A brief outage (502, 503) gets
# SPARQL_SERVER_RETRIES tries with 15, 30, 60 s waits. A query that runs past
# the 60-second limit (500, 504) isn't retried as it is: batched lookups split
# it in half instead (build_prompt.lookup_batches). See DECISIONS.md, 36.
#
# Quick queries still get a rest of at least SPARQL_MIN_GAP seconds: the
# budget isn't the only limit, and dozens of one-second queries in a row
# drew a 429 even at half the budget. Each 429 also doubles every rest for
# the rest of the run, up to SPARQL_MAX_SLOWDOWN times: if Wikidata had to
# ask once, it's busy, and asking again costs a long wait. See DECISIONS.md, 37.
SPARQL_REST_FACTOR = 1.0
SPARQL_MIN_GAP = 3.0            # seconds between Wikidata queries, at least
SPARQL_MAX_SLOWDOWN = 8
SPARQL_RATE_LIMIT_RETRIES = 5
SPARQL_SERVER_RETRIES = 3

# A dropped connection or a request that times out says nothing about the
# data: usually the server closed a connection that sat idle (during a long
# rate-limit wait, say), or the wifi blipped. Every request, to any of the
# APIs, is retried on a fresh connection after each of these waits (seconds)
# in turn, and only then does the build stop, cleanly. See DECISIONS.md, 37.
CONNECTION_RETRY_WAITS = (5, 30, 60, 120)

# Page views are summed over this many full calendar months, ending last
# month. Two years smooths out news spikes (a mineral in the headlines for a
# week) and seasonal swings (school terms), while still reflecting what
# people read now. See DECISIONS.md, "Familiarity signals".
PAGEVIEW_MONTHS = 24

# The last month counted, fixed so builds after a month ends don't start the
# page-view cache from scratch (every article fetched again: hours for the
# big prompts). Move it on when you want fresher numbers, for example once a
# term, and expect the next build of each prompt to take its full time.
# None goes back to "always last month". See DECISIONS.md, 39.
PAGEVIEW_LAST_MONTH = "2026-10"

# ---- Rarity model (design doc, rarity model) -------------------------------
# How much each familiarity signal counts. A signal a prompt doesn't have
# (PubMed links only exist for compounds) is dropped and the rest are
# rescaled to sum to 1, so these are relative weights, not percentages.
# They're starting guesses: the plan is to refit them from pilot-survey and
# player data once it exists.
WEIGHTS = {"views": 0.40, "sitelinks": 0.20, "pubmed": 0.15, "curriculum": 0.25}

# Curriculum level is put on a fixed scale instead of being z-scored like
# the other signals (see scoring.familiarity for why). A level-3 (GCSE)
# answer gets a boost worth this many standard deviations of familiarity;
# level 2 gets two thirds of it, level 1 one third.
CURRICULUM_BOOST_SD = 2.0

# Beta controls how strongly players pile onto the obvious answers. Rather
# than picking beta directly (it has no intuitive meaning), we choose the
# predicted share of the single most common answer and solve for beta.
# 0.15 means "about 1 in 7 players say the top answer". The first guess,
# 0.30, made the curve so steep on the real mineral data that predicted
# shares collapsed after ~25 answers, leaving almost nothing between Lab
# grade and the long tail (DECISIONS.md, 11). Still a guess until real
# player data replaces it. A prompt can set its own (target_top_share in
# prompt.toml): short or flat lists, where nearly everyone gives one of a
# few answers, need a steeper curve than minerals (DECISIONS.md, 46).
TARGET_TOP_SHARE = 0.15

# ---- Tiers and points (design doc, scoring) ---------------------------------
# An answer's tier depends on its "share above": the predicted share of all
# answers ranked above it. The first answers that together cover half of
# what players will say are Tap water, the next 30% Lab grade, the next 18%
# Reagent grade, and the last 2% (the long tail) Analytical grade. Order
# matters: assign_tiers takes the first tier whose cut-off is above the share.
# Tuned on the first real mineral run to give a pyramid where each tier is
# several times bigger than the one above (about 6 / 18 / 160 / 1,600),
# the same shape as Krillion's published tier counts.
TIER_CUTS = [
    ("tap_water", 0.50),
    ("lab_grade", 0.80),
    ("reagent_grade", 0.98),
    ("analytical_grade", float("inf")),
]

TIER_LABELS = {
    "tap_water": "Tap water",
    "lab_grade": "Lab grade",
    "reagent_grade": "Reagent grade",
    "analytical_grade": "Analytical grade",
    "single_crystal": "Single crystal",   # hand-picked gem, set in flags.csv
    "fools_gold": "Fool's gold",          # hand-picked trap, set in flags.csv
}

POINTS = {
    "tap_water": 10,
    "lab_grade": 30,
    "reagent_grade": 60,
    "analytical_grade": 85,
    "single_crystal": 100,
    "fools_gold": 15,
}

# ---- Gem and Fool's gold (DECISIONS.md, 13) ---------------------------------
# Where the model must place an answer, as (lowest, highest) share above,
# before it can be the hand-picked gem or Fool's gold. The build checks the
# answers in flags.csv against these and suggests candidates from inside them.
#
# Gem: rare as an answer, but recognisable once revealed, so the top end of
# Analytical grade (which starts at 98%). The lower edge sits a little
# below 98% because a gem's story is exactly what inflates its page views:
# Jadarite, the real mineral with kryptonite's formula, lands at 97.7% on
# the first real run. Set it to 0.98 to insist on Analytical grade proper.
GEM_WINDOW = (0.975, 0.995)
# Fool's gold: "famously obscure", the rare mineral everyone has heard is
# rare. Fame keeps it out of the long tail, so it must sit in Reagent grade.
FOOLS_GOLD_WINDOW = (0.80, 0.98)

# Guard rails on tier sizes. Without them, one dominant answer (say 90% of
# predicted share) would leave Tap water with a single answer and Lab grade
# empty, which makes for a poor reveal screen.
TAP_WATER_MIN = 2
TAP_WATER_MAX = 10
LAB_GRADE_MIN = 3

# ---- Automatic prompt checks (design doc, prompt design) ----------------------
MIN_SET_SIZE = 25             # fewer answers and the tiers stop meaning anything
MIN_TOP5_SHARE = 0.30         # a prompt needs some obvious answers to feel fair
MAX_MEDIAN_NAME_LENGTH = 20   # 25 seconds isn't long enough to type long names
# The tier pyramid (advice, not a requirement: DECISIONS.md, 46). Each tier
# should hold at least as many answers as the one above it. Below lab grade
# it should also grow by a factor that rises with the size of the set, from
# 1x at PYRAMID_SMALL_SET answers or fewer to MIN_TIER_GROWTH at
# PYRAMID_LARGE_SET or more (on a log scale between): a short list where
# most answers are well known, like the 118 elements, can't make a steep
# pyramid, and shouldn't have to. Tap water and lab grade are a handful of
# answers each by design, so between them only "at least equal" applies.
MIN_TIER_GROWTH = 2.0
PYRAMID_SMALL_SET = 100
PYRAMID_LARGE_SET = 1000

# Aliases shorter than this are ignored unless they're the answer's own name.
# Wikidata's alias lists include chemical symbols and abbreviations ("Sn",
# "Q") that would match far too much once typo tolerance is added in step 2.
MIN_ALIAS_LENGTH = 3

# ---- The playtest site (publish_site.py, DECISIONS.md, 41 and 42) -------------
# publish_site.py copies the game and the built prompts into SITE_DIR, which
# GitHub Pages serves as it is. serve.py (Play demo) publishes, then serves
# the same folder locally.
SITE_DIR = ROOT / "docs"

# "playtest": every game deals PROMPTS_PER_GAME random prompts from the bank
#             (prompts with status = "ready"), and Play again deals new ones.
# "daily":    everyone plays the day scheduled in data/schedule.csv.
SITE_MODE = "playtest"
PROMPTS_PER_GAME = 7
TIME_LIMIT_SECONDS = 25

# Where finished games are sent: the web app URL of the Google Apps Script in
# playtest/Code.gs, once you've deployed it (README.md, "Hosting the
# playtest"). Empty means the page offers a download instead.
SITE_RESULTS_URL = "https://script.google.com/macros/s/AKfycbzJa4aqg9XZRaOPZvNaXXwCOJ_4o-WEuLvLYISX__NFvDy2LG1S452hRfpzDXFtnS0H/exec"

# Shown on the page for questions and bug reports. A placeholder until you
# choose an address (a PPM one later); nothing reads contact.txt for this.
SITE_CONTACT = "ppm-feedback@example.com"

# A new day starts at this hour (UTC) for everyone, so nobody plays tomorrow
# early: 05:00 UTC is midnight in New York, 05:00 in London, 07:00 in
# Helsinki and 21:00 the evening before in California.
DAY_STARTS_UTC_HOUR = 5

# The readiness check (publish_site.py --check) looks this many days ahead.
READINESS_DAYS = 14
# The schedule filler (schedule.py) won't repeat a prompt within this many
# days, when the bank is big enough to avoid it.
SCHEDULE_MIN_GAP_DAYS = 7
