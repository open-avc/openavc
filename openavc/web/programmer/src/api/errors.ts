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
 * `openavc/api/errors.py`), so there is exactly one place to look for the text.
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

/** What the page says when its own OpenAVC server did not answer at all. */
export const SERVER_NOT_ANSWERING =
  "The OpenAVC server is not answering. Check that it is running and reachable on the network, then try again.";

// The TypeError a fetch rejects with when no answer came back at all (the
// server stopped, the network dropped, the connection was refused), in each
// browser's own words: Chrome and Edge, Firefox, then Safari's. Any other
// TypeError is a fault in the page and keeps its own text.
const NO_ANSWER_MESSAGES =
  /^(Failed to fetch|NetworkError when attempting to fetch resource\.?|Load failed|The network connection was lost\.?|Could not connect to the server\.?|The Internet connection appears to be offline\.?)$/;

/** True when a fetch got no answer from the server at all. */
export function isNoAnswerError(e: unknown): boolean {
  return e instanceof TypeError && NO_ANSWER_MESSAGES.test(e.message);
}

/**
 * Unwrap any thrown value to a clean, human-readable string. Handles the typed
 * `ApiError` directly, turns a fetch that got no answer into one sentence
 * saying so, and still parses the legacy "API <status>: <body>" string
 * envelope for anything else (defensive).
 */
export function parseApiError(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  if (isNoAnswerError(e)) return SERVER_NOT_ANSWERING;
  if (!(e instanceof Error)) return String(e);
  const match = e.message.match(/^API \d+: (.+)/s);
  if (!match) return e.message;
  return extractDetail(match[1]) ?? e.message;
}

/**
 * The `code` a structured refusal carries beside `detail`: "" when the answer
 * has none, undefined when the failure was not an answer from the server.
 * Report a problem reads it (a 409 `command_incomplete` is the driver's).
 */
export function apiErrorCode(e: unknown): string | undefined {
  if (!(e instanceof ApiError)) return undefined;
  try {
    const code = JSON.parse(e.body)?.code;
    return typeof code === "string" ? code : "";
  } catch {
    return "";
  }
}

/**
 * The `errors` list a structured refusal carries beside `detail` (a driver
 * definition that failed validation lists each problem there), or null when
 * the error has none.
 */
export function apiErrorList(e: unknown): string[] | null {
  if (!(e instanceof ApiError)) return null;
  try {
    const errors = JSON.parse(e.body)?.errors;
    if (
      Array.isArray(errors) &&
      errors.length > 0 &&
      errors.every((item) => typeof item === "string")
    ) {
      return errors;
    }
  } catch {
    /* not JSON */
  }
  return null;
}
