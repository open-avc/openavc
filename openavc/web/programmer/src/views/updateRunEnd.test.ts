import { describe, it, expect } from "vitest";
import { sameVersionRestartSentence } from "./updateRunEnd";

// A restart that left the version where it was says how the run ended, as
// the server settled it at its start, never a guess.
describe("sameVersionRestartSentence", () => {
  const ended = (end: string, message: string, version = "0.41.0") => ({
    "system.version": version,
    "system.update_last_result": end,
    "system.update_last_result_version": "0.42.0",
    "system.update_last_result_message": message,
  });

  it("says why an update did not apply", () => {
    expect(sameVersionRestartSentence(ended("failed", "Update did not apply: expected v0.42.0, running v0.41.0")))
      .toBe("Update did not apply: expected v0.42.0, running v0.41.0.");
  });

  it("says a rollback did not take effect, not that an update may not have applied", () => {
    const sentence = sameVersionRestartSentence(ended("failed", "Rollback did not take effect: still running v0.42.0", "0.42.0"));
    expect(sentence).toBe("Rollback did not take effect: still running v0.42.0.");
    expect(sentence).not.toContain("may not have applied");
  });

  it("says an update was reverted", () => {
    expect(sameVersionRestartSentence(ended("rolled_back", "Update was reverted; running v0.41.0")))
      .toBe("Update was reverted; running v0.41.0.");
  });

  it("gives the helper's reason for an update it tries again", () => {
    const message = "Update not applied yet (could not download the release's dependencies); it is tried again at the next start";
    expect(sameVersionRestartSentence(ended("pending", message))).toBe(message + ".");
  });

  it("says only that the version did not change when there is no end to read", () => {
    expect(sameVersionRestartSentence({ "system.version": "0.41.0" }))
      .toBe("The server restarted but the version did not change (still v0.41.0). Check Update History.");
    expect(sameVersionRestartSentence(ended("success", ""))).toContain("did not change");
  });
});
