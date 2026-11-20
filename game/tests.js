// Browser tests for matching.js and scoring.js. Open game/tests.html via serve.py.
import { normalise, editDistance, typoAllowance, matchAnswer, preparePrompt } from "./matching.js";
import { formatPpm, ppmFromPoints, formatPurity, landmarkPassed } from "./scoring.js";
import { deal, gameDate } from "./deal.js";

const results = [];
const check = (name, got, expected) =>
  results.push({ name, ok: got === expected, got, expected });

const cases = await (await fetch("../tests/shared_cases.json", { cache: "no-store" })).json();

// The same cases the Python tests run (tests/test_shared_cases.py).
for (const [raw, expected] of cases.normalise) {
  check(`normalise(${JSON.stringify(raw)})`, normalise(raw), expected);
}
for (const [points, expected] of cases.format_ppm) {
  check(`formatPpm at ${points} points`, formatPpm(ppmFromPoints(points)), expected);
}

// Game-only rules: typo tolerance.
check("swapped neighbours count as one mistake", editDistance("quratz", "quartz", 2), 1);
check("a missing letter is one mistake", editDistance("quatz", "quartz", 2), 1);
check("gives up early above the limit", editDistance("abcdef", "uvwxyz", 1), 2);
check("no corrections for 3-letter words", typoAllowance(3), 0);
check("one typo from 4 letters", typoAllowance(4), 1);
check("one typo up to 9 letters", typoAllowance(9), 1);
check("two typos from 10 letters", typoAllowance(12), 2);

// A tiny made-up prompt to test the matching rules end to end.
const prompt = preparePrompt({
  answers: [
    { id: "A", name: "Quartz", tier: "tap_water", share: 0.20 },
    { id: "B", name: "Quarts", tier: "analytical_grade", share: 0.001 },  // invented, one letter off
    { id: "C", name: "Galena", tier: "lab_grade", share: 0.05 },
    { id: "D", name: "Hematite", tier: "lab_grade", share: 0.04, spellings: ["haematite", "hematite"] },
    { id: "E", name: "Onyx", tier: "reagent_grade", share: 0.01 },
    { id: "F", name: "Monazite-(Ce)", tier: "analytical_grade", share: 0.0004, spellings: ["monazite ce"] },
    { id: "G", name: "Monazite-(La)", tier: "analytical_grade", share: 0.0001, spellings: ["monazite la"] },
    { id: "H", name: "Molybdite", tier: "analytical_grade", share: 0.0001 },
  ],
  aliases: {
    quartz: "A", quarts: "B", galena: "C", "lead glance": "C", hematite: "D", haematite: "D", onyx: "E",
    "monazite ce": "F", monazite: "F", "monazite la": "G", molybdite: "H", moo3: "H",
  },
});
check("exact match wins over a near one", matchAnswer(prompt, "Quarts").answer.id, "B");
check("own name is exact", matchAnswer(prompt, "  QUARTZ ").status, "exact");
check("British spelling of the name is exact", matchAnswer(prompt, "Haematite").status, "exact");
check("other names count, but as an alias", matchAnswer(prompt, "Lead-Glance").status, "alias");
check("aliases lead to the right answer", matchAnswer(prompt, "Lead-Glance").answer.id, "C");
check("typo corrected", matchAnswer(prompt, "galina").status, "typo");
// "quartx" is one letter from both Quartz and Quarts: the commoner answer wins,
// so a typo can never be "corrected" into a rarer, higher-scoring answer.
check("ties go to the commoner answer", matchAnswer(prompt, "quartx").answer.id, "A");
check("unknown words are rejected", matchAnswer(prompt, "kryptonite").status, "none");
check("4-letter typo is suggested", matchAnswer(prompt, "onyd").answer?.id, "E");
check("short words aren't corrected towards formulas", matchAnswer(prompt, "moon").status, "none");
// A player's brackets are part of the answer, not Wikipedia disambiguation.
check("brackets with a space keep their contents", matchAnswer(prompt, "Monazite (La)").answer?.id, "G");
check("brackets without a hyphen, misspelt", matchAnswer(prompt, "mozanite(la)").answer?.id, "G");
check("plain name goes to the most familiar one", matchAnswer(prompt, "monazite").answer?.id, "F");
check("Wikipedia-style titles still work", matchAnswer(prompt, "Quartz (mineral)").answer?.id, "A");
check("blank input is ignored", matchAnswer(prompt, "  ").status, "empty");

check("purity display", formatPurity(355), "99.972%");
check("landmark passed", landmarkPassed(355)?.label, "24-carat gold");
check("no landmark below the first", landmarkPassed(100), null);

// Dealing a playtest game from the bank (deal.js).
const seeded = (seed) => () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
const bank = [
  ...["a", "b", "c", "d", "e"].map((id) => ({ id, subject: "chemistry", audience: "school" })),
  ...["f", "g", "h"].map((id) => ({ id, subject: "biology", audience: "specialist" })),
  ...["i", "j"].map((id) => ({ id, subject: "physics", audience: "everyone" })),
];
const hand = deal(bank, 7, [], seeded(7));
check("a deal has 7 different prompts", new Set(hand).size, 7);
check("no more than 3 of one subject", hand.filter((id) => "abcde".includes(id)).length <= 3, true);
check("no more than 2 specialist prompts", hand.filter((id) => "fgh".includes(id)).length <= 2, true);
const nextHand = deal(bank, 3, hand, seeded(11));
check("play again avoids last game's prompts", nextHand.every((id) => !hand.includes(id)), true);
check("a small bank still fills the game", deal(bank.slice(0, 4), 7, [], seeded(3)).length, 4);
const families = [
  { id: "minerals", subject: "geology", audience: "everyone", family: "minerals" },
  { id: "gemstones", subject: "geology", audience: "everyone", family: "minerals" },
  { id: "chemists", subject: "chemistry", audience: "everyone", family: "chemists" },
  { id: "nobel", subject: "chemistry", audience: "everyone", family: "chemists" },
  ...["k", "l", "m", "n", "o", "p"].map((id) => ({ id, subject: `s${id}`, audience: "everyone" })),
];
const neverTogether = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].every((seed) => {
  const h = deal(families, 7, [], seeded(seed));
  return !(h.includes("minerals") && h.includes("gemstones")) && !(h.includes("chemists") && h.includes("nobel"));
});
check("one prompt per family in a game", neverTogether, true);
check("a family fills a game only when nothing else is left", deal(families.slice(0, 4), 4, [], seeded(5)).length, 4);
check("the day changes at 05:00 UTC", gameDate(new Date("2026-12-02T04:59:00Z"), 5), "2026-12-01");
check("…and not before", gameDate(new Date("2026-12-02T05:00:00Z"), 5), "2026-12-02");

const failed = results.filter((r) => !r.ok);
document.getElementById("summary").textContent =
  failed.length ? `${failed.length} of ${results.length} failed` : `All ${results.length} passed`;
document.getElementById("summary").className = failed.length ? "fail" : "pass";
document.getElementById("results").innerHTML = results.map((r) =>
  `<li class="${r.ok ? "pass" : "fail"}">${r.ok ? "✓" : "✗"} ${r.name}` +
  (r.ok ? "" : ` — got <code>${r.got}</code>, expected <code>${r.expected}</code>`) + "</li>"
).join("");
