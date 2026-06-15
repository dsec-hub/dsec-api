/**
 * DSEC — inbound email capture forwarder (Google Apps Script)
 * ------------------------------------------------------------------
 * Fires the DSEC API on EVERY new inbound email. No decision-making: it just
 * POSTs the raw message (from / to / subject / body) to `/ingest/email`, which
 * records it to the EventLog. Triage (spam-gate / classify / draft) is a
 * separate, later step at `/email/process` — this script never classifies.
 *
 * It runs on a 15-minute time trigger, scans recent inbox messages, and forwards
 * any it has not sent yet. Idempotency is belt-and-braces:
 *   - every processed Gmail message id is recorded in Script Properties, and
 *   - the API dedupes on the message id (a re-send returns status="duplicate").
 * There is intentionally NO thread label gate (unlike the DUSA forwarder):
 * threads gain replies over time, so we dedupe per-message, not per-thread.
 *
 * FIRST-TIME SETUP
 *   1. Bind this to the Gmail account that receives the mail (deakinsec@gmail.com).
 *   2. Project Settings → Script properties → add:
 *        DSEC_API_KEY   = dsec_live_xxxxxxxx…   (a key with the `ingest` scope)
 *        DSEC_API_BASE  = https://api.dsec.club  (optional; this is the default)
 *   3. Run `setup()` once — installs the 15-minute trigger and walks the OAuth
 *      consent screen.
 *   4. (Optional) Run `captureInbox()` manually once to test.
 *
 * This is a SEPARATE Apps Script project from the DUSA forwarder; they share the
 * same API key (which needs the `ingest` scope) but run independently.
 */

// ----------------------------------------------------------------------------
// Configuration
// ----------------------------------------------------------------------------

/** Default API base; override with the DSEC_API_BASE script property. */
var DEFAULT_API_BASE = 'https://api.dsec.club';

/** Path of the capture endpoint on the API. */
var CAPTURE_PATH = '/ingest/email';

/**
 * Which messages to capture. `in:inbox` = inbound mail that actually landed in
 * the inbox (excludes Sent/Drafts/Chats). `newer_than:2d` bounds the work and
 * gives the API ~2 days of retry runway if it is briefly down. Widen this if you
 * want filtered/auto-archived mail too (e.g. drop `in:inbox`).
 */
var SEARCH_QUERY = 'in:inbox newer_than:2d';

/** Truncate very long bodies so the JSON payload stays small. */
var MAX_BODY_CHARS = 16000;

// ----------------------------------------------------------------------------
// Entry points
// ----------------------------------------------------------------------------

/**
 * One-time setup: install the recurring trigger. Safe to re-run — it will not
 * create duplicate triggers.
 */
function setup() {
  var props = PropertiesService.getScriptProperties();
  if (!props.getProperty('DSEC_API_KEY')) {
    throw new Error(
      'Set the DSEC_API_KEY script property (Project Settings → Script ' +
      'properties) before running setup().');
  }
  installTrigger_();
  Logger.log('Setup complete. 15-minute capture trigger installed.');
}

/**
 * Main job — the trigger target. Forwards every not-yet-sent inbox message.
 */
function captureInbox() {
  var threads = GmailApp.search(SEARCH_QUERY, 0, 50);
  var sent = 0;
  var failed = 0;

  threads.forEach(function (thread) {
    thread.getMessages().forEach(function (msg) {
      var msgId = msg.getId();
      if (isProcessed_(msgId)) return;

      if (sendToApi_(msg)) {
        markProcessed_(msgId);
        sent++;
      } else {
        failed++;
      }
    });
  });

  Logger.log('Capture run complete — %s forwarded, %s failed.', sent, failed);
}

// ----------------------------------------------------------------------------
// HTTP
// ----------------------------------------------------------------------------

/**
 * POST one message to the API as JSON. Returns true on any 2xx (which includes
 * the idempotent status="duplicate" response).
 */
function sendToApi_(msg) {
  var props = PropertiesService.getScriptProperties();
  var apiKey = props.getProperty('DSEC_API_KEY');
  if (!apiKey) throw new Error('DSEC_API_KEY script property is not set.');
  var base = props.getProperty('DSEC_API_BASE') || DEFAULT_API_BASE;
  var url = base.replace(/\/+$/, '') + CAPTURE_PATH;

  var body = {
    message_id: msg.getId(),
    thread_id: msg.getThread().getId(),
    from: msg.getFrom(),
    to: msg.getTo(),
    subject: msg.getSubject(),
    body: truncate_(msg.getPlainBody(), MAX_BODY_CHARS),
    received_at: msg.getDate().toISOString(),
  };

  var options = {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(body),
    headers: { Authorization: 'Bearer ' + apiKey },
    muteHttpExceptions: true,
    followRedirects: true,
  };

  var resp = fetchWithRetry_(url, options, 2);
  var code = resp.getResponseCode();
  if (code >= 200 && code < 300) {
    Logger.log('Captured %s → %s', msg.getId(), code);
    return true;
  }
  Logger.log('FAILED capture %s: HTTP %s — %s', msg.getId(), code,
             truncate_(resp.getContentText(), 500));
  return false;
}

/** Fetch with a tiny exponential backoff on 5xx / transient failures. */
function fetchWithRetry_(url, options, attempts) {
  for (var i = 0; i < attempts; i++) {
    try {
      var resp = UrlFetchApp.fetch(url, options);
      if (resp.getResponseCode() < 500) return resp;
    } catch (e) {
      // network blip — fall through to the backoff and retry
    }
    Utilities.sleep(1500 * (i + 1));
  }
  // Final attempt — let the caller see whatever it is.
  return UrlFetchApp.fetch(url, options);
}

// ----------------------------------------------------------------------------
// Idempotency + helpers
// ----------------------------------------------------------------------------

function isProcessed_(msgId) {
  return PropertiesService.getScriptProperties().getProperty('cap:' + msgId) === '1';
}

function markProcessed_(msgId) {
  PropertiesService.getScriptProperties().setProperty('cap:' + msgId, '1');
}

function installTrigger_() {
  var exists = ScriptApp.getProjectTriggers().some(function (t) {
    return t.getHandlerFunction() === 'captureInbox';
  });
  if (exists) return;
  ScriptApp.newTrigger('captureInbox')
    .timeBased()
    .everyMinutes(15)
    .create();
}

function truncate_(s, n) {
  s = s || '';
  return s.length > n ? s.slice(0, n) + '…' : s;
}
