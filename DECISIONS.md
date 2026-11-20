# Design decisions

Why the pipeline is built the way it is. Each entry covers what was decided, why, what else was considered, and when it would be worth revisiting.

If you change one of these, update its entry so the reasoning stays true.

---

## Architecture

### 1. A batch job that writes files, not a live service

**Decision.** The pipeline runs on a developer's machine, builds a prompt once, and writes `results/<prompt>.json`. The game will only ever read these files.

**Why.**
- A day's tiers must be frozen. Every player has to be scored against the same answer set, and scores shouldn't shift mid-day.
- Wikidata is a shared public service. A query takes seconds, and it isn't meant to be hit once per player guess.
- Prepared files can be cached and served cheaply, however many people play.
- The hand pass (checking tiers, picking the gem) happens before anything goes live, which needs a build step anyway.

**Alternatives.** Querying Wikidata live during play: too slow, rate-limited, and the tiers would drift.

**Revisit when.** Never for the daily game. An "unlimited" mode might pre-build a large bank of prompts the same way.

### 2. No database yet: JSON files for output and cache

**Decision.** Downloads are cached as JSON files in `cache/`, and results are JSON plus a CSV in `results/`.

**Why.**
- The data is small: a few MB per prompt, even for thousands of answers.
- There's one writer (whoever runs the build) and no concurrent access.
- Files are easy to open, read and delete, and nothing needs installing or keeping running.

**Alternatives.**
- SQLite: one file, real queries. Worth it once many prompts share entities.
- Postgres: needed for the live game, not the pipeline.

**Revisit when.**
- **Collecting player answers.** Many concurrent writers needs a proper database, most likely Postgres on the game's host.
- **The pipeline grows to dozens of prompts sharing the same compounds.** Move to SQLite.

### 3. Python for the pipeline

**Decision.** The pipeline is plain Python 3, and its only third-party library is `requests`. `pytest` is only needed to run the tests.

**Why.**
- Python is the standard for this kind of data work.
- The chemistry prompts will need RDKit for substructure searches ("contains a benzene ring"), and RDKit is Python-first.
- Few dependencies make setup quick and keep things working for years.

**Alternatives.** JavaScript (one language for the whole project) or Java/Kotlin (native to IntelliJ). Both lack the chemistry tooling.

**Contract with the game.** The pipeline and the game share exactly two things:
- the JSON format in `results/`
- the `normalise()` rule in `aliases.py`

The game will be JavaScript, so `normalise()` must be ported exactly. `tests/test_aliases.py` is the specification it has to pass.

### 4. Prompts as data, curation as CSVs

**Decision.** Each prompt is an entry in `prompts.py`, a SPARQL file and up to four CSVs. Adding a prompt doesn't touch the pipeline code.

**Why.**
- Curation is the real bottleneck of the game, so it should be possible without programming. The CSVs open in any spreadsheet app.
- Every judgement call (this answer counts, that one is the gem) sits in one visible, version-controlled place.
- Every CSV is optional, and names that don't match anything are printed as warnings rather than failing silently.

**Alternatives.** TOML or YAML for prompt definitions would read slightly better. A Python dict avoids a dependency and works on older Python versions.

**Changed.** Prompts are now folders with a `prompt.toml` each, read with Python's built-in `tomllib` (decision 40).

---

## Where answers come from

### 5. Wikidata as the hub

**Decision.** Every answer is a Wikidata item, identified by its Q-number.

**Why.**
- Wikidata links out to nearly every specialist database (PubChem, ChEBI, Mindat, EC numbers), so later prompts can join extra data by ID instead of by fuzzy name matching.
- It's CC0, so it's free to reuse in a commercial game.
- One query language covers every field of science.

**Alternatives.** Scraping Wikipedia list articles: fragile, because formats vary and they get edited. Going straight to specialist databases: one integration per field instead of one overall.

### 6. Minerals: exact species class, plus a hand-made extras list

**Decision.** `queries/minerals.rq` takes items that are directly an instance of "mineral species" (`wdt:P31 wd:Q12089225`). Groups, gem varieties and mineraloids that players will type (feldspar, garnet, ruby, opal) are added by title from `data/minerals/extras.csv`.

