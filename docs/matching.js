// Matching what the player types against a prompt's answers.
//
// normalise() is a line-for-line port of normalise() in aliases.py. The
// pipeline stores every accepted spelling in normalised form, so the game
// must normalise typed text in exactly the same way or valid answers won't
// match. tests/shared_cases.json holds cases both versions must agree on
// (run them here with game/tests.html, and in Python with pytest).
//
// On top of exact lookup, this file adds typo tolerance, which the pipeline
// deliberately leaves to the game (DECISIONS.md, 14).

const GREEK = {
  "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon",
  "ζ": "zeta", "η": "eta", "θ": "theta", "κ": "kappa", "λ": "lambda",
  "μ": "mu", "ν": "nu", "ξ": "xi", "π": "pi", "ρ": "rho", "σ": "sigma",
  "τ": "tau", "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
};

const SUBSCRIPTS = Object.fromEntries(
  [..."₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹"].map((ch, i) => [ch, String(i % 10)])
);

/** Lowercase, plain-letter version of a name. Same steps, same order, as aliases.py. */
export function normalise(text) {
  let t = [...text].map((ch) => SUBSCRIPTS[ch] ?? ch).join("");
  for (const [letter, name] of Object.entries(GREEK)) {
    t = t.split(letter).join(name).split(letter.toUpperCase()).join(name);
  }
  // NFKD splits "ö" into "o" + a combining mark; dropping the marks leaves
  // plain letters ("Mössbauer" -> "Mossbauer"), like unicodedata in Python.
  t = t.normalize("NFKD").replace(/\p{Mn}/gu, "");
  t = t.toLowerCase().replaceAll("ß", "ss");
  t = t.replace(/\s\([^)]*\)/g, " ");          // "Gold (mineral)" -> "Gold"
  t = t.replace(/[‐-―−_\/-]/g, " ");  // dashes, the minus sign, underscores, slashes
  t = t.replace(/[^a-z0-9 ]/g, "");            // apostrophes, commas, dots...
  t = t.replace(/\s+/g, " ").trim();
  if (t.startsWith("the ")) t = t.slice(4);
  return t;
}

/**
 * How many typing mistakes to look past, by length of the normalised input.
 * A correction is only ever a suggestion the player confirms (decision 27),
 * so this can be generous: "onyd" offers Onyx. Three letters or fewer get
 * none, though: one letter is a third of the word, and "tin" is one away
 * from "tan", "sin" and "ton", so almost anything would suggest something.
 * Two mistakes only from 10 letters, because so many mineral names end in
 * "-ite" that two changes to a shorter one lands on a different mineral.
 */
export function typoAllowance(length) {
  if (length >= 10) return 2;
  if (length >= 4) return 1;
  return 0;
}

/**
 * Edit distance counting insertions, deletions, substitutions and swaps of
 * two neighbouring letters ("quratz" -> "quartz" is one mistake, not two).
 * This is the "optimal string alignment" version of Damerau-Levenshtein.
 * Gives up early and returns max + 1 once the distance must exceed max,
 * which keeps checking thousands of aliases fast.
 */
export function editDistance(a, b, max) {
  if (Math.abs(a.length - b.length) > max) return max + 1;
  let before = null;                                 // row i - 2
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j);  // row i - 1
  for (let i = 1; i <= a.length; i++) {
    const row = [i];
    let rowMin = i;
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      let d = Math.min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + cost);
      if (before && i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
        d = Math.min(d, before[j - 2] + 1);         // swapped neighbours
      }
      row.push(d);
      rowMin = Math.min(rowMin, d);
    }
    if (rowMin > max) return max + 1;
    before = prev;
    prev = row;
  }
  return prev[b.length];
}

/**
 * Prepare a prompt file from the pipeline (results/<prompt>.json) for play:
 * answers by id, and the alias list as entries for the typo search.
 */
