import { describe, it, expect } from "vitest";
import { healthProbeOutcome, updateCompletionOutcome, semverLt } from "./updatesHelpers";

// The health probe is the completion signal that survives a restart. The
// WebSocket one does not: session tokens live in server memory, so the restart
// being waited on invalidates this browser's token and the Programmer socket
// comes back refused. On a claimed instance that left a SUCCESSFUL update
// sitting under "Restarting server" until the watchdog called it slow.
describe("healthProbeOutcome", () => {
  it("reports an update when the probed version moved forward", () => {
    expect(healthProbeOutcome("0.24.1", "0.25.0", null)).toBe("updated");
  });

  it("reports a rollback when the probed version moved backward", () => {
    expect(healthProbeOutcome("0.25.0", "0.24.1", null)).toBe("rolled_back");
  });

  it("trusts the in-flight action over semver direction", () => {
    // A rollback to a HIGHER version is possible (rolling back a downgrade).
    // The action the user started is authoritative.
    expect(healthProbeOutcome("0.24.1", "0.25.0", "rollback")).toBe("rolled_back");
  });

  it("stays silent while the version is unchanged", () => {
    // The server answers /api/health both before it restarts and after a
    // same-version restart. Neither is a completed update, and treating them
    // as one would close the dialog with a false success.
    expect(healthProbeOutcome("0.25.0", "0.25.0", "update")).toBeNull();
  });

  it("stays silent when either version is missing", () => {
    // A probe that returns no version, or a flow with no anchor recorded,
    // must not resolve the dialog on an empty-string comparison.
    expect(healthProbeOutcome("", "0.25.0", "update")).toBeNull();
    expect(healthProbeOutcome("0.24.1", "", "update")).toBeNull();
  });

  it("agrees with the WebSocket path on the same transition", () => {
    // Two code paths race to resolve one dialog; if they disagreed on
    // direction the toast would depend on which won.
    const start = "0.25.0";
    const probed = "0.24.1";
    expect(healthProbeOutcome(start, probed, "rollback")).toBe(
      updateCompletionOutcome(start, "restarting", probed, "idle", "rollback"),
    );
  });
});

describe("semverLt", () => {
  it("orders normal releases", () => {
    expect(semverLt("0.24.1", "0.25.0")).toBe(true);
    expect(semverLt("0.25.0", "0.24.1")).toBe(false);
  });

  it("claims no ordering for non-numeric parts", () => {
    expect(semverLt("0.25.0", "abc")).toBe(false);
  });
});