**Why.**
- Following subclasses as well (`P31/P279*`) pulls in rocks, synthetic analogues and oddities, so it's harder to control.
- What people type doesn't match the official classification: "ruby" isn't a mineral species (it's corundum), but rejecting it would feel wrong to players. A short curated list solves that precisely.

**Revisit when.** Players keep typing something reasonable that gets rejected. Add it to `extras.csv`.

### 7. An English Wikipedia article is required

**Decision.** Only items with an English Wikipedia article are included.

**Why.**
- The article gives each answer a proper English name to type.
- It also gives an article whose page views we can count.

**Cost.** Valid species that only have articles in other languages are left out.

**Revisit when.** Experts complain about missing species. They could be added as Analytical grade using their Wikidata label, with sitelinks as their only signal.

### 8. A notability floor that's configurable per prompt

**Decision.** `min_views` in `prompts.py`, set to 0 for minerals.

**Why.** For minerals, IMA recognition already guarantees an answer is real and checkable, so an obscure species should still score. Prompts where anyone can invent a valid-sounding answer (compounds, where you can name a molecule nobody has made) will need a real floor.

---

## The rarity model

### 9. Signals, and why they're log-scaled

**Decision.** Familiarity comes from:
- English Wikipedia page views over the last 24 full months
- sitelinks (how many language Wikipedias cover the item)
- curriculum level
- PubMed links (later, compounds only)

Counts are log-scaled first, using `log10(1 + x)`.

**Why these signals.**
- **Page views:** the best free proxy for what people actually know.
- **Sitelinks:** a stable, global signal that's hard to inflate.
- **Curriculum level:** recall is shaped by what people were taught.

**Why these details.**
- **Log scale:** going from 100 to 1,000 views matters as much as going from 100,000 to 1,000,000. Without logs, a handful of famous answers would squash everyone else into the same score.
- **24 months:** smooths out news spikes and school-term swings.
- **The "user" traffic type:** excludes crawlers.
- **English only:** one request per article, and the first players will be English speakers.

**Alternatives.** Summing page views across languages multiplies the requests by about 30, which isn't worth it yet.

**Known gap.** Views of redirects (someone visiting "Haematite") aren't counted towards the main article. The effect is small.

### 10. Z-scores for most signals, a fixed scale for curriculum

**Decision.** Views, sitelinks and PubMed are z-scored within the answer set. Curriculum level uses a fixed scale: GCSE (level 3) = +2 standard deviations, level 2 two thirds of that, level 1 one third.

**Why z-scores.** The signals come in incompatible units. Standardising them means the weights in `config.py` mean what they say.

**Why curriculum is different.** This was found by stress-testing with 5,000 fake minerals. Only a few dozen answers are tagged, and z-scoring such a lopsided signal turns a tag into more than +10 SD. Any tagged mineral then beats even the most-read untagged one. A fixed scale keeps it a strong but bounded nudge.

### 11. Softmax with beta calibrated from a target top share

**Decision.** The predicted share of each answer is a softmax of its familiarity score. Beta is solved for so that the most familiar answer gets `TARGET_TOP_SHARE`, currently 15%.

**Why softmax.** It's the standard model for "choose one option from many", known as a conditional logit. That means the weights and beta can later be fitted directly to real player answers with ordinary statistics tools.

**Why calibrate.** Beta on its own has no intuitive meaning, but "1 in 7 players say the top answer" does, so we set that and solve for beta.

**History.** The first guess was 30%. On the first real mineral run (1,769 answers) that made beta so large that predicted shares collapsed after about 25 answers. The result was 3 Tap water, 9 Lab grade, 16 Reagent grade and 1,739 Analytical grade answers: no real hierarchy. 15% gives a gentler curve.

**Revisit when.** Pilot-survey or player data exists. Fit the weights and beta from it, and drop the guess.

### 12. Tiers by "share above", plus guard rails and a pyramid check

**Decision.** An answer's tier depends on the predicted share of all answers ranked above it:

| Share above | Tier |
|---|---|
| under 50% | Tap water |
| 50–80% | Lab grade |
| 80–98% | Reagent grade |
| 98% and up | Analytical grade |

Guard rails keep Tap water between 2 and 10 answers and Lab grade at 3 or more. The build also checks that each tier is at least twice the size of the one above (`MIN_TIER_GROWTH`).

**Why.**
- It makes tiers mean the same thing on every prompt, whatever its size. Tap water always means "what half of players would say".
- Fixed counts (top 5 = Tap water) don't survive a range from 30 answers to 30,000.
- The Reagent cut-off was moved from 95% to 98% after the first real run. Together with decision 11, that turns 3 / 9 / 16 / 1,739 into roughly 8 / 19 / 190 / 1,550. It's the same pyramid shape as Krillion's published tier counts, where each tier is several times bigger than the one above.
- The pyramid check flags any prompt where that shape breaks down.

### 13. Hand overrides: gem, Fool's gold and minimum tiers

**Decision.** `flags.csv` can set:
- the 100-point gem (`single_crystal`)
- the 15-point Fool's gold (`fools_gold`)
- a minimum tier for any answer (`min:reagent_grade` and so on)

**Why the gem and Fool's gold are hand-picked.** They're judgements about fun, which no signal measures.

**Where they must sit.** The model narrows each choice down, and the build checks the flagged answers against it:

- **The gem** must be rare as an answer but recognisable once revealed. Ideally it's also fun, quirky or in the news. That means the top end of Analytical grade: share above 97.5–99.5% (`GEM_WINDOW`). The lower edge is a little below Analytical's 98% start on purpose: a gem's story is exactly what inflates its page views. Jadarite (kryptonite's formula, for real) lands at 97.7% on the first real run.
- **Fool's gold** is "famously obscure": the rare mineral so notorious for its rarity or its name that everyone has heard of it. Cummingtonite is the model example. That fame keeps it out of the long tail, so it must sit in Reagent grade: 80–98% (`FOOLS_GOLD_WINDOW`).

**Candidate lists.** The build suggests both:

- **Gem candidates:** the most-read answers inside the gem window.
- **Fool's gold candidates:** Reagent grade answers with the highest "notoriety". That's English page views far above what the number of language Wikipedias would predict, calculated as z(log views) − z(log sitelinks).

On minerals, the notoriety list surfaces Moissanite, Cummingtonite, Kyawthuite and Painite, which are exactly the kind wanted.

**Why minimum tiers.** Sometimes the signals simply measure the wrong thing.

The native elements (gold, silver, sulfur, mercury…) are the example. Their page views are for the element articles, which are hugely popular. But knowing that native gold or sulfur counts as a mineral is niche, so as answers to "Name a mineral" they're rare.

A floored answer gets whichever is rarer: its floor, or the tier its position gives it. It's also left out of everyone else's share above, with the remaining shares rescaled, because we don't believe its predicted share and it mustn't push other answers into rarer tiers.

**Revisit when.** Player data exists. Fool's gold can then be confirmed directly (answers given far more often than predicted), and floors checked against real answer rates. Monthly page views would also let the build flag gem candidates that are trending in the news.

---

## Matching what players type

### 14. Normalise once, then look up in a dictionary

**Decision.** Every accepted spelling is reduced to a normalised form at build time and stored in an `aliases` map. At play time, the game normalises the typed text the same way and looks it up.

**Why.**
- Checking an answer becomes instant, however many answers there are.
- The rules live in one readable, tested function.

**Alternatives.** Fuzzy-matching the raw input against every name at play time: slow on big sets, unpredictable, and hard to test.

**What's not here.** Typo tolerance ("quatz") belongs in the game, where it can be restricted to the current prompt's answers and to longer words.

**Brackets.** Only a bracketed word after a space is dropped ("Gold (mineral)" → "gold"). Brackets joined on with a hyphen are kept, because in mineralogy they're part of the name: monazite-(Ce) and monazite-(La) are different minerals. The plain name ("monazite") is added as an alias of each, and goes to the most familiar one.

### 15. Spelling variants are generated on the answer side

**Decision.** British/American twins (haematite/hematite, sulphur/sulfur) are added as extra keys at build time. Some pairs only match at the start of a word.

**Why.** It keeps the play-time side a single lookup. The word-start anchor stops short fragments from mangling other words ("chemistry" must not become "chaemistry").

### 16. Alias collisions: own name first, then familiarity

**Decision.** When two answers claim the same spelling, an answer's own name beats another answer's alias, and after that the more familiar answer wins. Every collision is written, with names, to `results/<prompt>_collisions.txt`.

**Why.** It's the most likely intended meaning. The file uses names as well as Wikidata IDs because a list of Q-numbers can't be checked by eye. (The first version only wrote IDs.)

### 17. Aliases under 3 characters are dropped

**Decision.** `MIN_ALIAS_LENGTH = 3`, except for an answer's own name.

**Why.** Wikidata's alias lists include symbols and abbreviations that would match far too much once typo tolerance is added. Short own names like "tin" still count.

---

## Working with the APIs

### 18. Caching, pacing and rate limits

