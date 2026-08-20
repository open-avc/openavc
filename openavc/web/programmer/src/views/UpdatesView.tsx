import { useState, useEffect, useRef, useCallback } from "react";
import { RefreshCw, Download, RotateCcw, CheckCircle, XCircle, Loader, CloudDownload } from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { ConfirmDialog } from "../components/shared/ConfirmDialog";
import { Modal } from "../components/shared/Modal";
import { useConnectionStore } from "../store/connectionStore";
import { showError, showSuccess } from "../store/toastStore";
import * as api from "../api/restClient";
import type { UpdateStatus, UpdateCheckResult, UpdateHistoryEntry } from "../api/restClient";
import { updateCompletionOutcome, healthProbeOutcome, historyEntryDisplay } from "./updatesHelpers";
import { healthProbeUrl } from "../components/shared/restartPollHelpers";

// How often to ask /api/health whether the server is back on a new version.
// Fast enough that the dialog closes promptly after a swap, slow enough that a
// server rebinding its port isn't hammered while it starts.
const HEALTH_POLL_INTERVAL_MS = 2000;

const cardStyle: React.CSSProperties = {
  background: "var(--bg-surface)",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  padding: "var(--space-lg)",
};

const sectionTitle: React.CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  textTransform: "uppercase",
  letterSpacing: "var(--tracking-wide)",
  fontWeight: "var(--font-weight-semibold)",
  marginBottom: "var(--space-md)",
};

const btnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "var(--space-xs)",
  padding: "var(--space-sm) var(--space-lg)",
  borderRadius: "var(--border-radius)",
  fontSize: "var(--font-size-sm)",
  fontWeight: "var(--font-weight-medium)",
  cursor: "pointer",
  transition: "all var(--transition-fast)",
};

const primaryBtn: React.CSSProperties = {
  ...btnStyle,
  background: "var(--accent-bg)",
  color: "#fff",
  border: "1px solid var(--accent)",
};

const secondaryBtn: React.CSSProperties = {
  ...btnStyle,
  background: "transparent",
  color: "var(--text-primary)",
  border: "1px solid var(--border-color)",
};

type UpdateStep = "backup" | "download" | "verify" | "apply" | "restart";

const STEPS: { id: UpdateStep; label: string }[] = [
  { id: "backup", label: "Creating backup" },
  { id: "download", label: "Downloading update" },
  { id: "verify", label: "Verifying checksum" },
  { id: "apply", label: "Applying update" },
  { id: "restart", label: "Restarting server" },
];

function statusToStep(status: string): UpdateStep | null {
  const map: Record<string, UpdateStep> = {
    backing_up: "backup",
    downloading: "download",
    verifying: "verify",
    applying: "apply",
    restarting: "restart",
  };
  return map[status] ?? null;
}

