# Parts Per Million: answer-set pipeline and demo

Two parts so far:

- **Step 1, the pipeline** (the Python files): builds the answer set for a prompt.
- **Step 2, the demo** (`game/` and `serve.py`): a playable page that loads those answer sets. See [Playing the demo](#playing-the-demo).
- **Step 3, the playtest and the bank**: a site you can host on GitHub Pages that deals random prompts from a bank of reviewed ones and sends games and feedback to a Google Sheet ([Hosting the playtest](#hosting-the-playtest)), a daily schedule for later ([Planning days](#planning-days)), and a way to design new prompts ([Designing a prompt](#designing-a-prompt)).

The pipeline builds an answer set for each prompt. Seven are reviewed and in the bank: minerals, chemical elements, named reactions, lab equipment, compounds with a benzene ring, enzymes, and pigments and dyes. Another 19 are drafts, built once and cleaned up, waiting for a review (two more, vitamins and cell parts, are retired in `retired/`). Each answer set has:

- every valid answer
- how likely players are to say it
- its rarity tier
- every spelling that should count

It writes the result as a file the game loads.

There's no server and no database to run. It's a batch job. It queries Wikidata (the free database behind Wikipedia) and Wikimedia's page-view statistics, caches what it downloads in `cache/`, and writes plain files to `results/`. Wikidata is effectively your database. You only ever read from it.

```
Wikidata (who counts) ─┐
                       ├─> familiarity score ─> predicted share ─> tier ─> results/<prompt>.json
Page views (how known) ┘          ▲
your CSVs in data/ ───────────────┘  (school level, extra aliases, gem / Fool's gold)
```

## What's in here

| Path | What it is |
|---|---|
| `build_prompt.py` | The script you run. Builds one prompt, or a group (`ready`, `drafts`, `all`), from start to finish. `probe` takes a quick look instead |
| `config.py` | Settings: your contact email, weights, tier cut-offs, points, the site |
| `data/<prompt>/` | Everything about one prompt: `prompt.toml` (its settings) and the CSVs (extra answers, exclusions, school level, aliases, gem and Fool's gold, blurbs, must-accept answers) |
| `prompts.py` | Reads every `data/<prompt>/prompt.toml` into the list of prompts |
| `new_prompt.py`, `templates/` | Starts a new prompt from a template |
| `queries/*.rq` | The Wikidata queries for prompts that use one (others use Wikipedia categories or infoboxes) |
| `publish_site.py` | Copies the game and the built prompts into `docs/`, the folder GitHub Pages serves, and checks the coming days |
| `schedule.py` | Plans which prompts are played on which day (`data/schedule.csv`) |
| `playtest/Code.gs` | The Google Apps Script that collects playtest games into a Google Sheet |
| `docs/` | The published site (made by `publish_site.py`; committed, because GitHub Pages serves it) |
| `sources.py` | Downloads from Wikidata and Wikimedia, with caching |
| `scoring.py` | The rarity model and the ppm maths |
| `aliases.py` | Turns names into the forms players type |
| `structure.py` | Reads a compound's structure (its SMILES string) to check for a benzene ring |
| `tests/` | 140 tests, including full builds of every prompt on fake data that need no internet |
| `tests/shared_cases.json` | Cases both the Python and JavaScript versions must agree on |
| `game/` | The game: `index.html`, `style.css`, `game.js`, `deal.js` (choosing prompts), plus `matching.js` and `scoring.js` ported from Python |
| `game/tests.html` | Browser tests for the JavaScript |
| `serve.py` | Serves the demo on your computer and opens it in the browser. Also saves playtests |
| `playtest_report.py` | Summarises playtests: what people typed against what the model predicted |
| `playtests/` | Saved playtests (created by the first one; ignored by Git, as they hold players' names) |
| `results/` | Results (created on first run) |
| `cache/` | Downloads (created on first run). Delete it to start fresh |

## Reading the code

Every module starts with a note on what it does and why. Every non-obvious choice has a comment next to it, and [DECISIONS.md](DECISIONS.md) gathers the bigger design decisions in one place: what was decided, the alternatives, and when to revisit each one.

A good reading order. The main code is just under 1,000 lines, much of it comments:

1. `config.py`: every setting, with the reasoning behind each value.
2. `build_prompt.py`, the `build()` function: the whole pipeline as numbered stages.
3. `scoring.py`: the rarity model as small pure functions.
4. `aliases.py`: how typed text is matched.
5. `sources.py`: fetching, caching and retries.
6. `tests/`: each test says what it protects. `test_aliases.py` doubles as the specification for the JavaScript version of the matching rules.

For the demo:

7. `game/matching.js` and `game/scoring.js`: the same rules as `aliases.py` and `scoring.py`, plus typo tolerance.
8. `game/game.js`: the game itself. Start at `startDay()` and follow the flow.
9. `game/style.css`: the look, organised by screen area.

## Setting up IntelliJ on your Mac

### 1. Install Python 3.12 or newer

(3.11 is the oldest that works: the prompt settings files are read with `tomllib`, which arrived in 3.11.)

Open Terminal and run `python3 --version`.

- If it says 3.12 or higher, you're set.
- If it's older, or macOS offers to install developer tools, install the latest Python 3 from [python.org/downloads](https://www.python.org/downloads/). It's a normal macOS installer. If you use Homebrew, `brew install python@3.13` works too.

### 2. Get the free student licence

Apply at [jetbrains.com/shop/eform/students](https://www.jetbrains.com/shop/eform/students) with your Aalto email. Then in IntelliJ, go to **Help → Register…** and log in with that JetBrains account. This unlocks the full IDE: JavaScript for step 2, plus the database and CSV tools.

### 3. Install the Python plugin (before opening the project)

**IntelliJ IDEA → Settings… (⌘,) → Plugins → Marketplace**, search **Python**, install the one by JetBrains, and restart.

Do this first. The project is marked as a Python module, and IntelliJ won't recognise it without the plugin.

### 4. Open the project

1. Unzip `ppm-pipeline` into `~/IdeaProjects/`.
2. In IntelliJ, choose **File → Open…** and select the `ppm-pipeline` folder.
3. Click **Trust Project** when asked.

### 5. Create the Python environment

This gives the project its own private copy of the libraries, kept in `.venv/`.

1. Open **File → Project Structure… (⌘;) → Project**.
2. Under **SDK**, choose **Add SDK → Python SDK…**.
3. Pick **Virtualenv Environment → New**.
4. Set **Base interpreter** to the Python 3.12+ from step 1.
5. Set **Location** to `~/IdeaProjects/ppm-pipeline/.venv`.
6. Click **OK** twice.

Then open `requirements.txt` and click **Install requirements** in the yellow banner. If no banner appears, open the Terminal tab at the bottom of IntelliJ (it uses `.venv` automatically) and run:

```
pip install -r requirements.txt
```

### 6. Check it all works without the internet

Pick **All tests** in the run menu at the top right and press ▶. You should see `140 passed`. (The offline builds' pyramid checks fail on purpose: the fake data is too small to make a pyramid.)

Then run **Build all (offline test data)**. It builds every prompt in the bank from fake data in `tests/fixtures/` and prints a summary, which shows the whole pipeline works before you touch the network. Its output goes to `results/offline/` so it never mixes with real results.

### 7. Do the real run

1. Give the script your email address: create a file called `contact.txt` in the project folder (**right-click the project → New → File**) containing just the address. Wikimedia asks every script to identify itself, and the script refuses to run until you do. The file is ignored by Git, so your address never ends up on GitHub, and code updates never touch it. Setting an environment variable `PPM_CONTACT` works too.
2. Run **Build bank prompts** (or **Build minerals**, **Build elements** or **Build named reactions** for just one; the others build with `python build_prompt.py <name>`).

The first run queries Wikidata, then fetches two years of page views for every article. The two years end with the month in `PAGEVIEW_LAST_MONTH` in `config.py` (October 2026 for now): it only moves when you change it, because moving it means fetching every page view again (decision 39). Requests go one at a time, as Wikimedia's page-view API asks. Progress and time left are printed as it goes, and everything is cached, so later runs take seconds.

The compounds prompt is the slow one the first time. It lists every article with a chemical or drug infobox (about 26,000), looks up just their structures and language counts on Wikidata in batches of 200, keeps those with a benzene ring and articles in 5+ languages, fetches page views for those, and only then looks up names and aliases for the ones that are left. Expect it to take the best part of an hour. If it stops, run it again: it carries on from the last batch.

### If the run stops early

Since 2026, Wikimedia limits how many requests each user can make, and Wikidata's query service gives each user 60 seconds of query time per minute. The build paces itself to stay well inside both. If it's still told to slow down, it waits as long as asked, several times over, and leaves longer gaps between queries for the rest of the run. A Wikidata lookup that's too slow is split in half automatically, and a dropped connection is retried on a fresh one. If it's refused even so, it stops with a message saying so, not a crash. Everything fetched so far is already saved, so wait a while (an hour is usually plenty) and run the same build again. It carries on where it stopped.

The build never runs on partial data, because tiers worked out from half the page views would be wrong.

If the run configurations ever go missing, make one yourself: **Run → Edit Configurations… → + → Python**, with script `build_prompt.py`, parameters `minerals` (or another prompt's folder name in `data/`, or `ready`, `drafts` or `all`) and working directory set to the project folder.

## Reading the results

Each prompt writes three files to `results/`, named after it (`minerals`, `elements`, `named_reactions`, `lab_equipment`, `aromatics`, `enzymes`, `pigments`). The terminal summary shows:

- how many answers landed in each tier
- the 15 most common answers with their predicted shares
- **gem candidates** (rare but recognisable) and **Fool's gold candidates** (famously obscure)
- the automatic **prompt checks**: the design doc's three, the gem and Fool's gold ranges, and the must-accept answers
- for category prompts, **where the answers came from**: which subcategory brought in how many

`results/<prompt>_review.csv` is for your hand pass. Right-click it and choose **Edit as Table**, or open it in Numbers. It lists every answer ranked from most to least common, with its tier, share, page views (and any discount), sitelinks, school level and, for category prompts, the subcategory it came from, plus links to Wikidata and Wikipedia.

`results/<prompt>.json` is what the game loads: the answers with their tiers and points, the spellings it accepts outright for each, plus `aliases`, which maps every accepted spelling (normalised) to an answer.

`results/<prompt>_collisions.txt` lists spellings that more than one answer could claim, and which answer gets each one. Most are expected, like plain "monazite" being shared by monazite-(Ce), monazite-(La) and so on, or "wittig" by the Wittig reaction and the Wittig rearrangement.

## Tuning it

All of these are quick, because reruns use the cache. Use `--refresh` only to pick up edits made on Wikidata itself:

- **Wrong tiers overall?** Edit `data/minerals/curriculum.csv`, where school level is the strongest lever. Then try `TARGET_TOP_SHARE` (lower spreads answers into rarer tiers) and `TIER_CUTS` in `config.py`. The pyramid check in the summary tells you if the shape breaks.
- **One answer clearly too common?** Give it a minimum tier in `data/minerals/flags.csv`, like `gold,min:reagent_grade`. The native elements are already there, because their page views are for the element, not the mineral.
- **Too many or too few compounds?** The benzene-ring prompt has two notability floors in `data/aromatics/prompt.toml`: `min_sitelinks` (articles in at least this many languages) and `min_views` (English page views over the two years). Raise them for a smaller, better-known set.
- **A few answers read mostly by non-players, flattening everything else?** Pin them with `set:<tier>`, like `Haber process,set:tap_water` in `data/named_reactions/flags.csv`. They get exactly that tier and stop setting the model's scale (decision 33).
- **A whole kind of answer read mostly by non-players?** Discount it in `prompt.toml` with `view_discounts`, which scales down the page views of every article using a given infobox. The compounds prompt counts medicines' views at a quarter, because patients read them (decision 38). The `view_weight` column in the review CSV shows who was discounted.
- **Junk in a category-built prompt?** The summary's "Where the answers came from" shows which subcategories brought it in: add a word from a bad one's name to `skip_categories` in `prompt.toml`. Single articles go in `exclude.csv`. Lists, outlines and disambiguation pages are left out automatically.
- **An answer players will definitely say?** Add it to `must_accept.csv`. Every build checks them, and one that isn't accepted fails a prompt check, so a gap shows up before a player finds it.
- **Two answers with the same name?** Nothing to do: the less familiar one is shown by its Wikipedia title, like "Condenser (laboratory)".
- **A spelling that should count?** Add it to `data/minerals/aliases.csv`. British/American pairs like haematite/hematite are already handled.
- **A group or gem that's missing?** Add its exact Wikipedia title to `data/minerals/extras.csv`. Feldspar, ruby, gold and similar ones are there already. The next run notices the change and re-queries by itself.
- **Pick the day's gem and Fool's gold** in `data/minerals/flags.csv` (`single_crystal`, `fools_gold`). The summary suggests candidates for both and checks your picks. The gem must sit at the top end of Analytical grade, and Fool's gold must be famously obscure, so in Reagent grade. The windows are `GEM_WINDOW` and `FOOLS_GOLD_WINDOW` in `config.py`.

Any name in a CSV that doesn't match an answer is printed as a `!` warning, so typos don't fail silently.

To explore Wikidata yourself, paste any file from `queries/` into [query.wikidata.org](https://query.wikidata.org) and press ▶.

## Playing the demo

1. Run **Build bank prompts** once, so each prompt in the bank has its file in `results/`.
2. Run **Play demo**. It publishes the site into `docs/` (exactly what GitHub Pages would serve), starts a small web server on your Mac and opens the game in your browser.
3. Stop it with the red square when you're done.

Why a server? Browsers won't let a page opened straight from disk read other files, so double-clicking `docs/index.html` shows an error instead. `serve.py` uses Python's built-in web server, so there's nothing to install, and it's only reachable from your own computer. Locally, finished games are saved into `playtests/` rather than sent to the Sheet.

How the demo plays:

- Each game deals seven random prompts from the bank (prompts with `status = "ready"`), with a mix: no more than three of one subject or two specialist prompts. **Play again** deals new ones, avoiding the last game's where it can. The coloured band on the label shows which prompt you're on.
- 25 seconds each. Type an answer and press **Enter**. **Esc** passes. Something that isn't on the list doesn't use up your go, so you can try again while the burette runs.
- Element symbols count, as a suggestion to confirm ("fe" → Iron). For reactions, the name on its own is enough: "Grignard" counts as Grignard reaction. British names count for lab equipment ("conical flask"), and brand names for compounds ("Prozac" → fluoxetine).
- Near misses are suggested, not accepted outright: press **Enter** again to accept, edit it, or **Esc** to get your own text back. How close counts: none for 3 letters or fewer, 1 typo from 4 letters, 2 from 10 letters.
- Finding the gem or Fool's gold shows its blurb (from `blurbs.csv`).
- The end screen has your ppm and purity, the share text, a feedback form, and each prompt's full answer list by tier. Your answers are outlined.

The settings (prompts per game, time limit, playtest or daily) are in `config.py` under "The playtest site". Rules of play (retrying unknown answers, repeats) are in `RULES` at the top of `game/game.js`.

**Checking the JavaScript.** With the demo running, open `/game/tests.html` (the address is printed when it starts). It runs the shared cases from `tests/shared_cases.json`, the typo rules and the dealing rules in the browser. The same shared cases run in Python as part of **All tests**, so if the two versions ever drift apart, one side fails.

**Editing the page.** Edit the files in `game/`, not `docs/` (which is rewritten every publish), then stop and start **Play demo**.

## Hosting the playtest

The plan: the site lives on GitHub Pages (free), and every finished game, plus any feedback, is added to a Google Sheet you own. Nothing to run or pay for. About fifteen minutes, once.

**1. The Sheet and its script.**
1. Make a new Google Sheet, for example "PPM playtest results".
2. **Extensions → Apps Script**. Replace everything in `Code.gs` with the contents of `playtest/Code.gs`, and save.
3. Choose `setup` in the function menu and press **Run**. Allow it to edit your spreadsheets. (Google may say the app isn't verified: **Advanced → Go to project**, since it's your own.) The Sheet now has **Games** and **Attempts** tabs.
4. **Deploy → New deployment**, the cog → **Web app**. Execute as: **Me**. Who has access: **Anyone**. **Deploy**, and copy the Web app URL (it ends in `/exec`).
5. Paste it into `SITE_RESULTS_URL` in `config.py`. Put the address you want players to contact in `SITE_CONTACT` (it's a placeholder for now).

**2. GitHub.**
1. Run **Publish site**. It writes `docs/`.
2. In IntelliJ: **Git → GitHub → Share Project on GitHub** (on older versions, **VCS → Share Project on GitHub**). Leave **Private** unticked: free GitHub Pages needs a public repository. `.gitignore` already keeps out your contact file, the cache, results and playtests.
3. On github.com, open the repository: **Settings → Pages**. Source: **Deploy from a branch**. Branch: **main**, folder **/docs**. **Save**.
4. A minute or two later the site is at `https://<your-username>.github.io/<repository>/`. Send that link to your classmates.

**Each update after that:** build, **Publish site**, then commit and push (**Git → Commit**, then **Commit and Push**). Tick **Unversioned Files** as well as **Changes**: every rebuilt prompt gets a new file in `docs/data/prompts/`, and if it's left behind the site can't load it ("Couldn't load the prompts … 404"). In Terminal, `git add -A` does the same.

**What players see.** The start screen says it's a playtest and what's sent: their answers (including ones that weren't accepted) and, if they fill it in, their feedback and a nickname. Each game is sent when it ends; the feedback form sends it again with the feedback.

## Running a playtest

The tiers are predictions from page views. Playtests check them against what people actually type.

- **Online:** players use the link. Their games arrive in the Sheet. To run the report, download the **Games** tab as CSV (**File → Download → Comma-separated values**) into `playtests/`.
- **On your Mac:** run **Play demo** and let people play. Games are saved in `playtests/` directly.

Then run **Playtest report**. It counts each game once, even though it arrives twice (at the end, and again with feedback), and goes prompt by prompt:
- **Answers given:** how many players gave each one, against the tier and share the model predicted. Rare-tier answers given by more than one person are flagged as worth a look.
- **Not on the list:** everything typed that wasn't accepted, counted. These are the most useful lines: missing answers, missing aliases, British spellings.
- **Corrections, passes and time:** how many corrections were suggested, accepted and undone, plus passes, time-outs and average time to answer.
- **Feedback:** most fun and most annoying prompts, whether people would play daily, and everything they wrote about bugs and ideas.

It also writes `results/playtest_report.csv`, with one row per answer given.

With five or ten players the counts are hints, not measurements. The point is to catch tiers that are clearly wrong and answers that are clearly missing.

## Planning days

For the daily game rather than the playtest. The day changes at the same moment for everyone: 05:00 UTC (`DAY_STARTS_UTC_HOUR` in `config.py`), which is midnight in New York, 05:00 in London, 07:00 in Helsinki and 21:00 the evening before in California.

1. **Plan the next 4 weeks** adds days to `data/schedule.csv` that aren't planned yet, from the bank, mixing subjects and not repeating a prompt within a week when the bank is big enough. Edit the file by hand as much as you like: planned days are never changed.
2. **Publish site** writes a file for each day. A day is rewritten from your latest builds until it starts, then frozen: everyone playing it is scored against the same list, however often you rebuild. Prompt files are named by their contents, so old days keep theirs.
3. **Check upcoming days** (also run at the end of every publish) lists anything that would stop a day in the next two weeks from being played as planned: nothing scheduled, a prompt not built or not ready, no gem, or a failing prompt check.
4. To try the daily mode: `?mode=daily` on the site's address, and locally `?mode=daily&date=2026-12-01` to preview a day. Set `SITE_MODE = "daily"` in `config.py` when the real game starts.

**Show schedule** lists the next three weeks.

## Designing a prompt

Every prompt is a folder in `data/`, and its settings are in `prompt.toml` there. 19 drafts are ready to pick (amino acids, alloys, plastics, solvents, acids, isotopes, Nobel chemists, chemists, hormones, sugars, functional groups, organic reagents, pH indicators, lab techniques, rocks, gemstones, antibiotics, catalysts not counting enzymes, fatty acids), each with its source, the answers it must accept, and a gem and Fool's gold to check after its next build. Retired prompts live in `retired/`, where nothing reads them.

**Taking a draft into the bank:**
1. **Probe draft prompts** (or `python build_prompt.py probe <id>`): a quick look at every draft, without page views. It shows each one's size, its best-known candidates, where they came from (which subcategory), titles that look out of place (companies, dates, colons), and which must-accept answers are missing. It writes `results/<id>_probe.csv`.
2. Fix what it shows: a junk subcategory goes in `skip_categories`, single articles in `exclude.csv`, missing answers in `extras.csv` or `aliases.csv`. Probe again.
3. Build it (`python build_prompt.py <id>`, or **Build draft prompts** for all of them). Read the summary: its prompt checks, "Where the answers came from", and the gem and Fool's gold candidates. Open `results/<id>_review.csv` for the full list.
4. Pick the gem (and a Fool's gold if there's a good one) in `flags.csv`, write their two-sentence blurbs in `blurbs.csv`, then set `status = "ready"` in `prompt.toml`. It's in the bank from the next **Publish site**.

**Starting a new one:** `python new_prompt.py <id> <template> "<text>"`, for example `python new_prompt.py noble_gases category "Name a noble gas"`. Templates are in `templates/`: `category` (Wikipedia categories: most prompts), `query` (a Wikidata query, when Wikidata has a clean class) and `infobox` (every article with an infobox, for big compound sets). It makes the folder with every file, a label colour nobody else uses, and the query file if needed.

### Prompt settings

The fields of `prompt.toml`. The folder name is the prompt's id, which also names its cache files.

| Field | What it does |
|---|---|
| `text` | What the player sees: "Name an alloy" |
| `label` | The short name on the label's colour band |
| `status` | `ready` (reviewed, in the bank) or `draft` |
| `subject`, `audience` | For mixing a game: subject (chemistry, biochemistry, biology, earth science, physics, lab, history of science) and who'd know the answers (everyone, school, university, specialist) |
| `colour` | The label colour |
| `query` | A Wikidata query in `queries/` returning `item`, `label`, `title`, `sitelinks`, `aliases`, or… |
| `category` | …a Wikipedia category, or a list of them, with `category_depth` (levels of subcategories to read) and `skip_categories` (subcategories with any of these words aren't read), or… |
| `templates` | …every article using these infoboxes |
| `require_benzene` | Keep only compounds whose structure has a benzene ring |
| `require_properties` | Keep only answers whose Wikidata item has one of these properties, e.g. `["P274", "P233", "P231", "P662"]` (chemical formula, SMILES, CAS number, PubChem id): for prompts about substances, this drops the processes, diseases, people and products categories also hold. Extras always stay (decision 44) |
| `family` | Prompts too alike to play together share a family (`minerals` for minerals and gemstones, `chemists` for chemists and Nobel laureates): one per game, and never on two days running (decision 44) |
| `min_sitelinks`, `min_views` | Notability floors: articles in at least this many languages; page views over the window |
| `view_discounts` | Count some infoboxes' page views for less, e.g. medicines read by patients (decision 38) |
| `kind` | A label for answers in the review CSV |
| `min_alias_length` | Shortest alias that counts (elements use 1, for symbols) |
| `drop_alias_patterns` | Regular expressions: aliases that shouldn't count |
| `drop_title_patterns` | Regular expressions: titles that aren't answers (lists, outlines and disambiguation pages never are) |
| `strip_descriptors` | Last words to strip into a short form: "Grignard" for Grignard reaction |
| `surnames` | For people: a surname on its own counts, unless two answers share it |

And the files beside it: `curriculum.csv` (school level), `aliases.csv`, `extras.csv`, `exclude.csv`, `flags.csv`, `blurbs.csv` and `must_accept.csv` (answers checked every build: one missing fails a prompt check).

The flags in `flags.csv`: `single_crystal` (the gem), `fools_gold`, `min:<tier>` (no more common than this tier), `set:<tier>` (exactly this tier), and `views:<factor>` (between 0 and 1), which counts one answer's page views for less, like `view_discounts` does for a whole infobox: Margaret Thatcher's article is read for her politics, not her chemistry. A name can have more than one flag, one per row.

**What counts as an answer.** When a prompt has a clear, objective limit, every candidate inside it is an answer: all the Nobel laureates, every isotope, every acid. The work is in keeping out what isn't one (topics, processes, people in the wrong place), not in deciding which real answers are famous enough.