export function preparePrompt(data) {
  // "spellings" (own name plus British/American twins) comes from the build.
  // Files built before it existed fall back to just the normalised name.
  const answers = data.answers.map((a) => ({ ...a, spellings: new Set(a.spellings ?? [normalise(a.name)]) }));
  const byId = Object.fromEntries(answers.map((a) => [a.id, a]));
  return {
    ...data,
    answers,
    byId,
    aliasEntries: Object.entries(data.aliases),
    // Just the answers' own names and spellings, for correcting short words.
    nameEntries: answers.flatMap((a) => [...a.spellings].map((s) => [s, a.id])),
    gem: data.answers.find((a) => a.tier === "single_crystal") ?? null,
    foolsGold: data.answers.find((a) => a.tier === "fools_gold") ?? null,
  };
}

/**
 * The keys to look up for what a player typed, best first.
 *
 * normalise() drops a bracket after a space, because in Wikipedia titles
 * that's disambiguation: "Gold (mineral)" is just gold. But when a player
 * types brackets, they're part of the answer: "Monazite (La)" means
 * Monazite-(La), not plain "monazite" (which goes to Monazite-(Ce)). So the
 * first key turns brackets into spaces and keeps what's inside. The plain
 * normalise() key comes second, for anyone who types a Wikipedia-style title.
 */
export function playerKeys(typed) {
  const kept = normalise(typed.replace(/[()[\]{}]/g, " "));
  const plain = normalise(typed);
  return kept === plain ? [kept] : [kept, plain];
}

/** Exact lookup: "exact" for the answer's own spellings, "alias" for other names. */
function exactMatch(prompt, key) {
  const id = prompt.aliases[key];
  if (!id) return null;
  const answer = prompt.byId[id];
  return { status: answer.spellings.has(key) ? "exact" : "alias", answer, key };
}

/**
 * The closest alias within the typo allowance, commoner answer on a tie.
 * Words under 5 letters are only corrected towards answers' own names.
 * Wikidata's other short names are mostly mineral symbols and formulas
 * ("cal", "moo3"), which would turn "coal" and "moon" into suggestions.
 */
function closestMatch(prompt, key) {
  const allowed = typoAllowance(key.length);
  if (allowed === 0) return null;
  const candidates = key.length < 5 ? prompt.nameEntries : prompt.aliasEntries;
  let best = null;
  for (const [alias, id] of candidates) {
    if (Math.abs(alias.length - key.length) > allowed) continue;
    const distance = editDistance(key, alias, allowed);
    if (distance > allowed) continue;
    const answer = prompt.byId[id];
    if (!best || distance < best.distance ||
        (distance === best.distance && answer.share > best.answer.share)) {
      best = { distance, answer, alias };
    }
  }
  return best && { status: "typo", answer: best.answer, key, distance: best.distance, matched: best.alias };
}

/**
 * Work out which answer, if any, the player meant.
 *
 * 1. An exact match on the normalised text always wins. It's "exact" if the
 *    text is the answer's own name (or its British/American twin), and
 *    "alias" if it's another name that leads there ("fool's gold" -> Pyrite).
 * 2. Otherwise the closest alias within the typo allowance counts: "typo".
 * 3. If several answers are equally close, the more common one wins. That
 *    stops a typo from being "corrected" into a rarer, higher-scoring answer.
 *
 * The game accepts "exact" straight away and asks the player to confirm
 * "alias" and "typo", showing the answer's name (DECISIONS.md, decision 27).
 *
 * Returns { status: "exact" | "alias" | "typo" | "none" | "empty", answer?, key, ... }.
 */
export function matchAnswer(prompt, typed) {
  const keys = playerKeys(typed);
  if (!keys[0]) return { status: "empty", key: "" };
  // Each key is tried in full (exact, then typo) before the next, so
  // "mozanite (la)" is corrected to Monazite-(La) rather than falling back
  // to plain "mozanite" and landing on Moganite.
  for (const key of keys) {
    const match = exactMatch(prompt, key) ?? closestMatch(prompt, key);
    if (match) return match;
  }
  return { status: "none", key: keys[0] };
}
