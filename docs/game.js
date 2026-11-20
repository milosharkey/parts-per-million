// Parts Per Million: the playable demo and playtest.
//
// Loads the answer sets the pipeline built, as published by publish_site.py
// (docs/data/), and plays them. In playtest mode each game deals random
// prompts from the bank; in daily mode everyone plays the scheduled day.
// config.json (also written by publish_site.py) says which. Plain JavaScript
// with no framework or build step:
// the page is small enough that a framework would add more to learn than it
// saves. Everything on screen is drawn by the render* functions from one
// `state` object, so to understand the flow, start at startDay().

import { deal, gameDate } from "./deal.js";
import { matchAnswer, normalise, preparePrompt } from "./matching.js";
import {
  TIERS, TIER_ORDER, LANDMARKS, ppmFromPoints, formatPpm, formatPurity, nines, landmarkPassed,
} from "./scoring.js";

// ---- Settings -----------------------------------------------------------------
// The site's settings come from config.json (publish_site.py writes it from
// config.py); these are the defaults if a setting is missing.
const DEFAULTS = {
  mode: "playtest",        // playtest: random prompts from the bank. daily: the scheduled day
  promptsPerGame: 7,
  timeLimit: 25,           // seconds per prompt (design doc)
  resultsUrl: "",          // where finished games are sent; empty: offer a download
  contact: "",             // shown in the footer for questions and bugs
  dayStartsUtcHour: 5,     // the day changes at this hour UTC, for everyone
  local: false,            // served by serve.py: ?date= can preview a scheduled day
};
let SITE = { ...DEFAULTS };

const RULES = {
  // An answer that isn't on the list doesn't use up the prompt: the player can
  // try again while time remains. Set false for Krillion's stricter rule
  // (anything not accepted scores 0).
  retryUnknown: true,
  // Each answer may only be given once per prompt, in case a prompt comes up
  // twice in a day. (Gold can still be an element in one round and a mineral
  // in another.)
  allowRepeats: false,
};

// Before loading finishes, size the column for the planned day.
// Each prompt's coloured band on the reagent label, so a change of prompt is
// obvious at a glance (real reagent labels use colour bands the same way).
// The colours are kept clear of the tier colours. Prompts not listed get a
// plain band with their id.
const PROMPT_STYLE = {
  elements:        { band: "Elements",  colour: "#2F4B7C" },   // navy
  minerals:        { band: "Minerals",  colour: "#5E7F3E" },   // olive
  named_reactions: { band: "Reactions", colour: "#A23B4A" },   // crimson
  lab_equipment:   { band: "Lab equipment", colour: "#23756F" },   // teal
  pigments:        { band: "Pigments & dyes", colour: "#C2451C" }, // vermilion
  enzymes:         { band: "Enzymes",   colour: "#A8437A" },   // raspberry
  aromatics:       { band: "Aromatics", colour: "#8A4F2A" },   // rust
};
// Each prompt's own label and colour (prompt.toml) win; PROMPT_STYLE covers
// files built before prompts had them.
const promptStyle = (id) => {
  const meta = state.prompts[id]?.prompt ?? {};
  const fallback = PROMPT_STYLE[id] ?? { band: id.replaceAll("_", " "), colour: "#1d2124" };
  return { band: meta.label || fallback.band, colour: meta.colour || fallback.colour };
};

const maxPoints = () => 100 * (state.day.length || SITE.promptsPerGame);
const MISS_REASON = { time: "Time's up", pass: "Passed", unknown: "Not on the list" };
const MURKY = [0x4a, 0x3b, 0x2a];   // background at 0 points: unrefined crude
const CLEAR = [0xf3, 0xf8, 0xf9];   // background at a perfect score: glass white

// ---- State -----------------------------------------------------------------
// One object holds everything that changes during a game.
const state = {
  mode: "playtest",  // playtest or daily (see DEFAULTS)
  bank: null,        // data/bank.json, once loaded (playtest mode)
  prompts: {},       // id -> prepared prompt (from matching.preparePrompt)
  day: [],           // the prompt ids being played, in order
  lot: "",           // shown on the label: the day's lot number, or "Playtest"
  gameId: "",        // random id tying a game's result and feedback together
  sent: null,        // how the finished game was saved: null, "sending", "sent", "failed"
  round: 0,          // index into state.day
  rounds: [],        // one entry per finished prompt: { promptId, typed, answer, tier, points, how }
  phase: "loading",  // loading | start | playing | answered | results | error
  timer: null,       // { started, raf } while a prompt is running
  suggestion: null,  // { typed, answerId, how } while a correction waits to be confirmed
  log: null,         // the playtest record: every attempt, accepted or not (see logAttempt)
  shownPpm: ppmFromPoints(0),   // what the ppm readout currently shows (for the roll animation)
};

const $ = (id) => document.getElementById(id);
const totalPoints = () => state.rounds.reduce((sum, r) => sum + r.points, 0);
// Answers already given, per prompt: "minerals:Q897".
const usedKey = (promptId, answerId) => `${promptId}:${answerId}`;
const usedKeys = () => new Set(state.rounds.filter((r) => r.answer).map((r) => usedKey(r.promptId, r.answer.id)));
const escapeHtml = (text) =>
  String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// ---- Loading ------------------------------------------------------------------

