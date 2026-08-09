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
  rollback?: boolean;
}

/**
 * Display label for an update-history entry. Rollback entries record the
 * real target version plus a rollback flag; legacy entries carried the
 * literal string "rollback" in to_version.
 */
export function historyEntryDisplay(entry: HistoryEntryLike): { label: string; isRollback: boolean } {
  const legacy = entry.to_version === "rollback";
  const isRollback = !!entry.rollback || legacy;
  const target = legacy || !entry.to_version
    ? "previous version"
    : "v" + entry.to_version;
  return {
    label: "v" + entry.from_version + " → " + (isRollback ? target : "v" + entry.to_version),
    isRollback,
  };
}
