// Pure decision helpers for the Updates view, extracted so the update-flow
// logic (toast direction, completion detection, history labels) is
// unit-testable outside React.

import { compareVersions } from "../api/versionRule";

/** True when version `a` is older than `b`, by the version rule: a release
 *  candidate is older than its release, and a `v` prefix reads as nothing.
 *  False when either is not a version (no ordering claimed). */
export function semverLt(a: string, b: string): boolean {
  return compareVersions(a, b) === -1;
}

/** True when `a` and `b` are one release by the version rule ("0.41.0-rc1"
 *  and "0.41.0rc1" are); when either is not a version, when they are the
 *  same text. The server's settle asks the same (`checker.same_version`). */
export function sameVersion(a: string, b: string): boolean {
  const compared = compareVersions(a, b);
  return compared === null ? a === b : compared === 0;
}

const DEPLOYMENT_LABELS: Record<string, string> = {
  windows_installer: "Windows Installer",
  linux_package: "Linux Package",
  macos_app: "macOS App",
  android_appliance: "Appliance",
  docker: "Docker",
  git_dev: "Development (Git)",
  unknown: "Unknown",
};

/** The Updates page's words for how this system was installed; a type it
 *  does not know is shown as the server sent it. */
export function deploymentLabel(deploymentType: string): string {
  return DEPLOYMENT_LABELS[deploymentType] ?? deploymentType;
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
  if (prevVersion && version && !sameVersion(version, prevVersion)) {
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
 * The server's startup status needs no credential and reports the running
 * version, which is exactly the fact being waited on. A changed version means the swap
 * happened; direction is decided by the same rule the WS path uses.
 */
export function healthProbeOutcome(
  startVersion: string,
  probedVersion: string,
  action: "update" | "rollback" | null,
): CompletionOutcome {
  if (!startVersion || !probedVersion || sameVersion(probedVersion, startVersion)) return null;
  return updateCompletionOutcome(startVersion, "", probedVersion, "", action);
}

/**
 * What the Updates page says when the server restarted on the version it was
 * on: how the update or rollback ended, as the server's start settled it and
 * its Update History row says (`system.update_last_result*`). Without an end
 * to read, only that the version did not change.
 */
export function sameVersionRestartSentence(liveState: Record<string, unknown>): string {
  const end = String(liveState["system.update_last_result"] ?? "success");
  const message = String(liveState["system.update_last_result_message"] ?? "");
  const unsettled = end !== "success" && message !== "";
  if (unsettled) return message + ".";
  return "The server restarted but the version did not change (still v" +
    String(liveState["system.version"] ?? "") + "). Check Update History.";
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
  const version = panelText(state, "version");
  if (!version) return null;
  return { version, pending: pendingSentence(state), lastResult: lastResultDisplay(state) };
}

/** One `system.panel_software.*` value as text, "" when it is not set. */
function panelText(state: Record<string, unknown>, key: string): string {
  return String(state["system.panel_software." + key] ?? "");
}

/** "nobody has touched the panel for N minutes", from the panel's own idle window. */
function untouchedFor(state: Record<string, unknown>): string {
  const idleSeconds = Number(state["system.panel_software.install_idle_seconds"] ?? 0);
  const minutes = Math.max(1, Math.round((idleSeconds > 0 ? idleSeconds : 600) / 60));
  return "nobody has touched the panel for " + minutes + (minutes === 1 ? " minute" : " minutes");
}

function pendingSentence(state: Record<string, unknown>): string | null {
  const pendingInstall = panelText(state, "pending_install");
  const ready = "Panel software " + (panelText(state, "pending_version") || "update") + " is ready.";
  if (pendingInstall === "when_idle") {
    return ready + " It installs by itself once " + untouchedFor(state) + ".";
  }
  if (pendingInstall === "held") {
    return ready + " It waits while the space is in use, then installs once " + untouchedFor(state) + ".";
  }
  if (pendingInstall === "at_panel") {
    return ready + " It installs when somebody starts it at the panel.";
  }
  return null;
}

function lastResultDisplay(state: Record<string, unknown>): PanelSoftwareDisplay["lastResult"] {
  const result = panelText(state, "last_result");
  const message = panelText(state, "last_result_message");
  const at = panelText(state, "last_result_at");
  const resultVersion = panelText(state, "last_result_version");
  const target = resultVersion ? " to " + resultVersion : "";
  if (result === "installed") {
    return { ok: true, text: "Updated" + target, at };
  }
  if (result === "refused") {
    return { ok: false, text: "Refused the update" + target + (message ? ": " + message : "."), at };
  }
  if (result === "failed") {
    // The panel's own sentence already says it did not install.
    return { ok: false, text: message || "The update" + target + " did not install.", at };
  }
  return null;
}
