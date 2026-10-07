/**
 * Web Worker for serializing large project objects off the main thread.
 *
 * Usage: post the project object (structured clone), receive the JSON
 * string back. Falls back gracefully — if the worker can't be created or
 * the clone fails, the caller stringifies on the main thread instead.
 */

import { parseApiError } from "../api/errors";

self.onmessage = (event: MessageEvent<unknown>) => {
  try {
    const text = JSON.stringify(event.data);
    self.postMessage({ ok: true, text });
  } catch (e) {
    self.postMessage({ ok: false, error: parseApiError(e) });
  }
};