export function UpdatesView() {
  const liveState = useConnectionStore((s) => s.liveState);
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [checkResult, setCheckResult] = useState<UpdateCheckResult | null>(null);
  const [history, setHistory] = useState<UpdateHistoryEntry[]>([]);
  const [checking, setChecking] = useState(false);
  const [showProgressModal, setShowProgressModal] = useState(false);
  const [showRollbackConfirm, setShowRollbackConfirm] = useState(false);
  const [watchdogTripped, setWatchdogTripped] = useState(false);
  const prevVersionRef = useRef<string>("");
  const prevStatusRef = useRef<string>("");
  // Which action this view started, so the completion toast can tell a
  // rollback from an update (semver direction is the fallback for actions
  // started elsewhere, e.g. cloud-initiated).
  const actionRef = useRef<"update" | "rollback" | null>(null);
  // The version running when this flow started, compared against /api/health
  // to notice the swap without needing the (now unauthenticated) WebSocket.
  const startVersionRef = useRef<string>("");
  // Both completion paths — WS snapshot and health poll — funnel through
  // resolveCompletion, so whichever arrives first wins and the other is a
  // no-op rather than a second toast.
  const resolvedRef = useRef(false);

  // Load initial data
  useEffect(() => {
    api.getUpdateStatus().then(setStatus).catch(console.error);
    api.getUpdateHistory().then(setHistory).catch(console.error);
  }, []);

  // Track live update state
  const updateStatus = String(liveState["system.update_status"] ?? "idle");
  const updateProgress = Number(liveState["system.update_progress"] ?? 0);
  const updateError = String(liveState["system.update_error"] ?? "");
  const updateAvailable = String(liveState["system.update_available"] ?? "");
  const stagedVersion = String(liveState["system.update_staged_version"] ?? status?.staged_version ?? "");
  const currentVersion = String(liveState["system.version"] ?? status?.current_version ?? "");

  // Show progress modal when update is in progress
  useEffect(() => {
    const active = ["backing_up", "downloading", "verifying", "applying", "restarting"].includes(updateStatus);
    if (!active) return;
    // A cloud-initiated update opens this modal without going through
    // handleApply, so anchor the comparison version here too.
    if (!startVersionRef.current && currentVersion) startVersionRef.current = currentVersion;
    setShowProgressModal(true);
  }, [updateStatus, currentVersion]);

  const resolveCompletion = useCallback((rolledBack: boolean, version: string) => {
    if (resolvedRef.current) return;
    resolvedRef.current = true;
    showSuccess(rolledBack ? "Rolled back to v" + version : "Updated to v" + version);
    setShowProgressModal(false);
    actionRef.current = null;
    startVersionRef.current = "";
    // Best-effort: after a restart this browser's session token is gone, so
    // these 401 and the app bounces to the login screen. That's the correct
    // outcome and not an error worth logging.
    api.getUpdateStatus().then(setStatus).catch(() => {});
    api.getUpdateHistory().then(setHistory).catch(() => {});
  }, []);

  // Completion detection that does NOT depend on the WebSocket. See
  // healthProbeOutcome: the restart being waited on invalidates this browser's
  // session token, so the Programmer socket reconnects refused and its
  // snapshot never arrives. /api/health carries no credential and reports the
  // running version, which is the fact this dialog needs.
  useEffect(() => {
    if (!showProgressModal) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      let probed = "";
      try {
        const res = await fetch(healthProbeUrl(window.location.origin), { cache: "no-store" });
        if (!res.ok) return;
        probed = String((await res.json())?.version ?? "");
      } catch {
        // Server is mid-restart and not answering yet. Keep polling; the
        // watchdog below is the backstop if it never comes back.
        return;
      }
      if (cancelled || !probed) return;
      const outcome = healthProbeOutcome(startVersionRef.current, probed, actionRef.current);
      if (outcome === "updated" || outcome === "rolled_back") {
        resolveCompletion(outcome === "rolled_back", probed);
      }
    }, HEALTH_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [showProgressModal, resolveCompletion]);

  // Detect completion after the restart (the WebSocket reconnect snapshot
  // delivers the new version + update_status reset to idle in one shot):
  // version change = update or rollback finished; restarting -> idle with no
  // version change = the restart happened but nothing was applied.
  useEffect(() => {
    const prevVersion = prevVersionRef.current;
    const prevStatus = prevStatusRef.current;
    prevVersionRef.current = currentVersion;
    prevStatusRef.current = updateStatus;

    const outcome = updateCompletionOutcome(
      prevVersion, prevStatus, currentVersion, updateStatus, actionRef.current,
    );
    if (!outcome) return;

    if (outcome === "same_version_restart") {
      if (!showProgressModal || resolvedRef.current) return;
      resolvedRef.current = true;
      setShowProgressModal(false);
      actionRef.current = null;
      startVersionRef.current = "";
      showError(
        "The server restarted but the version did not change (still v" + currentVersion +
        "). The update may not have applied. Check Update History.",
      );
      api.getUpdateStatus().then(setStatus).catch(() => {});
      api.getUpdateHistory().then(setHistory).catch(() => {});
      return;
    }
    // Shared with the health poll, so whichever notices first reports it once.
    resolveCompletion(outcome === "rolled_back", currentVersion);
  }, [currentVersion, updateStatus, showProgressModal, resolveCompletion]);

  // Detect error state
  useEffect(() => {
    if (updateStatus === "error" && updateError) {
      setShowProgressModal(false);
    }
  }, [updateStatus, updateError]);

  // Watchdog: if nothing moves (no status/progress change) for two minutes
  // while the modal is up — e.g. the server never comes back after the
  // restart — surface guidance and a way out instead of hanging forever.
  useEffect(() => {
    if (!showProgressModal) {
      setWatchdogTripped(false);
      return;
    }
    setWatchdogTripped(false);
    const timer = window.setTimeout(() => setWatchdogTripped(true), 120_000);
    return () => window.clearTimeout(timer);
  }, [showProgressModal, updateStatus, updateProgress]);

  const handleCheck = async () => {
    setChecking(true);
    try {
      const result = await api.checkForUpdates();
      setCheckResult(result);
      if (!result.update_available) {
        showSuccess("You're up to date.");
      }
      // Refresh status
      api.getUpdateStatus().then(setStatus).catch(console.error);
    } catch (e) {
      showError("Update check failed: " + String(e));
    } finally {
      setChecking(false);
    }
  };

  const handleApply = async () => {
    actionRef.current = "update";
    startVersionRef.current = currentVersion;
    resolvedRef.current = false;
    try {
      const result = await api.applyUpdate();
      if (!result.success) {
        actionRef.current = null;
        showError(result.error ?? "Update failed");
      }
    } catch (e) {
      actionRef.current = null;
      showError("Failed to start update: " + String(e));
    }
  };

  const handleRollback = useCallback(async () => {
    setShowRollbackConfirm(false);
    actionRef.current = "rollback";
    startVersionRef.current = currentVersion;
    resolvedRef.current = false;
    try {
      const result = await api.rollbackUpdate();
      if (result.success) {
        showSuccess(result.message ?? "Rollback initiated");
      } else {
        actionRef.current = null;
        showError(result.error ?? "Rollback failed");
      }
    } catch (e) {
      actionRef.current = null;
      showError("Rollback failed: " + String(e));
    }
  }, []);

  const deploymentLabel = (dt: string) => {
    const map: Record<string, string> = {
      windows_installer: "Windows Installer",
      linux_package: "Linux Package",
      docker: "Docker",
      git_dev: "Development (Git)",
      unknown: "Unknown",
    };
    return map[dt] ?? dt;
  };

  const canSelfUpdate = status?.can_self_update ?? false;
  const changelog = checkResult?.changelog ?? "";
  const hasUpdate = !!updateAvailable;
  const hasStaged = !!stagedVersion;
  // apply_update consumes a cloud-staged update before checking GitHub, so
  // the staged version is what an Install actually installs.
  const installTarget = stagedVersion || updateAvailable;

  return (
    <ViewContainer title="System Updates">
      <div style={{ maxWidth: 700 }}>
        {/* Version info */}
        <div style={{ ...cardStyle, marginBottom: "var(--space-xl)" }}>
          <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "var(--space-sm) var(--space-xl)", fontSize: "var(--font-size-sm)" }}>
            <span style={{ color: "var(--text-secondary)" }}>Current version</span>
            <span style={{ fontWeight: "var(--font-weight-semibold)" }}>{"v" + currentVersion}</span>

            {hasUpdate && <>
              <span style={{ color: "var(--text-secondary)" }}>Available</span>
              <span style={{ fontWeight: "var(--font-weight-semibold)", color: "var(--accent)" }}>{"v" + updateAvailable}</span>
            </>}

            {hasStaged && <>
              <span style={{ color: "var(--text-secondary)" }}>Staged from cloud</span>
              <span style={{ fontWeight: "var(--font-weight-semibold)", color: "var(--accent)" }}>{"v" + stagedVersion}</span>
            </>}

            <span style={{ color: "var(--text-secondary)" }}>Channel</span>
            <span>{status?.update_channel ?? "stable"}</span>

            <span style={{ color: "var(--text-secondary)" }}>Deployment</span>
            <span>{deploymentLabel(status?.deployment_type ?? "")}</span>
          </div>
        </div>

        {/* Cloud-staged update */}
        {hasStaged && (
          <div style={{ ...cardStyle, marginBottom: "var(--space-xl)", borderColor: "var(--accent-bg)", display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
            <CloudDownload size={20} style={{ color: "var(--accent)", flexShrink: 0 }} />
            <div>
              <div style={{ fontWeight: "var(--font-weight-medium)", fontSize: "var(--font-size-sm)" }}>{"Update to v" + stagedVersion + " staged from the cloud"}</div>
              <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", marginTop: "var(--space-2xs)" }}>
                It's ready to install whenever you are. Installing restarts the server.
              </div>
            </div>
          </div>
        )}

        {/* Up to date message */}
        {!hasUpdate && !hasStaged && updateStatus === "idle" && !updateError && (
          <div style={{ ...cardStyle, marginBottom: "var(--space-xl)", display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
            <CheckCircle size={20} style={{ color: "var(--color-success)", flexShrink: 0 }} />
            <div>
              <div style={{ fontWeight: "var(--font-weight-medium)", fontSize: "var(--font-size-sm)" }}>You're up to date</div>
              <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", marginTop: "var(--space-2xs)" }}>{"Running OpenAVC v" + currentVersion}</div>
            </div>
          </div>
        )}

        {/* Error message */}
        {updateError && updateStatus === "error" && (
          <div style={{ ...cardStyle, marginBottom: "var(--space-xl)", borderColor: "rgba(239,68,68,0.3)", display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
            <XCircle size={20} style={{ color: "var(--color-error)", flexShrink: 0 }} />
            <div>
              <div style={{ fontWeight: "var(--font-weight-medium)", fontSize: "var(--font-size-sm)", color: "var(--color-error)" }}>Update error</div>
              <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", marginTop: "var(--space-2xs)" }}>{updateError}</div>
            </div>
          </div>
        )}

        {/* Changelog */}
        {hasUpdate && changelog && (
          <div style={{ marginBottom: "var(--space-xl)" }}>
            <h3 style={sectionTitle}>Changelog</h3>
            <div style={{ ...cardStyle, fontSize: "var(--font-size-sm)", lineHeight: "var(--line-relaxed)", whiteSpace: "pre-wrap" }}>
              {changelog}
            </div>
          </div>
        )}

        {/* Instructions for non-self-updating deployments */}
        {hasUpdate && !canSelfUpdate && (
          <div style={{ ...cardStyle, marginBottom: "var(--space-xl)", borderColor: "var(--accent-bg)", background: "var(--color-info-bg)" }}>
            <div style={{ fontSize: "var(--font-size-sm)" }}>
              {checkResult?.instructions ?? ("A new version is available. Update to v" + updateAvailable + " using your deployment method.")}
            </div>
          </div>
        )}

        {/* Action buttons */}
        <div style={{ display: "flex", gap: "var(--space-md)", marginBottom: "var(--space-xl)" }}>
          <button
            style={{ ...secondaryBtn, opacity: checking ? 0.7 : 1 }}
            onClick={handleCheck}
            disabled={checking}
          >
            {checking ? <Loader size={14} style={{ animation: "spin 1s linear infinite" }} /> : <RefreshCw size={14} />}
            <span>{checking ? "Checking..." : "Check for Updates"}</span>
          </button>

          {(hasUpdate || hasStaged) && canSelfUpdate && (
            <button
              style={primaryBtn}
              onClick={handleApply}
              disabled={updateStatus !== "idle" && updateStatus !== "error"}
            >
              <Download size={14} />
              <span>{"Install v" + installTarget}</span>
            </button>
          )}
        </div>

        {/* Update History */}
        {history.length > 0 && (
          <div style={{ marginBottom: "var(--space-xl)" }}>
            <h3 style={sectionTitle}>Update History</h3>
            <div style={{ ...cardStyle, padding: 0, overflow: "hidden" }}>
              {history.map((entry, i) => {
                const display = historyEntryDisplay(entry);
                return (
                  <div
                    key={i}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--space-md)",
                      padding: "var(--space-sm) var(--space-md)",
                      borderTop: i > 0 ? "1px solid var(--border-color)" : undefined,
                      fontSize: "var(--font-size-sm)",
                    }}
                  >
                    {entry.status === "success" || entry.status === "applied" ? (
                      <CheckCircle size={14} style={{ color: "var(--color-success)", flexShrink: 0 }} />
                    ) : (
                      <XCircle size={14} style={{ color: "var(--color-error)", flexShrink: 0 }} />
                    )}
                    <span style={{ fontWeight: "var(--font-weight-medium)" }}>
                      {display.label}
                    </span>
                    {display.isRollback && (
                      <span style={{
                        fontSize: "var(--font-size-2xs)",
                        fontWeight: "var(--font-weight-semibold)",
                        padding: "var(--space-2xs) var(--space-sm)",
                        borderRadius: "var(--border-radius)",
                        textTransform: "uppercase",
                        letterSpacing: "var(--tracking-wide)",
                        background: "rgba(59,130,246,0.15)",
                        color: "#3b82f6",
                      }}>
                        rollback
                      </span>
                    )}
                    <span style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)", marginLeft: "auto" }}>
                      {new Date(entry.timestamp).toLocaleDateString()}
                    </span>
                    <span style={{
                      fontSize: "var(--font-size-2xs)",
                      fontWeight: "var(--font-weight-semibold)",
                      padding: "var(--space-2xs) var(--space-sm)",
                      borderRadius: "var(--border-radius)",
                      textTransform: "uppercase",
                      letterSpacing: "var(--tracking-wide)",
                      background: entry.status === "success" || entry.status === "applied"
                        ? "rgba(76,175,80,0.15)"
                        : "rgba(239,68,68,0.15)",
                      color: entry.status === "success" || entry.status === "applied"
                        ? "var(--color-success)"
                        : "var(--color-error)",
                    }}>
                      {entry.status}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Rollback */}
        {status?.rollback_available && (
          <div style={{ marginBottom: "var(--space-xl)" }}>
            <h3 style={sectionTitle}>Rollback</h3>
            <div style={{ ...cardStyle, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div>
                <div style={{ fontSize: "var(--font-size-sm)" }}>
                  {"Previous version: v" + (status.rollback_version || "unknown")}
                </div>
                <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", marginTop: "var(--space-2xs)" }}>
                  Reverts the application code. Your projects and configuration are preserved.
                </div>
              </div>
              <button
                style={secondaryBtn}
                onClick={() => setShowRollbackConfirm(true)}
                disabled={updateStatus !== "idle" && updateStatus !== "error"}
              >
                <RotateCcw size={14} />
                <span>{"Rollback to v" + (status.rollback_version || "?")}</span>
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Progress Modal */}
      {showProgressModal && (
        <Modal
          label="Update Progress"
          // No dismiss handler on purpose: an update in progress is not
          // something a stray click or an Escape should walk away from. The
          // states that are safe to leave offer their own Close button.
          overlayStyle={{ background: "rgba(0,0,0,0.7)" }}
          panelStyle={{
            padding: "var(--space-xl)",
            width: 400,
            boxShadow: "var(--shadow-md)",
          }}
        >
            <div style={{ fontSize: "var(--font-size-lg)", fontWeight: "var(--font-weight-semibold)", marginBottom: "var(--space-lg)" }}>
              {installTarget ? "Installing OpenAVC v" + installTarget : "Updating OpenAVC"}
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)", marginBottom: "var(--space-lg)" }}>
              {STEPS.map((step) => {
                const currentStep = statusToStep(updateStatus);
                const stepIndex = STEPS.findIndex(s => s.id === step.id);
                const currentIndex = currentStep ? STEPS.findIndex(s => s.id === currentStep) : -1;
                const isDone = stepIndex < currentIndex;
                const isActive = step.id === currentStep;
                const isFailed = updateStatus === "error" && isActive;
                const isPending = stepIndex > currentIndex && !isFailed;

                return (
                  <div key={step.id} style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", fontSize: "var(--font-size-sm)" }}>
                    {isDone && <CheckCircle size={16} style={{ color: "var(--color-success)", flexShrink: 0 }} />}
                    {isActive && !isFailed && <Loader size={16} style={{ color: "var(--accent)", flexShrink: 0, animation: "spin 1s linear infinite" }} />}
                    {isFailed && <XCircle size={16} style={{ color: "var(--color-error)", flexShrink: 0 }} />}
                    {isPending && <div style={{ width: 16, height: 16, borderRadius: "50%", border: "2px solid var(--border-color)", flexShrink: 0 }} />}
                    <span style={{ color: isFailed ? "var(--color-error)" : isPending ? "var(--text-muted)" : "var(--text-primary)", fontWeight: isActive ? 500 : 400 }}>
                      {step.label}
                      {isActive && step.id === "download" && updateProgress > 0 && (" (" + updateProgress + "%)")}
                    </span>
                  </div>
                );
              })}
            </div>

            {/* Progress bar */}
            {updateStatus === "downloading" && (
              <div style={{ height: 4, background: "var(--bg-hover)", borderRadius: "var(--radius-sm)", overflow: "hidden", marginBottom: "var(--space-lg)" }}>
                <div style={{
                  height: "100%",
                  width: updateProgress + "%",
                  background: "var(--accent-bg)",
                  transition: "width 0.3s ease",
                  borderRadius: "var(--radius-sm)",
                }} />
              </div>
            )}

            {watchdogTripped && updateStatus !== "error" ? (
              <div>
                <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
                  This is taking longer than expected. The server may still be coming back up.
                  If this dialog doesn't close within a few minutes, check the server logs.
                  It's safe to close this dialog; any update in progress continues on the server.
                </div>
                <button
                  style={secondaryBtn}
                  onClick={() => setShowProgressModal(false)}
                >
                  Close
                </button>
              </div>
            ) : (
              <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", textAlign: "center" }}>
                Do not close this window or power off the system.
              </div>
            )}

            {/* Error in modal */}
            {updateStatus === "error" && (
              <div style={{ marginTop: "var(--space-lg)" }}>
                <div style={{ fontSize: "var(--font-size-sm)", color: "var(--color-error)", marginBottom: "var(--space-sm)" }}>
                  {updateError || "An error occurred during the update."}
                </div>
                <button style={secondaryBtn} onClick={() => setShowProgressModal(false)}>
                  Close
                </button>
              </div>
            )}
        </Modal>
      )}

      {/* CSS keyframe for spinner */}
      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>

      {showRollbackConfirm && (
        <ConfirmDialog
          title="Rollback"
          message="Roll back to the previous version? The server will restart."
          confirmLabel="Rollback"
          onConfirm={handleRollback}
          onCancel={() => setShowRollbackConfirm(false)}
        />
      )}
    </ViewContainer>
  );
}
