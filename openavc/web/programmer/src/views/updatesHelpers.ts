// Pure decision helpers for the Updates view, extracted so the update-flow
// logic (toast direction, completion detection, history labels) is
// unit-testable outside React.

/** Numeric semver comparison: true when a < b. Non-numeric parts compare
 *  false (no ordering claimed). Missing parts count as 0. */
export function semverLt(a: string, b: string): boolean {
  const parse = (v: string) => v.split(/[.+-]/).map((n) => parseInt(n, 10));
  const pa = parse(a);
  const pb = parse(b);
  for (let i = 0; i < 3; i++) {
    const x = pa[i] ?? 0;
    const y = pb[i] ?? 0;
    if (Number.isNaN(x) || Number.isNaN(y)) return false;
    if (x !== y) return x < y;
  }
  return false;
}

export type CompletionOutcome = "updated" | "rolled_back" | "same_version_restart" | null;

/**
 * Decide what a state transition means for the update-progress UI.
 *
 * After a restart the server comes back with the new (or rolled-back)
 * version and system.update_status reset to "idle", delivered in one WS
 * snapshot. A version change completes the flow as an update or rollback —
 * the explicit in-flight action wins, falling back to the semver direction
 * (covers cloud-initiated rollbacks). A restarting -> idle transition with
 * NO version change means the restart finished but nothing was applied.
 */
export function updateCompletionOutcome(
  prevVersion: string,
  prevStatus: string,
  version: string,
  status: string,
  action: "update" | "rollback" | null,
): CompletionOutcome {
  if (prevVersion && version && version !== prevVersion) {
    if (action === "rollback" || semverLt(version, prevVersion)) return "rolled_back";
    return "updated";
  }
  if (prevStatus === "restarting" && status === "idle") {
    return "same_version_restart";
  }
  return null;
}

/**
 * Decide completion from an unauthenticated `/api/health` probe.
 *
 * The WebSocket snapshot cannot be the only completion signal. Session tokens
 * live in server memory, so the very restart this dialog is waiting for
 * invalidates the browser's token, and the Programmer socket comes back
 * refused (`ws.py` closes it 4001). On a claimed instance — which is every
 * shipped deployment — that left a finished update sitting under "Restarting
 * server" until the watchdog called it slow, and the only way to learn it had
 * worked was to reload and sign in again.
 *
 * `/api/health` needs no credential and reports the running version, which is
 * exactly the fact being waited on. A changed version means the swap
 * happened; direction is decided by the same rule the WS path uses.
 */
export function healthProbeOutcome(
  startVersion: string,
  probedVersion: string,
  action: "update" | "rollback" | null,
): CompletionOutcome {
  if (!startVersion || !probedVersion || probedVersion === startVersion) return null;
  return updateCompletionOutcome(startVersion, "", probedVersion, "", action);
}

export interface HistoryEntryLike {
  from_version: string;
  to_version: string;
  status?: string;
  rollback?: boolean;
}

/** Statuses that mean the version in this row is the one now running. */
const SUCCEEDED = new Set(["success", "applied"]);

/**
 * How a status reads on screen. The server writes machine words; only
 * `rolled_back` needs help, and it is the one that matters most — it is what
 * a row says after an update was applied and then undone, which used to go on
 * reporting "success" from a system sitting on the older version.
 */
const STATUS_LABELS: Record<string, string> = {
  rolled_back: "reverted",
};

/**
 * Display label for an update-history entry. Rollback entries record the
 * real target version plus a rollback flag; legacy entries carried the
 * literal string "rollback" in to_version.
 */
export function historyEntryDisplay(entry: HistoryEntryLike): {
  label: string;
  isRollback: boolean;
  succeeded: boolean;
  statusLabel: string;
} {
  const legacy = entry.to_version === "rollback";
  const isRollback = !!entry.rollback || legacy;
  const target = legacy || !entry.to_version
    ? "previous version"
    : "v" + entry.to_version;
  const status = entry.status || "";
  return {
    label: "v" + entry.from_version + " → " + (isRollback ? target : "v" + entry.to_version),
    isRollback,
    succeeded: SUCCEEDED.has(status),
    statusLabel: STATUS_LABELS[status] ?? status,
  };
}

/**
 * The panel software section of the Updates page, from the
 * `system.panel_software.*` state keys. Null where there is no panel software,
 * which is every system that is not appliance hardware.
 */
export interface PanelSoftwareDisplay {
  version: string;
  /** A sentence about an update waiting on the panel, or null. */
  pending: string | null;
  /** The last install attempt, or null when there has been none. */
  lastResult: { ok: boolean; text: string; at: string } | null;
}

export function panelSoftwareDisplay(state: Record<string, unknown>): PanelSoftwareDisplay | null {
  const version = String(state["system.panel_software.version"] ?? "");
  if (!version) return null;

  const pendingVersion = String(state["system.panel_software.pending_version"] ?? "");
  const pendingInstall = String(state["system.panel_software.pending_install"] ?? "");
  const idleSeconds = Number(state["system.panel_software.install_idle_seconds"] ?? 0);
  const minutes = Math.max(1, Math.round((idleSeconds > 0 ? idleSeconds : 600) / 60));
  const untouched = "nobody has touched the panel for " + minutes + (minutes === 1 ? " minute" : " minutes");
  const ready = "Panel software " + (pendingVersion || "update") + " is ready.";
  let pending: string | null = null;
  if (pendingInstall === "when_idle") {
    pending = ready + " It installs by itself once " + untouched + ".";
  } else if (pendingInstall === "held") {
    pending = ready + " It waits while the space is in use, then installs once " + untouched + ".";
  } else if (pendingInstall === "at_panel") {
    pending = ready + " It installs when somebody starts it at the panel.";
  }

  const result = String(state["system.panel_software.last_result"] ?? "");
  const resultVersion = String(state["system.panel_software.last_result_version"] ?? "");
  const message = String(state["system.panel_software.last_result_message"] ?? "");
  const at = String(state["system.panel_software.last_result_at"] ?? "");
  const target = resultVersion ? " to " + resultVersion : "";
  let lastResult: PanelSoftwareDisplay["lastResult"] = null;
  if (result === "installed") {
    lastResult = { ok: true, text: "Updated" + target, at };
  } else if (result === "refused") {
    lastResult = { ok: false, text: "Refused the update" + target + (message ? ": " + message : "."), at };
  } else if (result === "failed") {
    // The panel's own sentence already says it did not install.
    lastResult = { ok: false, text: message || "The update" + target + " did not install.", at };
  }

  return { version, pending, lastResult };
}