async function getJson(path) {
  const response = await fetch(path, { cache: "no-cache" });
  if (!response.ok) throw Object.assign(new Error(`${path}: ${response.status}`), { status: response.status });
  return response.json();
}

/** Load prompt files not loaded yet: [{ id, file }]. */
async function loadPrompts(entries) {
  for (const { id, file } of entries) {
    if (!state.prompts[id] || state.prompts[id].file !== file) {
      state.prompts[id] = { ...preparePrompt(await getJson(file)), file };
    }
  }
}

/** Playtest mode: deal a fresh set of prompts from the bank. */
async function dealGame() {
  state.bank ??= await getJson("data/bank.json");
  if (!state.bank.length) throw new Error("no prompts are ready yet (run Build bank prompts, then Publish site)");
  const ids = deal(state.bank, SITE.promptsPerGame, state.day);
  const byId = Object.fromEntries(state.bank.map((e) => [e.id, e]));
  await loadPrompts(ids.map((id) => ({ id, file: byId[id].file })));
  state.day = ids;
  state.lot = "Playtest";
}

/** Daily mode: today's scheduled day (or, locally, ?date=YYYY-MM-DD). */
async function loadDay(requested) {
  const date = SITE.local && requested ? requested : gameDate(new Date(), SITE.dayStartsUtcHour);
  let day;
  try {
    day = await getJson(`data/days/${date}.json`);
  } catch (err) {
    if (err.status === 404) throw new Error(`there's no puzzle for ${date} yet. Come back soon!`);
    throw err;
  }
  await loadPrompts(day.prompts);
  state.day = day.prompts.map((p) => p.id);
  state.lot = day.lot;
}

async function load() {
  if (location.protocol === "file:") {
    return showError("This page has to be opened through <code>serve.py</code> (the <strong>Play demo</strong> run configuration). Browsers don't let a page opened straight from disk read the answer files.");
  }
  try {
    SITE = { ...DEFAULTS, ...(await getJson("config.json")) };
    const params = new URLSearchParams(location.search);
    state.mode = params.get("mode") === "daily" || params.get("mode") === "playtest" ? params.get("mode") : SITE.mode;
    if (state.mode === "daily") await loadDay(params.get("date"));
    else await dealGame();
  } catch (err) {
    return showError(`Couldn't load the prompts: ${escapeHtml(err.message)}`);
  }
  renderFooter();
  state.phase = "start";
  render();
}

function showError(html) {
  state.phase = "error";
  $("centre").innerHTML = `<div class="card error"><h2>Can't start the game</h2><p>${html}</p></div>`;
}

function renderFooter() {
  const lot = $("lot");
  if (lot) lot.textContent = state.mode === "daily" ? `Lot ${state.lot}` : "Playtest";
  const contact = $("contact");
  if (contact && SITE.contact) {
    contact.innerHTML = `Questions, ideas or bugs: <a href="mailto:${escapeHtml(SITE.contact)}">${escapeHtml(SITE.contact)}</a>`;
  }
}

// ---- Playtest log -----------------------------------------------------------------
// Everything a player types is recorded, not just the answers that counted:
// answers refused as "not on the list" show what's missing from an answer set
// or its aliases, and declined suggestions show where corrections go wrong.
// The record is saved at the end (sendGame) and summarised by
// playtest_report.py (DECISIONS.md, 35).

/** Add one event to the log: what happened, on which prompt, how far in. */
function logAttempt(event, details = {}) {
  if (!state.log) return;
  const ms = state.timer ? Math.round(performance.now() - state.timer.started) : null;
  const answer = details.answer;
  delete details.answer;
  state.log.attempts.push({
    round: state.round + 1, prompt: state.day[state.round], event, ms, ...details,
    ...(answer ? { answer: answer.name, answerId: answer.id, tier: answer.tier } : {}),
  });
}

// ---- Flow ---------------------------------------------------------------------

function startDay() {
  // Ignore repeats: a button press and an Enter key can both arrive at once.
  if (state.phase !== "start") return;
  state.round = 0;
  state.rounds = [];
  state.gameId = Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  state.sent = null;
  state.log = { started: new Date().toISOString(), attempts: [] };
  startPrompt();
}

/** Play again: a playtest deals new prompts; a daily game replays the day. */
async function playAgain() {
  if (state.phase !== "results") return;
  state.phase = "loading";
  $("centre").innerHTML = `<div class="card panel"><p>Mixing a fresh batch…</p></div>`;
  try {
    if (state.mode === "playtest") await dealGame();
  } catch (err) {
    return showError(`Couldn't load the prompts: ${escapeHtml(err.message)}`);
  }
  state.rounds = [];
  state.phase = "start";
  render();
}

function startPrompt() {
  state.phase = "playing";
  render();
  const input = $("answer");
  input.focus();
  state.timer = { started: performance.now(), raf: requestAnimationFrame(tick) };
}

