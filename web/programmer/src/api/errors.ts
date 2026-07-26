// Shared API error type + helper.
//
// Every same-origin REST transport (`request()` in api/base.ts, plus the
// hand-rolled fetches in projectClient/streamsClient) throws an `ApiError`, so
// callers can classify a failure by its HTTP `status` code instead of
// string-matching the message wording — which silently rebreaks every time the
// server rewords an error. The message is kept in the historical
// "API <status>: <body>" form so existing `String(e)` / `.message` consumers
// keep rendering the same text.

/**
 * Pull the human-readable detail out of a JSON error body, if it has one.
 *
 * The server's contract is that `detail` is always a string carrying the whole
 * message; anything a client branches on rides in sibling top-level keys (see
 * `server/api/errors.py`), so there is exactly one place to look for the text.
 */
function extractDetail(body: string): string | null {
  try {
    const detail = JSON.parse(body)?.detail;
    if (typeof detail === "string") return detail;
  } catch {
    /* not JSON, fall through */
  }
  return null;
}

/**
 * Error thrown by the REST transports on a non-2xx response. Carries the HTTP
 * `status` as data so components compare `err.status === 429` instead of
 * string-matching the message wording. `detail` is the clean, human-readable
 * message (the JSON `detail`, falling back to the raw body).
 */
export class ApiError extends Error {
  readonly status: number;
  /** Raw response body text. */
  readonly body: string;
  /** Clean human-readable detail (JSON `detail`, else the raw body). */
  readonly detail: string;

  constructor(status: number, body: string) {
    // Keep the historical message form so String(e)/.message consumers are
    // unaffected; the machine-readable data lives on the typed fields.
    super(`API ${status}: ${body}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.detail = extractDetail(body) ?? body;
  }
}

/**
 * Unwrap any thrown value to a clean, human-readable string. Handles the typed
 * `ApiError` directly and still parses the legacy "API <status>: <body>"
 * string envelope for anything that isn't (defensive).
 */
export function parseApiError(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  if (!(e instanceof Error)) return String(e);
  const match = e.message.match(/^API \d+: (.+)/s);
  if (!match) return e.message;
  return extractDetail(match[1]) ?? e.message;
}