**Decision.**
- Everything downloaded is cached.
- Wikidata results are keyed by a fingerprint of the query text.
- Page views are fetched **one request at a time**, at most one every 0.25 s.
- A 429 ("too many requests") is waited out for as long as Wikimedia's `Retry-After` header asks (a minute if it doesn't say), up to 3 times per article. If it keeps refusing, or asks for more than 5 minutes, the download stops cleanly with a plain message and exit code 2.
- Server errors (5xx) are retried with backoff, then that one article is skipped.
- A 404 means zero views.
- The User-Agent follows Wikimedia's format: tool/version, contact address, then HTTP library/version.
- The contact address is required. It comes from `contact.txt` (ignored by Git) or the `PPM_CONTACT` environment variable, never from the code itself.

**Why.**
- **Sequential requests:** the first version used 4 parallel threads and crashed with a traceback about 1,000 articles into the first real run, when Wikimedia started refusing requests. The page-view API's documentation asks clients to wait for each request to finish before sending the next, and since 2026 Wikimedia enforces per-user rate limits on all its APIs. Clients whose User-Agent contact isn't recognised drop to the lowest tier.
- **Never build on partial data:** stopping cleanly with progress saved means a rerun later carries on, and the build never runs on partial data.
- **Fingerprinted Wikidata cache:** editing `extras.csv` or a query takes effect on the next run, without having to remember `--refresh`.
- **Caching and saving:** the page-view cache is named after its date window, so a new month starts fresh rather than mixing old and new numbers. It's saved every 50 articles.

**Alternatives considered.**
- *The Action API (`prop=pageviews`):* takes 50 titles per request, so about 50 times fewer requests, but only covers the last 60 days. That gives up the 24-month smoothing.
- *Authenticating with a Wikimedia account token:* Wikimedia gives logged-in users much higher limits. Not tested yet.

**Revisit when.** Prompts with tens of thousands of answers (compounds) make sequential fetching too slow. Try authentication first, then the 60-day batch API.

### 19. Fixture source and tests

**Decision.**
- `FixtureSource` serves saved fake responses through the same interface as `LiveSource`.
- `tests/` covers normalising, scoring and the live-source code (with the network faked), plus a full build on fixtures.

**Why.**
- The whole pipeline can be checked with no internet, in under a second.
- Changes to the model can be made with confidence.
- The alias tests double as the specification for the JavaScript port of `normalise()`.

---

### 20. Display names and matching hand edits by any name

**Decision.**
- The name shown to players is the Wikipedia title minus any disambiguation in brackets ("Garnet", "Mercury"). Wikidata's label ("garnet group", "mercury") is kept as an alias.
- The CSVs in `data/` match an answer by any name it goes by: display name, title, label, aliases or spelling variant.

**Why.**
- Titles read better and are capitalised properly.
- On the first real run, `curriculum.csv` failed to find "garnet" because Wikidata labels it "garnet group". Matching by any name stops that kind of silent miss.

### 21. Results go in `results/`, not `out/`

**Decision.** Output files are written to `results/`.

**Why.** When IntelliJ sets a project up as Java, it uses a folder called `out/` for its own build output, hides it, and can wipe it on a rebuild. The first version used `out/` and its files were hard to get at in IntelliJ.

---

## The demo page

### 22. Plain HTML, CSS and JavaScript, with no framework or build step

**Decision.** The demo is three files plus two modules, loaded with `<script type="module">`. No React, no npm, no bundler. `serve.py` serves it with Python's built-in web server.

**Why.**
- The whole thing is a few hundred lines. A framework would add more to learn than it saves.
- No build step means you edit, save and reload. There's nothing to install and nothing to go out of date.
- Python is already set up for the pipeline, so the server costs nothing.

**Alternatives.** A framework (React, Svelte) makes sense once there are accounts, archives and settings screens. The matching and scoring modules have no page code in them, so they can move across unchanged.

**Why a server at all.** Browsers block `fetch()` from pages opened as `file://`, so the page can't read `results/minerals.json` without one.

### 23. One set of matching rules, written twice, checked against shared cases

**Decision.** `game/matching.js` and `game/scoring.js` port `normalise()` and the ppm maths from Python. `tests/shared_cases.json` holds inputs and expected outputs that both versions are tested against: pytest on the Python side, `game/tests.html` on the JavaScript side.

**Why.** The build normalises every alias, and the game normalises what the player types. If the two ever disagree, valid answers stop matching, and nothing would flag it. Shared cases make a drift fail a test instead of failing quietly in play.

**Alternatives.** Running Python in the browser (Pyodide) is far too heavy for one function. Generating the JavaScript from the Python is overkill at this size.

**When adding a rule.** Change both files, and add a case to `shared_cases.json`.

### 24. Typo tolerance in the game, limited by length

**Decision.**
- An exact match (via the alias map) always wins.
- Otherwise, the typed text is compared with every alias of the current prompt using optimal string alignment distance, which counts a swapped pair of letters as one typo.
- 0 typos for 3 characters or fewer, 1 from 4 characters, 2 from 10. (Originally 1 only from 5; see "Changed" below.)
- Words under 5 characters are only corrected towards answers' own names, not their other aliases.
- If two answers are equally close, the more common one wins, the same rule as alias collisions (decision 16).
- The result screen says what was typed and what it counted as.

**Why.**
- Long mineral names are easy to misspell and that shouldn't cost points.
- On very short names, one typo turns a word into a different word ("tin", "tan", "ton"), so 3-letter answers need to be exact.
- Many mineral names end in "-ite", so two changes to a name under 10 letters too often lands on a different mineral.
- Wikidata's short aliases are mostly mineral symbols and formulas ("cal", "moo3"). Correcting short words towards them turned "coal" and "moon" into suggestions.
- Comparing against one prompt's aliases (about 3,000) takes a few milliseconds, so there's no need for a cleverer index yet.

**Revisit** if a prompt has tens of thousands of aliases (a trigram index or BK-tree would help), or if players report fair answers being refused.

**Changed.** At first a typo match was accepted straight away, so short words got no tolerance at all: a wrong correction would have cost the player an answer. Once decision 27 made corrections a suggestion the player confirms, a wrong guess only costs a glance, and Milo found "onyd" not suggesting Onyx. The threshold dropped from 5 letters to 4. Real words near a mineral now get a suggestion ("oval" → Opal), which is easily ignored.

**Brackets typed by the player.** `normalise()` drops a bracket after a space, because in Wikipedia titles it's disambiguation (decision 14). Player input is different: "Monazite (La)" means Monazite-(La), but it was being read as plain "monazite", which goes to Monazite-(Ce). And "mozanite (la)" fell back to "mozanite", whose nearest match is Moganite. So the game first looks up the typed text with brackets turned into spaces, exact and then typo, and only then tries the plain `normalise()` form ("Gold (mineral)" still works). Found by Milo on the second test day.

### 25. Unknown answers can be retried; repeats can't