/** Runs every frame while a prompt is live: drains the burette, ends on time-out. */
function tick(now) {
  const left = Math.max(0, SITE.timeLimit - (now - state.timer.started) / 1000);
  const burette = $("burette");
  if (burette) {
    burette.querySelector(".liquid").style.width = `${(left / SITE.timeLimit) * 100}%`;
    burette.classList.toggle("low", left <= 5);
    $("seconds").textContent = `${Math.ceil(left)} s`;
  }
  if (left <= 0) {
    logAttempt("time_up", { typed: $("answer")?.value ?? "" });
    return finishPrompt({ typed: "", answer: null, how: "time" });
  }
  state.timer.raf = requestAnimationFrame(tick);
}

function stopTimer() {
  if (state.timer) cancelAnimationFrame(state.timer.raf);
  state.timer = null;
}

/**
 * The player pressed Enter: work out what they meant and whether it counts.
 *
 * Only the answer's own name is accepted straight away. A typo or another
 * name for an answer ("fool's gold" -> Pyrite, "monazite" -> Monazite-(Ce))
 * is shown as a suggestion first, and the player presses Enter again to
 * accept it or edits it. That way nobody gets landed with a commoner answer
 * than they meant (DECISIONS.md, decision 27).
 */
function submit(event) {
  event.preventDefault();
  if (state.phase !== "playing") return;
  const input = $("answer");
  const typed = input.value;
  const prompt = state.prompts[state.day[state.round]];

  // Enter on an unchanged suggestion accepts it.
  const pending = state.suggestion && prompt.byId[state.suggestion.answerId];
  if (pending && normalise(typed) === normalise(pending.name)) {
    logAttempt("confirmed", { typed: state.suggestion.typed, answer: pending });
    return finishPrompt({ typed: state.suggestion.typed, answer: pending, how: state.suggestion.how });
  }

  const match = matchAnswer(prompt, typed);
  if (match.status === "empty") return;
  if (match.status === "none") {
    logAttempt("not_on_list", { typed });
    if (RULES.retryUnknown) return rejectTry(`“${escapeHtml(typed.trim())}” isn't on the list for “${escapeHtml(prompt.prompt.text)}”. Keep trying.`);
    return finishPrompt({ typed, answer: null, how: "unknown" });
  }
  if (!RULES.allowRepeats && usedKeys().has(usedKey(state.day[state.round], match.answer.id))) {
    logAttempt("repeat", { typed, answer: match.answer });
    return rejectTry(`You've already used ${escapeHtml(match.answer.name)} today. Try another.`);
  }
  if (match.status === "alias" || match.status === "typo") {
    logAttempt("suggested", { typed, how: match.status, answer: match.answer });
    return suggest(typed, match);
  }
  logAttempt("accepted", { typed, answer: match.answer });
  finishPrompt({ typed, answer: match.answer, how: "exact" });
}

/**
 * Show what the typed text resolves to and put that name in the box. The
 * tier stays hidden until it's accepted, so a near miss can't be used to
 * peek at how rare something is. The timer keeps running.
 */
function suggest(typed, match) {
  state.suggestion = { typed: typed.trim(), answerId: match.answer.id, how: match.status };
  const input = $("answer");
  input.value = match.answer.name;
  input.setSelectionRange(input.value.length, input.value.length);
  const feedback = $("feedback");
  feedback.className = "feedback suggestion";
  feedback.innerHTML = `${correctionHtml(state.suggestion.typed, match.answer.name)}
    <span class="confirm">Enter again to confirm · edit to change · Esc to undo</span>`;
}

/** Put the player's own text back and drop the suggestion (Esc, or editing). */
function clearSuggestion({ restore = false } = {}) {
  if (!state.suggestion) return;
  if (restore) {
    logAttempt("suggestion_undone", { typed: state.suggestion.typed });
    $("answer").value = state.suggestion.typed;
  }
  state.suggestion = null;
  const feedback = $("feedback");
  feedback.className = "feedback";
  feedback.innerHTML = "";
}

const correctionHtml = (typed, name) =>
  `<span class="was">“${escapeHtml(typed)}”</span> <span class="arrow">→</span> <span class="now">“${escapeHtml(name)}”</span>`;

function rejectTry(message) {
  const input = $("answer");
  input.classList.remove("shake");
  void input.offsetWidth;            // restart the shake animation
  input.classList.add("shake");
  input.select();
  const feedback = $("feedback");
  feedback.className = "feedback warn";
  feedback.innerHTML = message;
}

/** Record the outcome of the current prompt and show its result. */
function finishPrompt({ typed, answer, how }) {
  stopTimer();
  state.suggestion = null;   // a suggestion still waiting when time runs out isn't accepted
  const tier = answer ? answer.tier : "miss";
  state.rounds.push({
    promptId: state.day[state.round], typed, answer, tier, points: TIERS[tier].points, how,
  });
  state.phase = "answered";
  render();
  animatePpm();
  $("next").focus();
}

function next() {
  state.round += 1;
  if (state.round < state.day.length) startPrompt();
  else {
    state.phase = "results";
    render();
    sendGame("result");   // the answers go off as soon as the game ends
  }
}

function passPrompt() {
  if (state.phase !== "playing") return;
  logAttempt("pass", { typed: $("answer")?.value ?? "" });
  finishPrompt({ typed: "", answer: null, how: "pass" });
}

