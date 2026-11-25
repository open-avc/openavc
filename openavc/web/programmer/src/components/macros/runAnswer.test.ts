import { describe, it, expect } from "vitest";
import { runTrouble, didNotStart } from "./runAnswer";

// What the person who ran a macro by hand is told when it did not go cleanly.
// A run the macro refused said "it's already running, or it's still in its
// cooldown", a guess that was wrong for a queue that timed out or a system
// stopping. The server's answer now says which.

describe("runTrouble", () => {
  it("says why a run did not start, in the server's words", () => {
    const answer = {
      status: "skipped",
      code: "macro_queue_timeout",
      message: "The macro was still running after 300 seconds, so this queued run was dropped.",
    };
    expect(runTrouble(answer)).toBe(
      "The macro didn't start. The macro was still running after 300 seconds, so this queued run was dropped.",
    );
  });

  it("names no cause it was not given", () => {
    expect(runTrouble({ status: "skipped" })).toBe("The macro didn't start.");
  });

  it("says a failed or cancelled run did not go cleanly", () => {
    expect(runTrouble({ status: "failed" })).toContain("some of its steps didn't");
    expect(runTrouble({ status: "cancelled" })).toContain("cancelled before it finished");
  });

  it("says nothing about a clean run or one still running", () => {
    expect(runTrouble({ status: "completed" })).toBeNull();
    expect(runTrouble({ status: "running" })).toBeNull();
  });
});

describe("didNotStart", () => {
  it("is the same sentence the Test button shows", () => {
    const answer = { status: "skipped", code: "stopping", message: "The system is stopping." };
    expect(didNotStart(answer)).toBe(runTrouble(answer));
  });
});
