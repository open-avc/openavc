/**
 * Rate-limit state writes so a drag is a handful of requests, not hundreds.
 *
 * A range input fires a change per pixel of travel and every text field fires
 * one per keystroke, and each of those was its own POST. Across a slider the
 * width of a card that is a few hundred writes for one gesture, all of them
 * superseded by the last.
 *
 * So: send the first change at once (the control has to feel live), then at
 * most one per window per key, always carrying the newest value. Keys are
 * independent -- moving a fader must not delay a mute pressed at the same
 * moment -- and the final value of a gesture is never dropped, which is the
 * part that matters: whatever the throttle discards, the device still ends up
 * holding what the user let go of.
 */

export interface ThrottledWriter {
  /** Queue a write. Sends now, or at the end of the current window. */
  write(key: string, value: unknown): void;
  /** Send anything still queued. For unmount. */
  flush(): void;
  /** Drop anything still queued without sending. */
  cancel(): void;
}

export function createThrottledWriter(
  send: (key: string, value: unknown) => void,
  windowMs = 60,
  schedule: (fn: () => void, ms: number) => ReturnType<typeof setTimeout> = setTimeout,
  unschedule: (t: ReturnType<typeof setTimeout>) => void = clearTimeout,
): ThrottledWriter {
  // A key is "open" while its window is running. `pending` holds the newest
  // value that arrived during that window, if any.
  const timers = new Map<string, ReturnType<typeof setTimeout>>();
  const pending = new Map<string, unknown>();

  function closeWindow(key: string) {
    if (pending.has(key)) {
      const value = pending.get(key);
      pending.delete(key);
      send(key, value);
      // Something was still moving, so keep the window open behind it.
      timers.set(key, schedule(() => closeWindow(key), windowMs));
    } else {
      timers.delete(key);
    }
  }

  return {
    write(key, value) {
      if (timers.has(key)) {
        pending.set(key, value);
        return;
      }
      send(key, value);
      timers.set(key, schedule(() => closeWindow(key), windowMs));
    },
    flush() {
      for (const [key, value] of pending) send(key, value);
      pending.clear();
      for (const t of timers.values()) unschedule(t);
      timers.clear();
    },
    cancel() {
      pending.clear();
      for (const t of timers.values()) unschedule(t);
      timers.clear();
    },
  };
}