/**
 * The playtest record for this game: settings, each round's outcome with the
 * model's prediction for it, every attempt from the log, and the player's
 * feedback once they send it. "kind" is "result" (sent automatically when
 * the game ends) or "feedback" (the same record plus the form, sent when
 * they press the button); playtest_report.py keeps the latest for each game.
 */
function playtestRecord(kind) {
  const ids = [...new Set(state.day)];
  const player = $("player-name")?.value.trim() || "anonymous";
  return {
    format: "ppm-playtest-2",
    kind, gameId: state.gameId, mode: state.mode, lot: state.lot,
    player,
    device: matchMedia("(pointer: coarse)").matches ? "touch" : "desktop",
    started: state.log?.started, finished: new Date().toISOString(),
    day: state.day,
    builds: Object.fromEntries(ids.map((id) => [id, state.prompts[id].generated])),
    settings: { timeLimit: SITE.timeLimit, retryUnknown: RULES.retryUnknown },
    points: totalPoints(),
    rounds: state.rounds.map((r, i) => ({
      round: i + 1, prompt: r.promptId, typed: r.typed, how: r.how,
      answer: r.answer?.name ?? null, answerId: r.answer?.id ?? null,
      tier: r.tier, points: r.points, predictedShare: r.answer?.share ?? null,
    })),
    attempts: state.log?.attempts ?? [],
    feedback: kind === "feedback" ? readFeedback() : null,
  };
}

function readFeedback() {
  const value = (id) => $(id)?.value.trim() ?? "";
  return {
    fun: value("fb-fun"), annoying: value("fb-annoying"),
    playDaily: document.querySelector("input[name=fb-daily]:checked")?.value ?? "",
    broke: value("fb-broke"), comments: value("fb-comments"),
  };
}

/**
 * Send a record to config.resultsUrl. Locally that's serve.py, which saves
 * it in playtests/. On the published site it's the Google Apps Script
 * (playtest/Code.gs), which adds it to the Sheet. That's a different site, so
 * the request is sent as plain text with no-cors: the browser sends it
 * without a preflight check, but can't show us the reply.
 */
async function postRecord(record) {
  const url = SITE.resultsUrl;
  if (url.startsWith("/")) {
    const response = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(record),
    });
    if (!response.ok) throw new Error(String(response.status));
    return (await response.json()).saved;
  }
  await fetch(url, {
    method: "POST", mode: "no-cors", headers: { "Content-Type": "text/plain;charset=utf-8" },
    body: JSON.stringify(record),
  });
  return null;
}

function recordFileName(kind) {
  const player = $("player-name")?.value.trim() || "anonymous";
  const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
  const slug = player.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "player";
  return `ppm-playtest-${slug}-${stamp}-${kind}.json`;
}

/** Save the finished game: automatically at the end, again with feedback. */
async function sendGame(kind) {
  const record = { ...playtestRecord(kind), file: recordFileName(kind) };
  const status = $("playtest-status");
  const say = (text) => { if (status) status.textContent = text; };
  if (!SITE.resultsUrl) {
    if (kind === "result") return;   // nowhere to send: the button offers a download
    const blob = new Blob([JSON.stringify(record, null, 1)], { type: "application/json" });
    const link = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: record.file });
    link.click();
    URL.revokeObjectURL(link.href);
    return say(`Downloaded ${record.file}. Please send it to whoever's running the playtest.`);
  }
  // The button goes off the moment it's pressed, not when the send finishes:
  // the Sheet takes a second or two to answer, and every extra click in that
  // time used to add another copy of the same feedback.
  const button = kind === "feedback" ? $("send-feedback") : null;
  if (button) { button.disabled = true; button.textContent = "Sending…"; }
  state.sent = "sending";
  try {
    const saved = await postRecord(record);
    state.sent = "sent";
    if (kind === "feedback") {
      button.textContent = "Sent";
      say(saved ? `Saved as ${saved}. Thanks!` : "Sent. Thank you, that really helps! Change anything above to send an update.");
      reopenFeedbackOnEdit();
    } else {
      say(saved ? `Your answers were saved as ${saved}.` : "Your answers have been sent. Thanks for playing!");
    }
  } catch {
    state.sent = "failed";
    if (button) { button.disabled = false; button.textContent = "Send feedback"; }
    say("Couldn't send your answers (are you offline?). Press the button to try again.");
  }
}

/**
 * After feedback is sent the button stays off until the player changes
 * something in the form; then it offers to send an update. The report keeps
 * the latest feedback for each game, so an update replaces the first.
 */
function reopenFeedbackOnEdit() {
  document.querySelector(".feedback-form")?.addEventListener("input", () => {
    const button = $("send-feedback");
    if (button) { button.disabled = false; button.textContent = "Send update"; }
  }, { once: true });
}

// Keyboard: Enter moves on after a result. Esc undoes a suggestion if one is
// showing, and otherwise passes on a live prompt.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.suggestion) clearSuggestion({ restore: true });
  else if (event.key === "Escape") passPrompt();
  if (event.key === "Enter" && state.phase === "start" && document.activeElement?.id !== "start") {
    event.preventDefault(); startDay();
  }
  if (event.key === "Enter" && state.phase === "answered" && document.activeElement?.id !== "next") {
    event.preventDefault(); next();
  }
});

