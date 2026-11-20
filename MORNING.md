# Good morning: what changed overnight

Everything below is on your Mac and tested (140 Python tests, the browser tests, and a full game played in a headless browser against your real builds). Nothing needed your accounts, so the GitHub and Google steps are yours, and they're short.

## What's new

- **December fix.** The page views stop at October 2026 until you move `PAGEVIEW_LAST_MONTH` in `config.py`, so builds after 1 December don't refetch everything.
- **Must-accept answers.** Every prompt has `must_accept.csv`. The build checks each one is accepted and fails a prompt check if not. All seven bank prompts pass on last night's builds.
- **Prompt folders and the bank.** Each prompt's settings moved from `prompts.py` to `data/<prompt>/prompt.toml`, with a `status`: `ready` (in the bank) or `draft`. The seven you have are ready.
- **Blurbs.** Two sentences for every gem and fool's gold, in each `blurbs.csv`, shown in the game when someone finds one.
- **The playtest site.** **Publish site** writes `docs/` for GitHub Pages. Each game deals 7 random prompts from the bank, with a mix of subjects, and **Play again** deals new ones. Games go to a Google Sheet when they end, with a feedback form (most fun, most annoying, would you play daily, bugs, anything else).
- **Days, for later.** One day for everyone, changing at 05:00 UTC. **Plan the next 4 weeks**, **Show schedule** and **Check upcoming days** run configurations. Days freeze once they start.
- **The prompt designer.** `new_prompt.py` and three templates, a quick **probe** (size, best-known answers, where they came from, anything odd, missing must-accepts, no page views), and a "via" column showing which subcategory each answer came from.
- **21 draft prompts** ready to pick: amino acids, alloys, plastics, solvents, acids, isotopes, Nobel chemists, chemists, cell parts, hormones, sugars, functional groups, organic reagents, pH indicators, lab techniques, rocks, gemstones, antibiotics, catalysts, fatty acids, vitamins.
- **UI mock-ups**: https://claude.ai/artifact/NaRsWGp2m71CgwYarn57kN (press Play on the first artboard and add answers to watch the column rise and clear; the reveals artboard replays each grade's animation).

## This morning, in order

1. **All tests** → expect `140 passed`.
2. **Build bank prompts** → quick, all cached. It adds the labels, colours, blurbs and checks to the game files. Look for `PASS ... must-accept answers accepted` in each summary.
3. **Play demo** → it now publishes and opens `http://127.0.0.1:8000/docs/`. Play a game, try the feedback form, then run **Playtest report**: your game is in `playtests/`.
4. **The Google Sheet** (about 5 minutes): README.md, "Hosting the playtest", part 1. You'll paste a URL into `SITE_RESULTS_URL` in `config.py`. `SITE_CONTACT` is a placeholder until you pick an address.
5. **GitHub Pages** (about 10 minutes): same section, part 2. **Publish site** first, then **Git → GitHub → Share Project on GitHub** (public), then Settings → Pages → main, /docs.
6. **Probe draft prompts** → paste me the output and I'll fix the sources that need it. Then **Build draft prompts** (or one at a time), review, pick gems, write blurbs, and set `status = "ready"` to add them to the bank.

## Notes

- **Soxhlet extractor.** You took it out as lab equipment's fool's gold, so it stays out. Its blurb is still in `blurbs.csv`, as a fun fact for whoever names it.
- **Your gem and fool's gold picks** all landed in range on last night's build (Indian yellow just: 97.7%, with 97.5% the edge).
- **Leftovers you can delete:** `cache/_sync/` (archives I used to copy files across), the old `out/` folder and `queries/named_reactions.rq`.
- **Why:** DECISIONS.md, 39 to 43. How-to: README.md, "Hosting the playtest", "Planning days" and "Designing a prompt".
