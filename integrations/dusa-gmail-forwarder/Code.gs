/**
 * DSEC — DUSA weekly-report forwarder (Google Apps Script)
 * ------------------------------------------------------------------
 * Every week DUSA emails the club two spreadsheets:
 *   1. Membership report  — from memberships@dusa.deakin.edu.au
 *   2. Profit & Loss (P&L) — from dusa-accounts@deakin.edu.au
 *
 * This script runs on a daily time trigger, finds any NEW report email in the
 * mailbox it is bound to, grabs the .xlsx attachment, and POSTs it (raw, as
 * multipart/form-data) to the DSEC API, which parses it server-side and
 * ingests it into Neon. The script itself never parses Excel — it is a thin,
 * stable forwarder so the messy multi-sheet workbooks are parsed by openpyxl
 * on the server where the logic is testable and versioned.
 *
 * Idempotency is belt-and-braces:
 *   - every processed Gmail message id is recorded in Script Properties, and
 *   - the thread is labelled `DSEC/Ingested` (and excluded from the search).
 * The API also dedupes on the message id, so a re-send is always a no-op.
 *
 * FIRST-TIME SETUP
 *   1. Project Settings → Script properties → add:
 *        DSEC_API_KEY   = dsec_live_xxxxxxxx…   (a key with the `ingest` scope)
 *        DSEC_API_BASE  = https://api.dsec.club  (optional; this is the default)
 *      …or just run `setup()` once and paste the key when prompted.
 *   2. Run `setup()` once — it creates the label and the daily trigger and
 *      walks you through the OAuth consent screen.
 *   3. (Optional) Run `ingestWeeklyReports()` manually to backfill / test.
 */

// ----------------------------------------------------------------------------
// Configuration
// ----------------------------------------------------------------------------

/** Default API base; override with the DSEC_API_BASE script property. */
var DEFAULT_API_BASE = 'https://api.dsec.club';

/** Path of the ingestion endpoint on the API. */
var INGEST_PATH = '/ingest/dusa';

/** Gmail label applied to threads we have forwarded. */
var INGESTED_LABEL = 'DSEC/Ingested';

/** How far back to look. Dedup makes a wide window safe; this just bounds work. */
var SEARCH_WINDOW = 'newer_than:21d';

/**
 * The two report types. `query` is a Gmail search; keep it tight so we only
 * ever pick up the genuine DUSA emails. `report_type` is sent to the API and
 * selects the server-side parser.
 */
var REPORT_TYPES = [
  {
    report_type: 'membership',
    label: 'Membership report',
    query: 'from:memberships@dusa.deakin.edu.au ' +
           'subject:("club members weekly report") has:attachment filename:xlsx',
  },
  {
    report_type: 'pnl',
    label: 'Profit & Loss',
    query: 'from:dusa-accounts@deakin.edu.au ' +
           'subject:("Profit and Loss Report") has:attachment filename:xlsx',
  },
];

// ----------------------------------------------------------------------------
// Entry points
// ----------------------------------------------------------------------------

/**
 * One-time setup: store config, create the label, install the daily trigger.
 * Safe to re-run — it will not create duplicate triggers.
 */
function setup() {
  var props = PropertiesService.getScriptProperties();

  if (!props.getProperty('DSEC_API_KEY')) {
    // In the script editor `ui` may be unavailable; fall back to a clear throw.
    try {
      var ui = SpreadsheetApp.getUi && SpreadsheetApp.getUi();
      var resp = ui.prompt('Paste the DSEC API key (dsec_live_…)');
      props.setProperty('DSEC_API_KEY', resp.getResponseText().trim());
    } catch (e) {
      throw new Error(
        'Set the DSEC_API_KEY script property (Project Settings → Script ' +
        'properties) before running setup().');
    }
  }

  getOrCreateLabel_(INGESTED_LABEL);
  installDailyTrigger_();
  Logger.log('Setup complete. Daily trigger installed; label "%s" ready.', INGESTED_LABEL);
}

/**
 * Main job — the trigger target. Forwards every new report of each type.
 */
function ingestWeeklyReports() {
  var summary = [];
  REPORT_TYPES.forEach(function (type) {
    try {
      var n = processReportType_(type);
      summary.push(type.label + ': ' + n + ' forwarded');
    } catch (err) {
      summary.push(type.label + ': ERROR ' + err.message);
      Logger.log('Error processing %s: %s', type.label, err.stack || err.message);
    }
  });
  Logger.log('Run complete — %s', summary.join('; '));
}

// ----------------------------------------------------------------------------
// Per-type processing
// ----------------------------------------------------------------------------

/**
 * Find unprocessed messages of one report type and forward each. Returns the
 * number successfully forwarded.
 */