// ---- Rendering ------------------------------------------------------------------

function render() {
  const centre = $("centre");
  if (state.phase === "start") centre.innerHTML = startHtml();
  if (state.phase === "playing" || state.phase === "answered") centre.innerHTML = playHtml();
  if (state.phase === "results") centre.innerHTML = resultsHtml();
  wireUp();
  renderSoFar();
  renderColumn();
  renderClarity();
  if (state.phase !== "answered") renderReadout(ppmFromPoints(totalPoints()));
}

/** Attach event handlers to whatever the current screen contains. */
function wireUp() {
  $("start")?.addEventListener("click", startDay);
  $("answer-form")?.addEventListener("submit", submit);
  $("answer")?.addEventListener("input", (e) => {
    const pending = state.suggestion && state.prompts[state.day[state.round]].byId[state.suggestion.answerId];
    if (pending && normalise(e.target.value) !== normalise(pending.name)) clearSuggestion();
  });
  $("pass")?.addEventListener("click", passPrompt);
  $("next")?.addEventListener("click", next);
  $("again")?.addEventListener("click", playAgain);
  $("copy")?.addEventListener("click", copyShare);
  $("send-feedback")?.addEventListener("click", () => sendGame("feedback"));
  document.querySelectorAll("input.search[data-prompt]").forEach((box) => box.addEventListener("input", filterLongTail));
}

function startHtml() {
  const texts = [...new Set(state.day)].map((id) =>
    `<span class="mini-band" style="--accent:${promptStyle(id).colour}">${escapeHtml(state.prompts[id].prompt.text)}</span>`);
  const playtest = state.mode === "playtest";
  const notice = playtest ? `
      <p class="notice">This is a playtest. When you finish, your answers (including ones that
        weren't accepted) are sent so the rarity tiers can be checked against real play. Nothing
        else about you is collected; you can add a nickname and feedback at the end.</p>` : "";
  const key = ["tap_water", "lab_grade", "reagent_grade", "analytical_grade", "single_crystal", "fools_gold"]
    .map((t) => chipHtml(t, `${TIERS[t].label} · ${TIERS[t].points}`)).join("");
  return `
    <div class="card panel">
      <h1>${playtest ? "Playtest" : `Lot ${escapeHtml(state.lot)}`}</h1>
      <p>Your sample starts at <strong>1,000,000 ppm</strong> of impurity. Every answer removes some:
         the rarer the answer, the more it removes. Every 100 points is one more nine of purity.</p>
      <ul class="rules">
        <li>${state.day.length} prompts, ${SITE.timeLimit} seconds each. Type one answer and press <strong>Enter</strong>.</li>
        <li>${playtest ? "This game's prompts, picked at random" : "Today's prompts"}: ${texts.join(" ")}. The label's colour shows which one you're on.${
          state.day.length > new Set(state.day).size ? " Some come up more than once, and each answer counts once per prompt." : ""}</li>
        <li>Answers that aren't on the list don't use up your go. <strong>Esc</strong> passes.</li>
        <li>A near miss or another name for an answer is shown as a suggestion. Press <strong>Enter</strong> again to accept it, or edit it.</li>
      </ul>
      <div class="tier-key">${key}</div>${notice}
      <button id="start">Start (Enter)</button>
    </div>`;
}

function playHtml() {
  const prompt = state.prompts[state.day[state.round]];
  const done = state.phase === "answered" ? state.rounds[state.rounds.length - 1] : null;
  const grade = done ? `<strong>${TIERS[done.tier].label}</strong>` : "awaiting answer";
  const isLast = state.round === state.day.length - 1;

  let outcome = "";
  if (done) {
    const why = {
      exact: "",
      typo: `<span class="typed">${correctionHtml(done.typed, done.answer?.name ?? "")}</span>`,
      alias: `<span class="typed">${correctionHtml(done.typed, done.answer?.name ?? "")}</span>`,
      time: "Time's up.",
      pass: "Passed.",
      unknown: `“${escapeHtml(done.typed.trim())}” isn't on the list.`,
    }[done.how];
    outcome = `
      <div class="result-line">
        ${chipHtml(done.tier, done.answer ? escapeHtml(done.answer.name) : TIERS.miss.label, true)}
        <span class="gain">+${done.points}</span>
        <span>${done.answer ? TIERS[done.tier].label : ""}${done.tier === "fools_gold" ? ": everyone thinks they're the first to say that" : ""}${done.tier === "single_crystal" ? ": the gem of the day" : ""}</span>
      </div>
      ${done.answer?.blurb ? `<p class="blurb">${escapeHtml(done.answer.blurb)}</p>` : ""}
      <p class="feedback">${why}</p>
      <div class="next-row"><button id="next">${isLast ? "See your sample" : "Next prompt"} (Enter)</button></div>`;
  }

  // Flag a change of prompt from the round before (only as it starts, so the
  // animation doesn't replay when the result is shown).
  const id = state.day[state.round];
  const style = promptStyle(id);
  const changed = state.phase === "playing" && state.round > 0 && id !== state.day[state.round - 1];

  return `<div class="card play">
    <div class="label-card${changed ? " changed" : ""}" style="--accent:${style.colour}">
      <div class="label-band"><span>${escapeHtml(style.band)}</span>${changed ? `<span class="new-tag">New prompt</span>` : ""}</div>
      <div class="label-top"><span>Prompt ${state.round + 1} of ${state.day.length}</span><span>${state.mode === "daily" ? `Lot ${escapeHtml(state.lot)}` : "Playtest"}</span></div>
      <div class="prompt-text">${escapeHtml(prompt.prompt.text)}</div>
      <hr class="label-rule">
      <div class="grade-line">Grade: ${grade}</div>
    </div>
    ${done ? outcome : `
      <div class="burette-label"><span>Burette</span><span id="seconds">${SITE.timeLimit} s</span></div>
      <div class="burette" id="burette"><div class="liquid"></div></div>
      <form class="answer-form" id="answer-form" autocomplete="off">
        <input class="answer-input" id="answer" type="text" aria-label="Your answer"
               autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" enterkeyhint="go">
        <button type="button" class="secondary" id="pass">Pass</button>
      </form>
      <p class="hint">Enter to submit · Esc to pass · near misses are suggested for you to confirm</p>
      <p class="feedback" id="feedback"></p>`}
    </div>`;
}