**Decision.** In the demo, an answer that isn't on the list shakes the box and lets you try again while time remains. An answer you've already used today is refused the same way. Both are settings in `DAY` in `game/game.js`.

**Why.**
- A typo badly wrong enough to miss shouldn't end the prompt. The timer is pressure enough.
- The demo repeats one prompt, so without the repeat rule you could score seven times with the same rare answer.

**Alternatives.** Krillion's stricter rule: anything not accepted scores 0. Set `retryUnknown: false` to try it. Which feels better is a play-testing question.

### 26. How the score is drawn

**Decision.**
- The column's scale runs from 0 points at the bottom to 100 × the number of prompts at the top. Every gridline is 100 points: one more nine of purity, ten times fewer ppm.
- Each answer adds a band as tall as its points, in its tier's colour. Single crystal bands get a spectrum fill.
- The purity landmarks from the design doc are marked on the column, and the end screen names the cleanest one you passed.
- The page background moves from murky umber to glass white in proportion to points, and all text sits on light cards so it stays readable at every stage.
- Every tier has an icon as well as a colour, so colour is never the only cue. "Reduce motion" turns off the animations.

**Why.** Points are already a log scale of ppm, so a column that's linear in points is a log scale of impurity: equal steps for each extra nine, which is how purity is talked about in practice.

**Revisit in the UI overhaul.** After playing it, Milo prefers the original design-doc idea to the stacked bands: one liquid that shifts from murky umber to clear white as the sample gets purer. The per-answer colours already appear in the chips, the share grid and the answer lists, so the column doesn't need to repeat them. Left as it is for the demo.

### 27. Corrections are suggested, not accepted

**Decision.**
- Only an answer's own name, or its British/American twin, is accepted straight away. The build lists these as `spellings` for each answer in `results/<prompt>.json`.
- A typo, or another name that leads to an answer ("fool's gold" → Pyrite, "monazite" → Monazite-(Ce)), puts the answer's name in the box and shows the correction underneath. Enter again accepts it. Editing the text drops the suggestion, and Esc puts back what the player typed.
- The suggestion doesn't reveal the tier.
- The timer keeps running. A suggestion still unconfirmed when time runs out isn't accepted.

**Why.**
- From Milo's first test day: a typo or an alias can land you on a much commoner answer than you meant, and you should get the chance to say no. Krillion does the same.
- Hiding the tier stops near misses being used to peek at how rare an answer is before committing.
- It also makes alias collisions visible to the player. "monazite" plainly becomes Monazite-(Ce), instead of quietly scoring as one of several minerals.
- Esc undoes the suggestion rather than passing, so a player trying to dismiss it doesn't throw away the prompt.

**Alternatives.** Accepting corrections automatically (the first demo) is faster but takes the choice away. Showing several suggestions at once would also hint at what's on the list.

### 28. Short Wikidata aliases stay in, and gaming them is accepted

**Decision.** Short aliases from Wikidata are kept, even though most are mineral symbols, formulas or abbreviations nobody would say ("cal", "moo3", "cash"). For minerals, there are 77 aliases of 5 characters or fewer that aren't the answer's own name. Typing one exactly suggests its mineral, which is often a rare one.

**Why.** In Krillion, players found that short common words (the "James" exploit: James, Dan, Tom, John) got autocorrected into rare answers. Milo's view is that these are edge cases: anyone who spams short words to score is choosing that over playing properly, and that's on them. The cheaper guards are already in place:
- Words under 5 letters are only corrected towards answers' own names (decision 24).
- Typo ties go to the commoner answer (decision 24).
- A suggestion doesn't reveal the tier (decision 27).

**Revisit**
- if real play data shows a short alias being used far more than its answer's tier predicts, or
- in a data-quality pass. Some of these aliases look wrong as well as unhelpful: "cal" goes to Calclacite, though Cal is normally calcite's official symbol. A narrow build-time filter (aliases with digits, or symbol-style capitals like "MoO3" and "AgTe") would remove most of them without touching real names.

---

## More prompts

### 29. Elements: Wikidata's element class, with symbols as answers

**Decision.**
- Every item that is an instance of "chemical element" (wd:Q11344) with an atomic number from 1 to 118 and an English Wikipedia article.
- The element symbol (P246) is added to each element's aliases, and this prompt lowers the minimum alias length to 1 so one- and two-letter symbols survive. Typing a symbol suggests the element ("fe" → Iron) for the player to confirm.
- Latin and old names (ferrum, wolfram, quicksilver) are added in `data/elements/aliases.csv`. Wikidata's own aliases already include systematic names ("ununoctium", "element 118").

**Why.**
- 118 answers is a small, closed set, which tests whether the tier model behaves when most answers are well known.
- Knowing symbols is real chemistry knowledge, so it should count. The confirm step stops a stray "no" or "be" being taken as nobelium or beryllium without the player seeing it.

**Known gap.** The curriculum list is a first guess at UK school and first-year university teaching, to be checked by someone who's sat through it recently.

### 30. Named reactions: a Wikipedia category, plus short forms

**Decision.**
- The answers are the articles in English Wikipedia's "Name reactions" category. The build lists them through Wikipedia's own API (`list=categorymembers`), then looks them up on Wikidata by title in batches of 200, the same way extras are looked up. `prompts.py` gives a `category` for this prompt instead of a `query`.
- `data/named_reactions/extras.csv` adds famous named processes filed elsewhere (Haber, Ostwald, Solvay, Fischer–Tropsch, Hall–Héroult...). A new `exclude.csv` (available to every prompt) drops category members that aren't reactions, starting with Alpine borane, which is a reagent.
- Each prompt can list generic last words to strip (`strip_descriptors` in `prompts.py`). For reactions, "Grignard reaction" also gets "Grignard", "Haber process" gets "Haber" and "Baeyer–Villiger oxidation" gets "Baeyer–Villiger".
- A short form, whether this one or a mineral's plain name ("allanite" for allanite-(Ce)), is accepted outright when only one answer claims it. When several do ("wittig", "hofmann", "monazite"), it goes to the most familiar one, as before (decision 16), and the player is asked to confirm.
- The name-length check now measures the shortest form accepted outright, because that's what a player actually types.
- Prompt text: "Name a named reaction".

**Why.**
- Wikidata classifies reactions inconsistently (some "chemical reaction", some "organic reaction", some "name reaction", some nothing). The Wikipedia category is maintained by chemistry editors and means exactly "a reaction named after a person".
- Chemists say "a Grignard" or "a Diels–Alder", not the full title. Only the last word is stripped, so "Fischer indole synthesis" becomes "Fischer indole", not "Fischer": a bare surname is often shared, and those short forms are better added by hand where they're unambiguous ("Williamson", "Strecker" in `aliases.csv`).
- Named industrial processes are what chemical engineers know best, and they fit the prompt: they're named after people.