function processReportType_(type) {
  var query = type.query + ' ' + SEARCH_WINDOW + ' -label:' + INGESTED_LABEL;
  var threads = GmailApp.search(query, 0, 25);
  var sent = 0;

  threads.forEach(function (thread) {
    thread.getMessages().forEach(function (msg) {
      var msgId = msg.getId();
      if (isProcessed_(msgId)) return;

      var file = pickSpreadsheet_(msg);
      if (!file) return; // not the email we want (no .xlsx attachment)

      var ok = sendToApi_(type.report_type, msg, file);
      if (ok) {
        markProcessed_(msgId);
        sent++;
      }
    });
    // Label the whole thread once any of its messages were forwarded.
    thread.addLabel(getOrCreateLabel_(INGESTED_LABEL));
  });

  return sent;
}

// ----------------------------------------------------------------------------
// HTTP
// ----------------------------------------------------------------------------

/**
 * POST one attachment to the API as multipart/form-data with metadata.
 * Returns true on a 2xx (or 409 = already ingested, which we treat as success).
 */
function sendToApi_(reportType, msg, file) {
  var props = PropertiesService.getScriptProperties();
  var apiKey = props.getProperty('DSEC_API_KEY');
  if (!apiKey) throw new Error('DSEC_API_KEY script property is not set.');
  var base = props.getProperty('DSEC_API_BASE') || DEFAULT_API_BASE;
  var url = base.replace(/\/+$/, '') + INGEST_PATH;

  // A multipart payload: a Blob value makes UrlFetchApp send multipart/form-data.
  var payload = {
    report_type: reportType,
    message_id: msg.getId(),
    received_at: msg.getDate().toISOString(),
    sender: msg.getFrom(),
    subject: msg.getSubject(),
    filename: file.getName(),
    file: file.copyBlob().setName(file.getName()),
  };

  var options = {
    method: 'post',
    payload: payload,
    headers: { Authorization: 'Bearer ' + apiKey },
    muteHttpExceptions: true,
    followRedirects: true,
  };

  var resp = fetchWithRetry_(url, options, 2);
  var code = resp.getResponseCode();
  if (code >= 200 && code < 300) {
    Logger.log('Forwarded %s (%s) → %s', reportType, file.getName(), code);
    return true;
  }
  if (code === 409) {
    Logger.log('Already ingested %s (%s) — server reports duplicate.', reportType, msg.getId());
    return true;
  }
  Logger.log('FAILED %s (%s): HTTP %s — %s', reportType, file.getName(), code,
             truncate_(resp.getContentText(), 500));
  return false;
}

/** Fetch with a tiny exponential backoff on 5xx / transient failures. */
function fetchWithRetry_(url, options, attempts) {
  var lastErr;
  for (var i = 0; i < attempts; i++) {
    try {
      var resp = UrlFetchApp.fetch(url, options);
      if (resp.getResponseCode() < 500) return resp;
      lastErr = new Error('HTTP ' + resp.getResponseCode());
    } catch (e) {
      lastErr = e;
    }
    Utilities.sleep(1500 * (i + 1));
  }
  // Final attempt — let the caller see whatever it is.
  return UrlFetchApp.fetch(url, options);
}

// ----------------------------------------------------------------------------
// Attachment selection
// ----------------------------------------------------------------------------

/**
 * Return the first .xlsx attachment on a message, or null. Ignores inline
 * images and the DUSA signature logo.
 */
function pickSpreadsheet_(msg) {
  var atts = msg.getAttachments({ includeInlineImages: false, includeAttachments: true });
  for (var i = 0; i < atts.length; i++) {
    var a = atts[i];
    var name = (a.getName() || '').toLowerCase();
    var ctype = (a.getContentType() || '').toLowerCase();
    var isXlsx = name.indexOf('.xlsx') === name.length - 5 ||
                 ctype.indexOf('spreadsheetml') !== -1 ||
                 ctype.indexOf('ms-excel') !== -1;
    if (isXlsx) return a;
  }
  return null;
}

// ----------------------------------------------------------------------------
// Idempotency helpers
// ----------------------------------------------------------------------------

function isProcessed_(msgId) {
  return PropertiesService.getScriptProperties().getProperty('done:' + msgId) === '1';
}

function markProcessed_(msgId) {
  PropertiesService.getScriptProperties().setProperty('done:' + msgId, '1');
}

// ----------------------------------------------------------------------------
// Misc helpers
// ----------------------------------------------------------------------------

function getOrCreateLabel_(name) {
  return GmailApp.getUserLabelByName(name) || GmailApp.createLabel(name);
}

function installDailyTrigger_() {
  var exists = ScriptApp.getProjectTriggers().some(function (t) {
    return t.getHandlerFunction() === 'ingestWeeklyReports';
  });
  if (exists) return;
  // Reports land Friday; run daily so a missed day still catches up next run.
  ScriptApp.newTrigger('ingestWeeklyReports')
    .timeBased()
    .everyDays(1)
    .atHour(9)
    .create();
}

function truncate_(s, n) {
  s = s || '';
  return s.length > n ? s.slice(0, n) + '…' : s;
}