/** The end screen: score, share text, your answers, then each prompt's full list. */
function resultsHtml() {
  const points = totalPoints();
  const passed = landmarkPassed(points);
  const firstLandmark = LANDMARKS[0];
  const yours = usedKeys();

  const roundItems = state.rounds.map((r, i) => `
    <li><span class="n">${i + 1}.</span>
      ${chipHtml(r.tier, r.answer ? escapeHtml(r.answer.name) : TIERS.miss.label)}
      <span>+${r.points}</span>
      <span class="typed"><span class="dot" style="--accent:${promptStyle(r.promptId).colour}"></span>${escapeHtml(state.prompts[r.promptId].prompt.text)}${r.how === "typo" || r.how === "alias" ? ` · typed “${escapeHtml(r.typed.trim())}”` : ""}</span>
    </li>`).join("");

  const callout = (answer, tier, blurb) => answer ? `
    <div class="callout">${chipHtml(tier, TIERS[tier].label)}
      <div class="name">${escapeHtml(answer.name)}</div>
      <div class="typed">${blurb}</div></div>` : "";

  const fullList = (id) => {
    const prompt = state.prompts[id];
    const sections = TIER_ORDER.map((tier) => {
      const answers = prompt.answers.filter((a) => a.tier === tier);
      const names = answers.map((a) =>
        `<span class="${yours.has(usedKey(id, a.id)) ? "yours" : ""}" style="--tier:${TIERS[tier].colour}">${escapeHtml(a.name)}</span>`).join("");
      const heading = `<h3>${chipHtml(tier, TIERS[tier].label)}<span class="count">${answers.length.toLocaleString("en-GB")} answers · ${TIERS[tier].points} points</span></h3>`;
      // Short tiers are shown in full. A long tail is searchable, or opens in full.
      if (answers.length <= 200) return `<div class="tier-section">${heading}<div class="names">${names}</div></div>`;
      return `<div class="tier-section">${heading}
        <input class="search" type="search" data-prompt="${id}" data-tier="${tier}" placeholder="Search the long tail…" aria-label="Search ${TIERS[tier].label} answers">
        <div class="names" id="search-${id}-${tier}"></div>
        <details><summary>Show all ${answers.length.toLocaleString("en-GB")}</summary><div class="names">${names}</div></details></div>`;
    }).join("");
    return `
      <h2>${escapeHtml(prompt.prompt.text)}: the full list</h2>
      <div class="callouts">
        ${callout(prompt.gem, "single_crystal", escapeHtml(prompt.gem?.blurb ?? "The gem: rare, but worth knowing."))}
        ${callout(prompt.foolsGold, "fools_gold", escapeHtml(prompt.foolsGold?.blurb ?? "Famously obscure. Everyone thinks they're the first to say it."))}
      </div>
      ${sections}`;
  };

  return `
    <div class="card panel">
      <h1>Your sample</h1>
      <div class="final-ppm">${formatPpm(ppmFromPoints(points))}</div>
      <div class="final-sub">${formatPurity(points)} pure · ${nines(points).toFixed(2)}N · ${points} points</div>
      <p class="landmark">${passed ? `${passed.points === points ? "As pure as" : "Purer than"} ${escapeHtml(passed.label)}.` : `Not yet cleaner than ${escapeHtml(firstLandmark.label)} (${firstLandmark.points} points).`}</p>

      <h2>Share</h2>
      <div class="share"><textarea id="share-text" readonly>${escapeHtml(shareText())}</textarea>
        <button id="copy" class="secondary">Copy</button></div>

      ${feedbackHtml()}

      <h2>Your answers</h2>
      <ol class="round-list">${roundItems}</ol>

      ${[...new Set(state.day)].map(fullList).join("")}

      <div class="next-row"><button id="again">${state.mode === "playtest" ? "Play again with new prompts" : "Play again"}</button></div>
    </div>`;
}