**Alternatives.** A Wikidata class for name reactions would be simpler if it were complete, but reactions are filed under several classes and the category is the better-curated list. Revisit if Wikidata's classification catches up.

**Changed.** The first version read the category from inside one SPARQL query, through the query service's MediaWiki API service (`wikibase:mwapi`). On Milo's first real runs it timed out (HTTP 504, past the 60-second limit) twice, even with `hint:Query hint:optimizer "None"` to stop the planner reordering it. Two plain requests, each of a kind that already worked elsewhere in the pipeline, are more dependable than one clever one.

### 31. The demo day mixes prompts

**Decision.** `DAY.prompts` in `game/game.js` lists seven prompts drawn from all three. Each answer can be used once per prompt, so Gold can be both an element and a mineral. A prompt with no results file yet is swapped for one that has been built, so the day stays seven prompts long, and the start screen says what's missing. The end screen shows every prompt's full list, each with its own search box for long tiers.

**Why.** It's the first time the game feels like the design: different prompts, each with its own pool. Swapping in built prompts means the demo still works on a machine that's only built some of them.

**Seven different prompts.** Once seven answer sets existed, the day became one prompt from each: elements, lab equipment, minerals, pigments, enzymes, named reactions, compounds with a benzene ring. The per-prompt repeat rule and the swap for unbuilt prompts stay, in case a day repeats one.

**Prompt colour bands.** On Milo's first mixed run, a switch from elements to minerals went unnoticed, and element answers on a minerals prompt looked like a bug. Each prompt now has a coloured band across the top of its label (`PROMPT_STYLE` in `game/game.js`), the label flashes "New prompt" when the prompt changes, and a rejected answer names the prompt it was checked against. The colours are kept clear of the tier colours.

### 32. Elements' placeholder names don't count

**Decision.** For elements, Wikidata aliases that are placeholder names are thrown away at build time, using patterns in `prompts.py` (`drop_alias_patterns`, available to any prompt):
- IUPAC systematic names and their symbols: ununoctium (Uuo), unnilquadium (Unq), and misspellings of them that Wikidata carries (ununnubium)
- Mendeleev's predicted names: eka-radon, eka-manganese, dvi-indium
- "element 118" and "atomic number 109"

On the real data that's 157 aliases, and the patterns match none of the 118 real names (checked). Names added by hand in `aliases.csv` are never dropped.

**Why.** Milo's call: all 118 elements are discovered and named, so these names have no use, and a player who answers "ununoctium" hasn't kept up. Knowing the current name is the point of the prompt. It also removes data errors, like "ununnilium" (element 110) listed on Roentgenium (111) as well as Darmstadtium.

**Still counted.** Historical and Latin names: wolfram, quicksilver, ferrum, brimstone, and superseded names such as columbium, masurium, kurchatovium and hahnium. Revisit if those should go the same way.

### 33. Answers read by a different audience get a set tier and don't set the scale

**Problem.** The first real named-reactions build came out 10 / 119 / 267 / 130: a huge middle, too few rare answers, and the pyramid and top-5 checks failing. Milo's first thought was that academic topics have a narrower spread of page views. The numbers didn't bear that out: the spread of log views is similar to minerals (standard deviation 0.56 against 0.70, and elements, the narrowest at 0.34, came out fine), and the model rescales each prompt anyway. What actually happened: six answers get most of their traffic from people who aren't chemists (the Haber process from GCSE revision, Maillard from cookery, Bessemer, Hall–Héroult, Fischer–Tropsch and Solvay from history and economics). Beta is calibrated so the top answer gets 15%, and Haber sat so far above everything that holding it to 15% flattened the other 520. Without those six, the same model gives 10 / 22 / 136 / 352.

Things tried on the real data that didn't fix it: pinning the top 10's combined share instead of the top answer's (reactions only improved to 7 / 46 / 241 / 232), capping familiarity at the 99th percentile (broke minerals), and reweighting views against sitelinks (no change). The six are a different population, and no reweighting of the same signals can tell them apart.

**Decision.** A new flag, `set:<tier>`, in `flags.csv`:
- The answer gets exactly that tier.
- It's left out when calibrating beta, and out of everyone else's share above.
- Its shown share is capped at the top answer's, because the model's figure is the thing we don't believe.

`min:<tier>` keeps its old meaning ("the model overrates this one") and still counts in calibration. Making it work like `set:` was tried and changed the minerals tiers Milo had already approved (Jadarite fell out of the gem window), so the two stay separate.

For named reactions, the six are set by hand, with a first guess at tiers: Haber tap water; Maillard, Fischer–Tropsch and Hall–Héroult lab grade; Solvay and Bessemer reagent grade. The result is 11 / 26 / 142 / 347 and every check passes. Minerals and elements are unchanged.

**Revisit.** This is a hand fix for a measurement problem: page views measure the public, but the players of an academic prompt are chemists. A literature signal (how many papers mention each reaction, from a free source such as OpenAlex) would measure the right audience directly, and would scale to future academic prompts (chemists, laws, equations) without hand-picking outliers. The `pubmed` slot in `config.WEIGHTS` was reserved for this kind of signal.

### 34. Four more prompts, and checking structures for a benzene ring

Milo picked the prompts: lab equipment, compounds with a benzene ring (with a notability cut-off), enzymes, and pigments and dyes.

**Lab equipment** and **pigments and dyes** come from Wikipedia categories, now with subcategories: `category_depth` follows subcategories that many levels down, and `skip_categories` leaves out any whose title contains a given word (Ikat is a weaving technique, not a dye; "companies" lists are makers, not things). Everyday items filed elsewhere (thermometer, microscope, chlorophyll, litmus) come in through `extras.csv`. British names (conical flask, measuring cylinder, fume cupboard) and eponyms said on their own (a Bunsen, a Soxhlet) are in `aliases.csv`.

**Enzymes** are everything on Wikidata with an EC (Enzyme Commission) number and an English article: a clean definition with no class hierarchy to untangle. Enzyme families with no single EC number (DNA polymerase, protease, restriction enzyme) come in through `extras.csv`. The EC numbers themselves, listed as aliases, are dropped (`drop_alias_patterns`): nobody types "EC 3.2.1.1".

