// Choosing which prompts a game plays. Pure functions, so tests.js and the
// Node tests can check them without a page (DECISIONS.md, 41 and 42).

// A game of seven shouldn't be all one subject or all specialist prompts,
// and never has two prompts from one family (minerals and gemstones, say:
// prompt.toml, family), which would feel like the same question twice.
export const MAX_PER_SUBJECT = 3;
export const MAX_SPECIALIST = 2;

/**
 * Deal `n` different prompts from the bank for a playtest game.
 *
 * bank: [{ id, subject, audience, family }, ...] (data/bank.json)
 * previous: ids played last game; they're only used again if the bank runs out
 * random: () => number in [0, 1), so tests can fix the order
 *
 * Prompts are taken in random order, skipping any that would break the mix
 * (more than MAX_PER_SUBJECT of one subject, MAX_SPECIALIST specialist ones,
 * or a second prompt from one family). If that leaves the game short, the
 * skipped ones fill it, family clashes last: a small bank still gives a full
 * game.
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
  const families = new Set();
  let specialists = 0;
  for (const entry of ordered) {
    if (picked.length === n) break;
    const subjectFull = (subjects[entry.subject] ?? 0) >= MAX_PER_SUBJECT;
    const tooHard = entry.audience === "specialist" && specialists >= MAX_SPECIALIST;
    const sameFamily = Boolean(entry.family) && families.has(entry.family);
    if (subjectFull || tooHard || sameFamily) { skipped.push(entry); continue; }
    picked.push(entry);
    subjects[entry.subject] = (subjects[entry.subject] ?? 0) + 1;
    if (entry.audience === "specialist") specialists += 1;
    if (entry.family) families.add(entry.family);
  }
  // Fill any gap: first with prompts that only broke the subject or
  // specialist mix, and only then with a second prompt from a family.
  const clashes = (e) => Boolean(e.family) && families.has(e.family);
  for (const entry of [...skipped.filter((e) => !clashes(e)), ...skipped.filter(clashes)]) {
    if (picked.length === n) break;
    picked.push(entry);
    if (entry.family) families.add(entry.family);
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
