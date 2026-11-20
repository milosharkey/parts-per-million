// Tiers, points and the parts-per-million score.
//
// The ppm maths mirrors scoring.py (ppm_from_points, purity_percent,
// format_ppm); tests/shared_cases.json checks the two agree. Colours come
// from the design doc: every tier colour is a real chemical.

export const TIERS = {
  tap_water:        { label: "Tap water",        points: 10,  colour: "#A7B0B5", icon: "drop",    share: "⬜" },
  lab_grade:        { label: "Lab grade",        points: 30,  colour: "#C7832B", icon: "beaker",  share: "🟧" },
  reagent_grade:    { label: "Reagent grade",    points: 60,  colour: "#2A86C8", icon: "flask",   share: "🟦" },
  analytical_grade: { label: "Analytical grade", points: 85,  colour: "#7D2A86", icon: "volumetric", share: "🟪" },
  single_crystal:   { label: "Single crystal",   points: 100, colour: "#FFFFFF", icon: "crystal", share: "💎" },
  fools_gold:       { label: "Fool's gold",      points: 15,  colour: "#B5953A", icon: "cube",    share: "🟨" },
  miss:             { label: "No answer",        points: 0,   colour: "#2B2B2B", icon: "ring",    share: "⬛" },
};

/** Order the reveal screen lists tiers in, most common first. */
export const TIER_ORDER = ["tap_water", "lab_grade", "reagent_grade", "analytical_grade"];

/** Impurity left in the sample: 1,000,000 ppm at 0 points, 0.1 ppm at 700. */
export const ppmFromPoints = (points) => 10 ** (6 - points / 100);

/** 1,000,000 ppm is the whole sample, so 1% = 10,000 ppm. */
export const purityPercent = (points) => 100 - ppmFromPoints(points) / 10_000;

/** "Nines" of purity: every 100 points is one more nine. */
export const nines = (points) => points / 100;

const groupThousands = (n) => Math.round(n).toLocaleString("en-GB");
// Like Python's %g: 1.58 stays "1.58", 5.0 becomes "5".
const plainNumber = (n) => String(Number(n));

/** Three significant figures, switching to ppb below 1 ppm (same as scoring.py). */
export function formatPpm(ppm) {
  if (ppm >= 1) {
    const rounded = Number(ppm.toPrecision(3));
    return rounded >= 100 ? `${groupThousands(rounded)} ppm` : `${plainNumber(rounded)} ppm`;
  }
  const ppb = Number((ppm * 1000).toPrecision(3));
  return ppb >= 100 ? `${groupThousands(ppb)} ppb` : `${plainNumber(ppb)} ppb`;
}

/**
 * Purity for display, with just enough decimals to show where the impurity
 * starts plus one more digit: 96.5%, 99.972%, 99.99999%.
 */
export function formatPurity(points) {
  const impurity = ppmFromPoints(points) / 10_000;               // in %
  const firstDigit = impurity >= 1 ? 0 : Math.ceil(-Math.log10(impurity) - 1e-9);
  const decimals = Math.max(1, firstDigit + 1);
  const text = purityPercent(points).toFixed(decimals).replace(/0+$/, "").replace(/\.$/, ".0");
  return `${text}%`;
}

/**
 * Real-world purity landmarks (design doc, scoring). The end screen names
 * the cleanest one the player passed, and the column marks them.
 */
export const LANDMARKS = [
  { points: 170, label: "metallurgical-grade silicon", short: "Metallurgical Si" },
  { points: 225, label: "Ivory soap's \"99 44/100% pure\"", short: "Ivory soap" },
  { points: 300, label: "24-carat gold", short: "24-carat gold" },
  { points: 400, label: "four-nines gold bullion", short: "Gold bullion" },
  { points: 600, label: "solar-grade silicon", short: "Solar silicon" },
  { points: 700, label: "a perfect day", short: "Perfect day" },
];

/** The cleanest landmark at or below this score, or null. */
export function landmarkPassed(points) {
  return [...LANDMARKS].reverse().find((l) => points >= l.points) ?? null;
}