**Compounds with a benzene ring.**
- **Source.** Every article that uses a chemical or drug infobox (Template:Chembox, Template:Infobox drug), listed through Wikipedia's API (`list=embeddedin`). That's the most complete list of compounds Wikipedia has. The benzene categories are organised by chemical family, so they miss compounds filed elsewhere: serotonin sits under tryptamines and morphine under opioids, though both contain a benzene ring.
- **Structure.** Each article is looked up on Wikidata by title, as named reactions are, but also fetching its SMILES string (canonical SMILES P233, or isomeric SMILES P2017). Only compounds whose structure has a benzene ring are kept.
- **Notability.** Milo asked for a cut-off. There are two, both in `prompts.py`: articles in at least 5 languages (applied before page views are fetched, which keeps the slow step small) and at least 10,000 English page views over the two years. Answers added by hand in `extras.csv` are exempt from both floors.
- **Prompt text.** "Name a compound with a benzene ring", not "an aromatic compound", because aromatic also covers rings like pyridine and furan, which Milo's definition excludes.

**The benzene check** (`structure.py`) is a small SMILES reader written for this, not RDKit, the standard chemistry toolkit.
- **Why not RDKit.** RDKit is a large compiled package whose builds lag behind new Python versions (Milo's project runs on 3.15), and only one question needs answering.
- **What counts.** A benzene ring is six carbons in a ring, either all written as aromatic or joined by alternating double and single bonds (the Kekulé form Wikidata mostly uses). Pyridine (a nitrogen), cyclohexene (too few double bonds) and benzoquinone (double bonds pointing out of the ring) don't count. Fused systems count if any ring is a benzene ring.
- **Checking.** It was tested against RDKit on 3,624 random spellings of 65 molecules, including large fused systems, steroids, salts and metal complexes, and on every Kekulé arrangement of 11 fused ring systems (87 arrangements): no disagreements. `tests/test_structure.py` keeps fixed cases, plus the RDKit comparison whenever RDKit happens to be installed.

**Revisit.** The two floors are a guess. Check the size of the set after the first real build, and tune them in `prompts.py`.

### 35. Playtests record every attempt

**Decision.**
- **What's recorded.** The demo records everything a player types, not just the answers that counted: answers refused as not on the list, suggestions and whether they were accepted or undone, repeats, passes and time-outs, each with the time into the prompt. Each round's outcome is stored too, with the model's predicted share.
- **Saving.** At the end, **Save my answers** sends the record to `serve.py`, which saves it in `playtests/`, never overwriting an earlier file. Without the server, the browser downloads the file instead.
- **Reporting.** `playtest_report.py` summarises the records prompt by prompt against the current build.
- **Privacy.** `playtests/` is ignored by Git, because records hold players' names.

**Why.**
- Every tier is still a prediction from page views. A playtest is the cheapest check before building a server for daily play.
- Refused answers are the most useful data. They show what's missing from an answer set or its aliases (minerals turning down "erbium" was right; a lab-equipment prompt turning down "measuring cylinder" wouldn't be).
- Declined suggestions show where corrections go wrong.
- Saving through the existing local server means nothing extra to set up.

**Limits.** With a handful of players the counts are hints, not measurements; the report says so. Real calibration needs many players, so it waits for the hosted version.

### 36. Big lookups pace themselves, split, and fetch names last

**Problem.** The first real compounds build stopped at batch 42 of 132. Wikidata's query service gives each user 60 seconds of processing time per 60 seconds and answers 429 when that's spent. The build sent batches back to back, each fetching every English alias of 200 compounds (a common drug has dozens of brand names). One batch took about a minute, used up the budget, and after a single retry the build gave up. Milo's point: Krillion-style prompts often have thousands of answers, so big lookups have to work as a matter of course.

**Decision.** Four changes, all in place for every prompt:
1. **Pace by cost.** After each query, the build rests for as long as the query took (`SPARQL_REST_FACTOR`), so it never uses more than about half the time budget. That stops most 429s before they happen.
2. **Be patient.** On a 429 it waits as long as Wikidata asks (`Retry-After`), up to 5 times per query. Brief outages (502, 503) get 3 tries with growing waits. Only after that does it stop, cleanly, with everything so far cached. Page views get 5 rate-limit waits per article too (it was 3).
3. **Split what's too slow.** A batch that runs past the 60-second limit (500, 504) isn't retried as it is, which would only spend more budget. It's split in half, then in half again if needed (`lookup_batches`). The halves' rows are also saved as the whole batch, so a later run never tries the slow version again.
4. **Two passes.** Prompts built from Wikipedia lists first look up only what the filters need: the item, its language count and, for compounds, its structure. Names and aliases, the heavy part, are fetched afterwards, and only for the answers that survive the benzene check and the notability floors: a few thousand rather than 26,000.

**Why.** Each change removes one way a long build can fail, without making small builds any slower to write or read. The cache already made every batch a checkpoint, so a stopped run was never lost work. What was missing was not stopping in the first place.

**Cost.** Resting doubles the wall-clock time of the Wikidata lookups. That's minutes for the light pass, and safe to leave running. The first build after this change re-queries reactions, lab equipment and pigments with the new light queries, which takes a few minutes, once.

### 37. Dropped connections are retried, and a 429 slows the whole run

**Problem.** The next compounds build got further, then crashed with a traceback at batch 45: "Remote end closed connection without response". Wikidata had answered 429 and asked for a two-minute wait. The build waited, then reused its open connection, which the server had closed while it sat idle. Decision 36 handled every answer Wikidata could give, but not getting no answer at all. Separately, the 429 came even though the build was using about half its time budget: the light queries take a second or two each, so it was sending dozens of them a minute, and the time budget evidently isn't the only limit. And Milo noticed the rerun had started from batch 1 again. That was expected that once (decision 36 changed the queries, so the old batches no longer matched), but nothing on screen said so.

**Decision.**
1. **No answer is retried too.** Any request to any of the APIs that fails with a dropped connection or a timeout is retried on a fresh connection, after 5, 30, 60 and 120 s (`CONNECTION_RETRY_WAITS`). Only then does the build stop, with a plain message and progress saved, never a traceback. After every rate-limit wait the build opens a fresh connection anyway, so the case that crashed shouldn't recur.
2. **A minimum rest.** Every Wikidata query is followed by at least 3 s of rest (`SPARQL_MIN_GAP`), however quick it was.
3. **One 429 slows the rest of the run.** Each 429 doubles every later rest, up to 8 times (`SPARQL_MAX_SLOWDOWN`). If Wikidata has had to ask once, it's busy, and each further 429 costs a long wait, so resting longer is quicker overall.
4. **Wikipedia's API waits out busy spells** (429, 5xx) as well, instead of stopping at once.
5. **Reruns say what they're skipping.** A batched lookup now opens with "44 of 132 batches saved from an earlier run; carrying on from batch 45".
6. **Waits are what they say, and never silent.** Added after a run sat for minutes after "trying again in 15 s". The 15 s was only part of it: the build also rested (4x by then) for as long as the refused query had taken, and the retry could then wait up to 3 minutes for an answer. Now a refusal (429, 502, 503) gets no rest on top of its stated wait, since the query never ran. The request timeout is 90 s rather than 180 s, because the service stops every query at 60 s anyway. Every retry prints "querying Wikidata (…) again, try 2", and any rest over 30 s is announced.

