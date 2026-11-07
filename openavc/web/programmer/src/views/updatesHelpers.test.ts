import { describe, it, expect } from "vitest";
import {
  healthProbeOutcome,
  historyEntryDisplay,
  panelSoftwareDisplay,
  updateCompletionOutcome,
  semverLt,
} from "./updatesHelpers";

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

// A row whose update was applied and then reverted used to go on saying
// "success" from a system sitting on the older version -- measured on an
// appliance panel, and the only part of that defect a customer ever saw. The
// server now writes `rolled_back`; this is what the page makes of it.
describe("historyEntryDisplay", () => {
  it("shows a reverted update as failed, in words", () => {
    const d = historyEntryDisplay({
      from_version: "0.31.0",
      to_version: "0.32.0",
      status: "rolled_back",
    });
    expect(d.label).toBe("v0.31.0 → v0.32.0");
    expect(d.succeeded).toBe(false);
    expect(d.statusLabel).toBe("reverted");
    expect(d.isRollback).toBe(false);
  });

  it("keeps an update that stuck", () => {
    const d = historyEntryDisplay({
      from_version: "0.31.0",
      to_version: "0.32.0",
      status: "success",
    });
    expect(d.succeeded).toBe(true);
    expect(d.statusLabel).toBe("success");
  });

  it("passes through a status it has no better word for", () => {
    // The set is open: the server may add one, and an unknown status must
    // still be shown rather than swallowed.
    expect(historyEntryDisplay({ from_version: "1.0.0", to_version: "2.0.0", status: "pending" }).statusLabel)
      .toBe("pending");
  });

  it("still labels a rollback entry by what it restored", () => {
    const d = historyEntryDisplay({
      from_version: "0.32.0",
      to_version: "0.31.0",
      status: "success",
      rollback: true,
    });
    expect(d.isRollback).toBe(true);
    expect(d.label).toBe("v0.32.0 → v0.31.0");
    expect(d.succeeded).toBe(true);
  });

  it("still reads a legacy rollback row with no target version", () => {
    const d = historyEntryDisplay({ from_version: "0.32.0", to_version: "rollback" });
    expect(d.isRollback).toBe(true);
    expect(d.label).toBe("v0.32.0 → previous version");
  });
});

// The Panel Software section of the Updates page reads the
// `system.panel_software.*` keys the server publishes from the panel's own
// declaration. Absent everywhere that is not appliance hardware.
describe("panelSoftwareDisplay", () => {
  const base = {
    "system.panel_software.version": "0.3.27",
    "system.panel_software.version_code": 30,
    "system.panel_software.install_idle_seconds": 600,
    "system.panel_software.pending_version": "",
    "system.panel_software.pending_install": "",
    "system.panel_software.last_result": "",
  };

  it("is absent without panel software", () => {
    expect(panelSoftwareDisplay({})).toBeNull();
    expect(panelSoftwareDisplay({ "system.version": "0.40.0" })).toBeNull();
  });

  it("shows the version with nothing waiting and no history", () => {
    expect(panelSoftwareDisplay(base)).toEqual({ version: "0.3.27", pending: null, lastResult: null });
  });

  it("says how each waiting update installs", () => {
    const waiting = (mode: string) =>
      panelSoftwareDisplay({
        ...base,
        "system.panel_software.pending_version": "0.3.28",
        "system.panel_software.pending_install": mode,
      })?.pending;
    expect(waiting("when_idle")).toBe(
      "Panel software 0.3.28 is ready. It installs by itself once nobody has touched the panel for 10 minutes.",
    );
    expect(waiting("held")).toBe(
      "Panel software 0.3.28 is ready. It waits while the space is in use, then installs once nobody has touched the panel for 10 minutes.",
    );
    expect(waiting("at_panel")).toBe("Panel software 0.3.28 is ready. It installs when somebody starts it at the panel.");
  });

  it("reports the last attempt in the panel's own words when it did not install", () => {
    const last = (result: string, message: string) =>
      panelSoftwareDisplay({
        ...base,
        "system.panel_software.last_result": result,
        "system.panel_software.last_result_version": "0.3.28",
        "system.panel_software.last_result_message": message,
        "system.panel_software.last_result_at": "2026-11-06T09:00:00Z",
      })?.lastResult;
    expect(last("installed", "Updated from 0.3.27.")).toEqual({
      ok: true, text: "Updated to 0.3.28", at: "2026-11-06T09:00:00Z",
    });
    expect(last("refused", "The file is not signed with this panel's key.")).toEqual({
      ok: false,
      text: "Refused the update to 0.3.28: The file is not signed with this panel's key.",
      at: "2026-11-06T09:00:00Z",
    });
    expect(last("failed", "The update did not install: the installer never reported back")?.text).toBe(
      "The update did not install: the installer never reported back",
    );
    expect(last("failed", "")?.text).toBe("The update to 0.3.28 did not install.");
  });
});
