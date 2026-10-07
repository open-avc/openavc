/**
 * Web Worker for parsing large project JSON off the main thread.
 *
 * Usage: post a raw JSON string, receive the parsed object back.
 * Falls back gracefully — if the worker can't be created, the caller
 * parses on the main thread instead.
 */

import { parseApiError } from "../api/errors";

self.onmessage = (event: MessageEvent<string>) => {
  try {
    const parsed = JSON.parse(event.data);
    self.postMessage({ ok: true, data: parsed });
  } catch (e) {
    self.postMessage({ ok: false, error: parseApiError(e) });
  }
};
