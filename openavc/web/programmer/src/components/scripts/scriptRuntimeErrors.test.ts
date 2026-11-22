import { describe, it, expect } from "vitest";
import { markerFromLoadError, runtimeErrorTitle, toRuntimeErrorRows, type StoredRuntimeError } from "./scriptRuntimeErrors";

// The Scripts list's row for a script that failed while running. Some failures
// leave a line in the script to open (a handler that raised, an event sent past
// the chain limit); some do not (a timeout, a control's call refused before the
// function ran), and the row must not send anyone looking for one.

const RAISED: StoredRuntimeError = {
  count: 2,
  handler: "check_room",
  event: "every() timer",
  error: "'lectern'",
  traceback:
    "Traceback (most recent call last):\n" +
    '  File "/srv/project/scripts/poller.py", line 4, in check_room\n' +
    "KeyError: 'lectern'\n",
  at: 0,
};

const REFUSED: StoredRuntimeError = {
  count: 1,
  handler: "play_film",
  event: "script.call.play_film",
  error: "No script function named 'play_film' in script 'poller'. Check the function is defined in an enabled script and is not an event handler.",
  traceback: "",
  at: 0,
};

const SCRIPTS = [{ id: "poller", file: "poller.py" }];

describe("the Scripts list's runtime-error rows", () => {
  it("marks a failure whose trace reaches the script's own file", () => {
    const rows = toRuntimeErrorRows({ poller: RAISED }, SCRIPTS);
    expect(rows.poller).toEqual({
      count: 2,
      handler: "check_room",
      event: "every() timer",
      error: "KeyError: 'lectern'",
      marksLine: true,
    });
    expect(runtimeErrorTitle(rows.poller)).toBe(
      "check_room failed on every() timer: KeyError: 'lectern'. Open the script to see where.",
    );
  });

  it("does not point into the script for a refusal with no trace", () => {
    const rows = toRuntimeErrorRows({ poller: REFUSED }, SCRIPTS);
    expect(rows.poller.marksLine).toBe(false);
    expect(runtimeErrorTitle(rows.poller)).not.toContain("Open the script");
    expect(runtimeErrorTitle(rows.poller)).not.toContain("..");
  });

  it("does not mark a trace that runs only through other files", () => {
    const elsewhere = { ...RAISED, traceback: RAISED.traceback.replace("poller.py", "helpers.py") };
    expect(toRuntimeErrorRows({ poller: elsewhere }, SCRIPTS).poller.marksLine).toBe(false);
  });
});

// A script that did not load: the editor marks the line Python named, from
// the server's load error (`GET /api/scripts/errors`, and a reload's answer),
// worded the way Python words it.
describe("the editor's mark for a script that did not load", () => {
  it("marks the line a syntax error names", () => {
    expect(markerFromLoadError("invalid syntax (poller.py, line 2)", "poller.py")).toEqual({
      line: 2,
      message: "This script did not load: invalid syntax (poller.py, line 2)",
    });
  });

  it("marks the line at the end, not another line the sentence names", () => {
    const error = "expected an indented block after function definition on line 1 (poller.py, line 2)";
    expect(markerFromLoadError(error, "poller.py")?.line).toBe(2);
  });

  it("marks nothing for a load error that names no line", () => {
    expect(markerFromLoadError("name 'lectern' is not defined", "poller.py")).toBeNull();
  });

  it("marks nothing for a line in another file", () => {
    expect(markerFromLoadError("invalid syntax (helpers.py, line 4)", "poller.py")).toBeNull();
  });

  it("marks nothing when the script loaded", () => {
    expect(markerFromLoadError(undefined, "poller.py")).toBeNull();
  });
});
