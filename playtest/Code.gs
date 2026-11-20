// Parts Per Million: playtest results into a Google Sheet.
//
// The published game sends each finished game here (and again with the
// player's feedback, if they fill it in). This script adds one row per send
// to the "Games" tab and, for the first send, one row per attempt to the
// "Attempts" tab. playtest_report.py reads the Games tab, downloaded as CSV.
//
// Setting it up (README.md, "Hosting the playtest", has the same steps):
//   1. Make a new Google Sheet, for example "PPM playtest results".
//   2. Extensions > Apps Script. Replace everything in Code.gs with this file
//      and save (the disk icon).
//   3. Choose "setup" in the function menu and press Run. Google asks you to
//      allow the script to edit your spreadsheets: allow it. (It may say the
//      app isn't verified: Advanced > Go to project, since it's your own.)
//   4. Deploy > New deployment > the cog > Web app.
//      Execute as: Me.  Who has access: Anyone.  Deploy.
//   5. Copy the Web app URL (it ends in /exec) into SITE_RESULTS_URL in
//      config.py, then run Publish site and push.
//
// Anyone with the link can send rows (that's how players' games get here),
// so treat the sheet as untrusted input: text that would start a formula is
// stored as plain text, and every field is cut to a sensible length.

const GAMES = "Games";
const ATTEMPTS = "Attempts";
const GAME_HEADERS = ["received", "game_id", "kind", "player", "mode", "device", "prompts", "points",
                      "fun", "annoying", "play_daily", "broke", "comments", "record"];
const ATTEMPT_HEADERS = ["received", "game_id", "player", "round", "prompt", "event", "typed",
                         "answer", "tier", "ms"];
const MAX_CELL = 49000;   // Sheets allows 50,000 characters in a cell

function setup() {
  sheet_(GAMES, GAME_HEADERS);
  sheet_(ATTEMPTS, ATTEMPT_HEADERS);
}

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const record = JSON.parse(e.postData.contents);
    if (record.format !== "ppm-playtest-2") return text_("ignored: not a playtest record");
    const received = new Date();
    const fb = record.feedback || {};
    const json = JSON.stringify(record);
    sheet_(GAMES, GAME_HEADERS).appendRow([
      received, clip_(record.gameId), clip_(record.kind), clip_(record.player, 40), clip_(record.mode),
      clip_(record.device), clip_((record.day || []).join(" "), 500), Number(record.points) || 0,
      clip_(fb.fun), clip_(fb.annoying), clip_(fb.playDaily), clip_(fb.broke, 2000), clip_(fb.comments, 2000),
      json.length <= MAX_CELL ? json : "(too long to store: see the Attempts tab)",
    ]);
    if (record.kind === "result") {
      const rows = (record.attempts || []).slice(0, 1000).map((a) => [
        received, clip_(record.gameId), clip_(record.player, 40), Number(a.round) || 0, clip_(a.prompt),
        clip_(a.event), clip_(a.typed), clip_(a.answer), clip_(a.tier), Number(a.ms) || 0,
      ]);
      if (rows.length) {
        const sh = sheet_(ATTEMPTS, ATTEMPT_HEADERS);
        sh.getRange(sh.getLastRow() + 1, 1, rows.length, ATTEMPT_HEADERS.length).setValues(rows);
      }
    }
    return text_("ok");
  } catch (err) {
    return text_("error: " + err);
  } finally {
    lock.releaseLock();
  }
}

function doGet() {
  return text_("Parts Per Million playtest collector. Games are sent here with POST.");
}

function sheet_(name, headers) {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  const sh = book.getSheetByName(name) || book.insertSheet(name);
  if (sh.getLastRow() === 0) {
    sh.appendRow(headers);
    sh.setFrozenRows(1);
  }
  return sh;
}

// Plain text, cut to length, never a formula: "=..." typed by a player
// would otherwise run as one.
function clip_(value, max) {
  let s = String(value == null ? "" : value).slice(0, max || 200);
  if (/^[=+\-@]/.test(s)) s = "'" + s;
  return s;
}

function text_(s) {
  return ContentService.createTextOutput(s).setMimeType(ContentService.MimeType.TEXT);
}
