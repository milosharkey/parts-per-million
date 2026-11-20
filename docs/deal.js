// Choosing which prompts a game plays. Pure functions, so tests.js and the
// Node tests can check them without a page (DECISIONS.md, 41 and 42).

// A game of seven shouldn't be all one subject or all specialist prompts.
export const MAX_PER_SUBJECT = 3;
export const MAX_SPECIALIST = 2;

/**
 * Deal `n` different prompts from the bank for a playtest game.
 *
 * bank: [{ id, subject, audience }, ...] (data/bank.json)
 * previous: ids played last game; they're only used again if the bank runs out
 * random: () => number in [0, 1), so tests can fix the order
 *
 * Prompts are taken in random order, skipping any that would break the mix
 * (more than MAX_PER_SUBJECT of one subject, or MAX_SPECIALIST specialist
 * ones). If that leaves the game short, the skipped ones fill it: a small
 * bank still gives a full game.
 */
export function deal(bank, n, previous = [], random = Math.random) {
  const shuffled = bank
    .map((entry) => ({ entry, key: random() }))
    .sort((a, b) => a.key - b.key)
    .map(({ entry }) => entry);
  const seen = new Set(previous);
  const ordered = [...shuffled.filter((e) => !seen.has(e.id)), ...shuffled.filter((e) => seen.has(e.id))];
  const picked = [];
  const skipped = [];
  const subjects = {};
  let specialists = 0;
  for (const entry of ordered) {
    if (picked.length === n) break;
    const subjectFull = (subjects[entry.subject] ?? 0) >= MAX_PER_SUBJECT;
    const tooHard = entry.audience === "specialist" && specialists >= MAX_SPECIALIST;
    if (subjectFull || tooHard) { skipped.push(entry); continue; }
    picked.push(entry);
    subjects[entry.subject] = (subjects[entry.subject] ?? 0) + 1;
    if (entry.audience === "specialist") specialists += 1;
  }
  for (const entry of skipped) {
    if (picked.length === n) break;
    picked.push(entry);
  }
  return picked.map((e) => e.id);
}

/**
 * Today's game date (YYYY-MM-DD). The day changes at the same moment for
 * everyone, `startHourUtc` o'clock UTC, so nobody can play tomorrow early.
 */
export function gameDate(now, startHourUtc) {
  const shifted = new Date(now.getTime() - startHourUtc * 3600 * 1000);
  return shifted.toISOString().slice(0, 10);
}