/** The feedback form at the end of a game. */
function feedbackHtml() {
  const options = [...new Set(state.day)].map((id) =>
    `<option value="${id}">${escapeHtml(state.prompts[id].prompt.text)}</option>`).join("");
  const select = (id, label) => `
    <label class="field"><span>${label}</span>
      <select id="${id}"><option value="">Choose one (optional)</option>${options}</select></label>`;
  const daily = ["Yes", "Maybe", "No"].map((v) =>
    `<label class="choice"><input type="radio" name="fb-daily" value="${v.toLowerCase()}"><span>${v}</span></label>`).join("");
  const button = SITE.resultsUrl ? "Send feedback" : "Download my answers and feedback";
  return `
      <h2>How was it?</h2>
      <div class="feedback-form">
        <label class="field"><span>Nickname (optional)</span>
          <input id="player-name" class="player-name" type="text" autocomplete="off" maxlength="40"
                 placeholder="So we can tell your games apart"></label>
        <div class="field-row">${select("fb-fun", "Most fun prompt")}${select("fb-annoying", "Most annoying prompt")}</div>
        <fieldset class="field"><legend>Would you play this every day?</legend><div class="choices">${daily}</div></fieldset>
        <label class="field"><span>Did anything break or behave oddly?</span>
          <textarea id="fb-broke" rows="3" placeholder="A right answer that wasn't accepted, a strange correction, something on screen…"></textarea></label>
        <label class="field"><span>Anything else?</span>
          <textarea id="fb-comments" rows="3" placeholder="Ideas for prompts, what you'd change…"></textarea></label>
        <div class="next-row"><button id="send-feedback" class="secondary">${button}</button></div>
        <p class="feedback" id="playtest-status">${SITE.resultsUrl ? "" : "Results aren't being collected online yet, so this downloads a file to send on."}</p>
      </div>`;
}

function shareText() {
  const points = totalPoints();
  const squares = state.rounds.map((r) => TIERS[r.tier].share).join("");
  const lot = state.mode === "daily" ? `Lot ${state.lot}` : "Playtest";
  return `Parts Per Million · ${lot}\n${formatPpm(ppmFromPoints(points))} · ${formatPurity(points)} pure\n${squares}`;
}

async function copyShare() {
  const text = $("share-text");
  try {
    await navigator.clipboard.writeText(text.value);
    $("copy").textContent = "Copied";
  } catch {
    text.select();               // clipboard blocked: leave it selected to copy by hand
  }
}

/** Search box above a long tier list: shows up to 60 matching names. */
function filterLongTail(event) {
  const { prompt: id, tier } = event.target.dataset;
  const query = event.target.value.trim().toLowerCase();
  const hits = query.length < 2 ? [] :
    state.prompts[id].answers.filter((a) => a.tier === tier && a.name.toLowerCase().includes(query)).slice(0, 60);
  $(`search-${id}-${tier}`).innerHTML = hits.map((a) =>
    `<span style="--tier:${TIERS[tier].colour}">${escapeHtml(a.name)}</span>`).join("") ||
    (query.length >= 2 ? `<span class="typed">No matches</span>` : "");
}

/** The "Answers so far" list in the right-hand panel. */
function renderSoFar() {
  $("so-far").innerHTML = state.rounds.length ? state.rounds.map((r, i) => `
    <li><span class="n">${i + 1}</span>
      <span>${r.answer ? escapeHtml(r.answer.name) : "No answer"}<span class="sub">${r.answer ? TIERS[r.tier].label : MISS_REASON[r.how]}</span></span>
      <span class="pts">+${r.points}</span></li>`).join("")
    : `<li class="empty">Nothing yet.</li>`;
}

/** The ppm readout, with purity and nines underneath. */
function renderReadout(ppm) {
  const points = 100 * (6 - Math.log10(ppm));
  $("ppm").textContent = formatPpm(ppm);
  $("purity").textContent = `${formatPurity(points)} pure · ${nines(points).toFixed(2)}N`;
  state.shownPpm = ppm;
}

/** Roll the readout down to the new score (on a log scale, so it slows near the end). */
function animatePpm() {
  const from = Math.log10(state.shownPpm);
  const to = Math.log10(ppmFromPoints(totalPoints()));
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce || from === to) return renderReadout(10 ** to);
  const started = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - started) / 700);
    const eased = 1 - (1 - t) ** 3;
    renderReadout(10 ** (from + (to - from) * eased));
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/** The page background clears from murky to glass white as points rise. */
function renderClarity() {
  const f = Math.min(1, totalPoints() / maxPoints());
  const mix = MURKY.map((c, i) => Math.round(c + (CLEAR[i] - c) * f));
  document.body.style.setProperty("--clarity-bg", `rgb(${mix.join(",")})`);
}