**Why.** "Error free" for a long build means it rides out everything temporary on its own: rate limits, outages, slow queries, dropped connections, and stops only for something that needs a person, such as being offline for minutes on end. Everything here is temporary.

**Cost.** The minimum rest adds about 7 minutes to the compounds build's 132 lookups, and the slowdown more if Wikidata is busy. Page views still take most of the time.

### 38. Reviewing the first full build of the four new prompts

**Problem.** With all seven prompts built from real data, Milo spotted two things: the top of the compounds prompt was mostly medicines and street drugs (ketamine, cocaine, methamphetamine above toluene and phenol), and "List of inorganic pigments" was an answer for pigments and dyes. A full pass over the four new answer sets found more of the same kinds of problem:
- **Junk from category trees.** One level of subcategories under "Laboratory equipment" brought in 312 of 695 articles that aren't lab equipment: cameras, a washing machine, a salad spinner, telescope optics, weapon sights, refinery plant, field-biology nets, techniques, companies, a person, a Disney ride. Pigments had 75: umbrella words, lists, bleach, dyeing crafts, companies, a court case and a law.
- **Public interest in the wrong things.** Medicines are read by patients. Botulinum toxin and ricin are read as poisons. Saffron and spirulina are read as food.
- **Missing answers players will say.** Xylene, polystyrene and Kevlar (no single structure for the benzene check), balance, goggles and NMR (filed outside the category), woad (whose article is titled *Isatis tinctoria*, so the extras lookup missed it), umber and sienna.
- **Repeated names.** Wikidata calls six enzymes "alcohol dehydrogenase" and two articles "Condenser", so the results list showed duplicates.

**Decision.**
1. **Medicines' views count a quarter** for the compounds prompt: a new `view_discounts` option in `prompts.py` scales the page views of every article using a given infobox (here `Template:Infobox drug`), for the familiarity score only. A preview from the built data put paracetamol, benzene, TNT, phenol and aspirin at the top, with toluene, ketamine and cocaine in Lab grade. 0.25 is a first guess for playtests to refine.
2. **Exclusions by hand**, in each prompt's `exclude.csv`, with the reasons in its header. The rule for lab equipment: real lab kit stays however obscure (physics and electronics test gear included); everyday things, components, techniques, plant and organisations go.
3. **Hand-set tiers** for the public-interest outliers: botulinum toxin and ricin `set:reagent_grade`, titin, saffron, methylene blue, spirulina and butterfly pea `min:` floors.
4. **Extras and aliases** for the missing answers. Combination medicines ("amoxicillin/clavulanic acid") are excluded: two compounds sold together aren't a compound.
5. **Repeated names are told apart** by Wikipedia title ("Condenser (laboratory)"), automatically. The most familiar answer keeps the plain name and spelling.

**Why.** The model can only be as good as the list it ranks. Every junk answer is also a free point for anyone who types an everyday word, which matters more than any tier being slightly off.

**Next time.** Excluding 312 titles one by one works, but the problem was really a handful of subcategories. The prompt-design tooling (the next big piece after playtesting) should show which subcategory each article came from, so a whole branch can be skipped in one line.

### 39. The page-view window moves only when asked, and every prompt has must-accept answers

**Problem.** The page views covered the 24 months up to last month, so on 1 December the window would move and the cache, keyed by window, would start again: hours of fetching before any build. Separately, the first review found answers players will definitely say that weren't accepted (xylene, balance, woad), each found by eye.

**Decision.**
1. `PAGEVIEW_LAST_MONTH = "2026-10"` in `config.py` fixes the window's last month. Move it on deliberately, perhaps once a term, knowing the next build of each prompt takes its full time.
2. Each prompt has `must_accept.csv`: 20 to 30 answers players will certainly give. Every build checks each against everything it accepts. A miss prints a `!` warning and fails a prompt check, which the readiness check (decision 42) also sees. All seven bank prompts pass on the current builds.

**Why.** A month's fresher page views changes almost nothing; a surprise refetch costs a morning. And a missing obvious answer is the worst thing a player can meet, so it should fail loudly, before anyone plays.

### 40. Prompts live in folders and form a bank

**Problem.** Prompts were a Python dictionary in `prompts.py`, with their CSVs elsewhere. With dozens more planned, adding one meant editing code, and nothing said which prompts were reviewed and fit to play.

**Decision.** Each prompt is a folder, `data/<id>/`, holding `prompt.toml` (its settings) and its CSVs. `prompts.py` just reads them. New fields: `status` (`ready`: reviewed and in the bank; `draft`: not yet), `label` and `colour` (the label band, so the game no longer hard-codes them), and `subject` and `audience` (for mixing a game). Gems and Fool's gold get two-sentence blurbs in `blurbs.csv`, shown when found. The builds can be run by group: `ready`, `drafts` or `all`. The game file also carries the prompt's metadata and its prompt checks.

This is the "TOML would also work" from the old `prompts.py` notes. It needs Python 3.11+ (`tomllib`), which is fine with 3.15. The settings are identical to before, so cached lookups stay valid.

### 41. The playtest is a static site that sends games to a Google Sheet

**Problem.** Classmates needed to play from a link, not Milo's laptop, and their games had to get back to him. Email was the first idea, but a web page can't send email, and an address in the page would be scraped.

**Decision.**
1. `publish_site.py` copies the game and the built bank prompts into `docs/`, which GitHub Pages serves for free from a public repository. Prompt files are slimmed (no page views) and named by a hash of their contents.
2. In playtest mode each game deals seven random prompts from the bank, at most three of one subject and two specialist ones, and **Play again** deals new ones, avoiding the last set where it can (`game/deal.js`). Milo asked for this over a fixed day, so playtesters can play several times and more prompts get feedback.
3. Each game is sent when it ends, and again if the player fills in the feedback form (most fun and most annoying prompt, would they play daily, bugs, anything else), to a Google Apps Script (`playtest/Code.gs`) that writes rows into a Sheet Milo owns: one per game in "Games", one per attempt in "Attempts". The browser sends it as plain text with `no-cors`, the one way a page can post to Apps Script without a preflight check.
4. The start screen says it's a playtest and exactly what's sent. Nothing personal is collected beyond an optional nickname. The script stores text that would start a formula as plain text, because anyone with the link can send rows.
5. `playtest_report.py` reads the Sheet's Games tab downloaded as CSV, counts each game once and summarises the feedback. `serve.py` now publishes and serves the same site locally, saving games into `playtests/`.