/**
 * Draw the distillation column. The scale runs from 0 points (1,000,000 ppm)
 * at the bottom to the day's maximum at the top, so each gridline is ten
 * times fewer ppm. Each answer adds a band as tall as its points, in its tier
 * colour, so the liquid's height is the score. Drawn at the SVG's real pixel
 * size, so it works as a tall panel on desktop and a slim gauge on phones.
 */
function renderColumn() {
  const svg = $("column");
  const width = svg.clientWidth || 170;
  const height = svg.clientHeight || 600;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const showLabels = width > 120;

  const tubeW = Math.min(40, width - 16);
  const tubeX = showLabels ? 12 : (width - tubeW) / 2;
  const top = 12, bottom = height - 12;
  const y = (points) => bottom - (points / maxPoints()) * (bottom - top);

  let html = `<defs><linearGradient id="spectrum" x1="0" x2="1" y1="0" y2="1">
      <stop offset="0" stop-color="#e35d6a"/><stop offset=".3" stop-color="#f0b44c"/><stop offset=".5" stop-color="#6cc070"/>
      <stop offset=".7" stop-color="#4fb3d9"/><stop offset="1" stop-color="#8a6be0"/></linearGradient></defs>
    <rect class="col-tube" x="${tubeX}" y="${top - 6}" width="${tubeW}" height="${bottom - top + 12}" rx="${tubeW / 2}"/>`;

  // Gridlines every 100 points (one more nine), labelled with their ppm.
  for (let p = 0; p <= maxPoints(); p += 100) {
    html += `<line class="col-grid" x1="${tubeX + 4}" x2="${tubeX + tubeW - 4}" y1="${y(p)}" y2="${y(p)}"/>`;
    if (showLabels) {
      html += `<line class="col-tick" x1="${tubeX + tubeW}" x2="${tubeX + tubeW + 6}" y1="${y(p)}" y2="${y(p)}"/>
               <text class="col-label" x="${tubeX + tubeW + 10}" y="${y(p) + 3.5}">${formatPpm(ppmFromPoints(p))}</text>`;
    }
  }
  // Real-world landmarks inside the day's range. One that falls on a
  // gridline goes underneath that gridline's ppm label.
  if (showLabels) {
    for (const l of LANDMARKS.filter((l) => l.points <= maxPoints())) {
      const ly = l.points % 100 === 0 ? y(l.points) + 15 : y(l.points) + 3.5;
      html += `<text class="col-landmark" x="${tubeX + tubeW + 10}" y="${ly}">${escapeHtml(l.short)}</text>`;
    }
  }

  // Bands, bottom up. The newest one animates in.
  let base = 0;
  state.rounds.forEach((r, i) => {
    if (!r.points) return;
    const isNew = i === state.rounds.length - 1 && state.phase === "answered";
    const crystal = r.tier === "single_crystal";
    html += `<rect class="band${isNew ? " new" : ""}" x="${tubeX + 4}" y="${y(base + r.points)}" width="${tubeW - 8}"
                   height="${y(base) - y(base + r.points)}" rx="3" fill="${crystal ? "url(#spectrum)" : TIERS[r.tier].colour}"
                   fill-opacity="0.85" stroke="${TIERS[r.tier].colour}" stroke-width="1"/>`;
    base += r.points;
  });
  if (base > 0) html += `<line class="col-level" x1="${tubeX - 3}" x2="${tubeX + tubeW + 3}" y1="${y(base)}" y2="${y(base)}"/>`;
  svg.innerHTML = html;
}

// Redraw the column when the window size changes.
new ResizeObserver(() => renderColumn()).observe($("column"));

// ---- Tier chips and icons ------------------------------------------------------
// Each tier has a shape as well as a colour, so colour is never the only cue
// (design doc, "Tier icons").

const ICONS = {
  drop: `<path d="M8 1.5C8 1.5 3 7.4 3 10.3a5 5 0 0 0 10 0C13 7.4 8 1.5 8 1.5Z"/>`,
  beaker: `<path d="M3.5 2h9M5 2v10.5A1.5 1.5 0 0 0 6.5 14h3a1.5 1.5 0 0 0 1.5-1.5V2M5 8.5h6"/>`,
  flask: `<path d="M6 1.5h4M7 1.5v4.5L3 13a1 1 0 0 0 .9 1.5h8.2A1 1 0 0 0 13 13L9 6V1.5M4.8 10.5h6.4"/>`,
  volumetric: `<path d="M6.8 1.5h2.4M7.2 1.5v6.2a3.6 3.6 0 1 0 1.6 0V1.5M6.6 4.5h2.8"/>`,
  crystal: `<path d="M4 6l2-3h4l2 3-4 8z M4 6h8 M6 3l2 3 2-3 M8 6v8"/>`,
  cube: `<path d="M8 1.8l5.5 3v6.4L8 14.2l-5.5-3V4.8z M2.5 4.8 8 7.9l5.5-3.1 M8 7.9v6.3"/>`,
  ring: `<circle cx="8" cy="8" r="5.5"/>`,
};

function chipHtml(tier, text, big = false) {
  const t = TIERS[tier];
  const icon = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true">${ICONS[t.icon]}</svg>`;
  return `<span class="chip ${tier}${big ? " big" : ""}" style="--tier:${t.colour}">${icon}${text}</span>`;
}

load();