**Why.** No server, no cost, no account for players, and the data lands somewhere Milo can read it (and Claude can, through his Google connection). The answer lists are public in the site's files, which is fine for a playtest; the daily game can move to a small server later if it matters.

### 42. One day for everyone, frozen once it starts

**Problem.** A daily game needs everyone on the same puzzle, prepared well ahead, and unaffected by rebuilds.

**Decision.**
1. The day changes at 05:00 UTC for everyone (`DAY_STARTS_UTC_HOUR`): midnight in New York, 05:00 in London, 07:00 in Helsinki and 21:00 the evening before in California. Milo wanted one day worldwide, ticking over later than Finnish midnight for North America's sake.
2. `data/schedule.csv` lists each date's prompts. `schedule.py fill` plans days not yet planned, from the bank, with the same mix rules as a playtest deal and no repeat within a week when the bank allows. Hand edits are never overwritten.
3. Publishing writes a file per day pointing at the hashed prompt files. A day is rewritten from the latest builds until it starts, then never again.
4. The readiness check looks two weeks ahead for days with nothing planned, prompts not built or not ready, no gem, or a failing prompt check.

**Why.** Building takes hours but scheduling takes seconds, so they're separate: prompts are built into the bank whenever suits, and days only pick from it.

### 43. A prompt designer: templates, probes and 21 drafts

**Problem.** The bank needs dozens of prompts, and each of the first seven took several rounds of building and reviewing by hand.

**Decision.**
1. `new_prompt.py` starts a prompt from one of three templates (`category`, `query`, `infobox`), with every file, a free label colour and a query file if needed.
2. `build_prompt.py probe <id>` takes a quick look without page views: size, best-known candidates, which subcategory each came from, titles that look out of place, and missing must-accept answers. The lookups it makes are cached for the real build.
3. Category builds remember which subcategory each article came from ("via" in the review CSV, and a breakdown in the summary), so a junk branch can go in `skip_categories` in one line. The first lab equipment build needed 312 exclusions one by one (decision 38).
4. Lists, outlines, timelines, glossaries, histories and disambiguation pages are never answers. `drop_title_patterns` adds a prompt's own (isotopes drop "Isotopes of carbon" and the like).
5. `surnames = true` lets a surname count on its own for people prompts ("Curie"), unless two answers share it.
6. 21 drafts are ready to pick, each with its source, notes and must-accept list: amino acids, alloys, plastics, solvents, acids, isotopes, Nobel chemists, chemists, cell parts, hormones, sugars, functional groups, organic reagents, pH indicators, lab techniques, rocks, gemstones, antibiotics, catalysts, fatty acids and vitamins. Their sources couldn't be tried overnight (no access to Wikipedia from where they were written), so the probe is the first step for each.

### 44. The first full build of the drafts, and what it changed

**Problem.** The first real build of all 21 drafts (20 November) showed the categories going wrong both ways. Some were too shallow: antibiotics found 4 of its 20 must-accept answers, hormones 6 and solvents 5, because amoxicillin, testosterone and methanol sit two or three subcategories down. Others pulled in junk from side branches: acid-attack cases in acids, gemologists in gemstones, autophagy proteins in cell parts (insulin came top), and concept pages everywhere (scurvy in vitamins, lactose intolerance in sugars). Medicines rode on their page views (gabapentin top of amino acids), and Wikipedia's capitalised titles showed "PH meter" and "Α-Amylase".

**Decision.**
1. `require_properties` (prompt.toml) keeps only answers whose Wikidata item has one of the listed properties. Prompts about substances require a chemical formula, SMILES, CAS number or PubChem id (hormones also a UniProt id), which drops processes, diseases, people and products in one line. Extras stay, so mixtures like turpentine can be added by hand.
2. Shown names take their first letter from Wikidata's label where the case matters: "pH meter", "mTOR", "p-Xylene", "α-Amylase" (`fix_first_letter`).
3. British spellings: "-isation", "-iser", "-lyse", "fibre" and "grey" join sulph/sulf and colour/color. Names with brackets joined on ("Benzo(a)pyrene", "Iron(III) chloride") also accept spaces where the brackets were ("benzo a pyrene", "iron iii chloride"). Both are generated on the answer side, so the game's matching is unchanged.
4. `views:<factor>` in flags.csv counts one answer's views for less, for answers read about for something else (Thatcher in chemists, ethylene in hormones, fluorine in organic reagents).
5. Families (`family` in prompt.toml) keep prompts that are too alike apart: never two in one game or one day, and a family never on two days running. Minerals and gemstones are one family, chemists and Nobel laureates another. Milo's call: both pairs are worth having, but not back to back.
6. Catalysts no longer counts enzymes. They have their own prompt, and any line drawn between the two would leave someone unhappy (Milo's call).
7. Vitamins and cell parts are retired (`retired/`): the bank was leaning too far into biology and biochemistry, against physical, inorganic and analytical chemistry.
8. When a prompt has an objective limit, every candidate inside it counts: all the isotopes, all the laureates, every acid (carboxylic acids included). The effort goes into keeping out what isn't an answer, not into choosing which real answers are famous enough.
9. The chemists query reads every occupation statement, not only the "truthy" one. When one of a person's occupations is marked preferred, the truthy form sees only that one, which is the likely reason Marie and Pierre Curie were missing (they're extras too, to be sure).
10. Each draft got its clean-up (skips, exclusions, extras, aliases) and a gem and Fool's gold with blurbs, chosen from the 20 November build. Their windows will move once the junk is out, so the next build's checks decide.

In the bank, pigments lost about 40 staining methods (Gram stain, H&E: procedures, not colourants), elements gained a Fool's gold (oganesson) and lab equipment another (the Liebig condenser, named after a man who didn't invent it).

## Known limitations

- The weights, `TARGET_TOP_SHARE`, the tier cut-offs and `CURRICULUM_BOOST_SD` are tuned by eye on one prompt, and remain guesses until real recall data exists.
- `data/minerals/curriculum.csv` is a starter list and needs a proper pass against actual syllabuses.
- English page views only (see decision 9).
- Answers without an English Wikipedia article are left out (see decision 7).
- The demo has no daily rotation, no stored history and no real "what everyone else said" numbers: tiers come from the model's predictions. Those need the server side, which comes later.
- Lab equipment, compounds, enzymes and pigments have had one review pass (decision 38). Their curriculum lists and the medicines discount are still first drafts.
- The 19 drafts have been built once and cleaned up from that build (decision 44), but not built again since. Some fixes rely on category names that haven't been tried (hormones' "Peptide hormones", "Steroid hormones", "Plant hormones"), and none has a curriculum list yet.
- The UI mock-ups (the rising column, reveals, specimen cards, certificate of analysis) are designs, not yet in the game.
