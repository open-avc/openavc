import { useState, useEffect, useRef, useCallback } from "react";
import { RefreshCw, Download, RotateCcw, CheckCircle, XCircle, Loader, CloudDownload } from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { ConfirmDialog } from "../components/shared/ConfirmDialog";
import { Modal } from "../components/shared/Modal";
import { useConnectionStore } from "../store/connectionStore";
import { showError, showSuccess } from "../store/toastStore";
import * as api from "../api/restClient";
import type { UpdateStatus, UpdateCheckResult, UpdateHistoryEntry } from "../api/restClient";
import { deploymentLabel, updateCompletionOutcome, healthProbeOutcome, historyEntryDisplay, sameVersionRestartSentence } from "./updatesHelpers";
import { cardStyle, primaryBtn, secondaryBtn, sectionTitle } from "./updatesStyles";
import { PanelSoftwareSection } from "./PanelSoftwareSection";
import { UpdateWarnings, hasUpdateWarning } from "./UpdateWarnings";
import { useInstallRestartWait } from "./useInstallRestartWait";
import { InstallWaitFooter } from "./InstallWaitFooter";
import { parseApiError } from "../api/errors";

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

/** The updater's values as the page reads them: the live state key, else the
 *  status read on load, else `none`; `none` alone while the product firmware
 *  carries updates. */
function readUpdater(liveState: Record<string, unknown>, status: UpdateStatus | null) {
  const firmwareManaged = status?.update_owner === "firmware";
  const owned = (key: string, onLoad: unknown, none: string) =>
    firmwareManaged ? none : String(liveState["system." + key] ?? onLoad ?? none);
  return {
    firmwareManaged,
    updateStatus: owned("update_status", undefined, "idle"),
    updateError: owned("update_error", undefined, ""),
    updateAvailable: owned("update_available", undefined, ""),
    stagedVersion: owned("update_staged_version", status?.staged_version, ""),
    // What an Install installs, as the updater decides it.
    installTarget: owned("update_install_version", status?.install_version, ""),
  };
}

export function UpdatesView() {
  const liveState = useConnectionStore((s) => s.liveState);
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [statusLoadFailed, setStatusLoadFailed] = useState(false);
  const [checkResult, setCheckResult] = useState<UpdateCheckResult | null>(null);
  const [history, setHistory] = useState<UpdateHistoryEntry[]>([]);
  const [checking, setChecking] = useState(false);
  const [showProgressModal, setShowProgressModal] = useState(false);
  // The version the Install button named when it was pressed, which the
  // progress dialog's title keeps: the one the updater publishes moves on once
  // the update applies. An update started anywhere else has none.
  const [installingVersion, setInstallingVersion] = useState("");
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
    api.getUpdateStatus().then(setStatus).catch(() => setStatusLoadFailed(true));
    api.getUpdateHistory().then(setHistory).catch(console.error);
  }, []);

  // Track live update state
  const {
    firmwareManaged, updateStatus, updateError, updateAvailable, stagedVersion, installTarget,
  } = readUpdater(liveState, status);
  const updateProgress = Number(liveState["system.update_progress"] ?? 0);
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

  const closeProgress = useCallback(() => {
    setShowProgressModal(false);
    setInstallingVersion("");
  }, []);

  const resolveCompletion = useCallback((rolledBack: boolean, version: string) => {
    if (resolvedRef.current) return;
    resolvedRef.current = true;
    showSuccess(rolledBack ? "Rolled back to v" + version : "Updated to v" + version);
    closeProgress();
    actionRef.current = null;
    startVersionRef.current = "";
    // Best-effort: after a restart this browser's session token is gone, so
    // these 401 and the app bounces to the login screen. That's the correct
    // outcome and not an error worth logging.
    api.getUpdateStatus().then(setStatus).catch(() => {});
    api.getUpdateHistory().then(setHistory).catch(() => {});
  }, [closeProgress]);

  // Completion detection that does NOT depend on the WebSocket (see
  // useInstallRestartWait), which also keeps a failed start to show.
  const settleOnVersion = useCallback((probed: string) => {
    const outcome = healthProbeOutcome(startVersionRef.current, probed, actionRef.current);
    if (outcome === "updated" || outcome === "rolled_back") {
      resolveCompletion(outcome === "rolled_back", probed);
    }
  }, [resolveCompletion]);
  const { startFailure, startingAgain } = useInstallRestartWait(showProgressModal, settleOnVersion);

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
      closeProgress();
      actionRef.current = null;
      startVersionRef.current = "";
      showError(sameVersionRestartSentence(useConnectionStore.getState().liveState, currentVersion));
      api.getUpdateStatus().then(setStatus).catch(() => {});
      api.getUpdateHistory().then(setHistory).catch(() => {});
      return;
    }
    // Shared with the health poll, so whichever notices first reports it once.
    resolveCompletion(outcome === "rolled_back", currentVersion);
  }, [currentVersion, updateStatus, showProgressModal, resolveCompletion, closeProgress]);

  // Detect error state
  useEffect(() => {
    if (updateStatus === "error" && updateError) {
      closeProgress();
    }
  }, [updateStatus, updateError, closeProgress]);

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
      if (result.error) {
        // A check that couldn't reach GitHub returns 200 with an error, and
        // "no update available" is not the same claim as "you're up to date".
        // An isolated system would otherwise be told it was current forever.
        showError("Could not check for updates: " + result.error);
      } else if (!result.update_available && result.update_owner !== "firmware") {
        showSuccess("You're up to date.");
      }
      // Refresh status
      api.getUpdateStatus().then(setStatus).catch(console.error);
    } catch (e) {
      showError("Update check failed: " + parseApiError(e));
    } finally {
      setChecking(false);
    }
  };

  const handleApply = async () => {
    actionRef.current = "update";
    startVersionRef.current = currentVersion;
    resolvedRef.current = false;
    setInstallingVersion(installTarget);
    try {
      const result = await api.applyUpdate();
      if (!result.success) {
        actionRef.current = null;
        setInstallingVersion("");
        showError(result.error ?? "Update failed");
      }
    } catch (e) {
      actionRef.current = null;
      setInstallingVersion("");
      showError("Failed to start update: " + parseApiError(e));
    }
  };

  const handleRollback = useCallback(async () => {
    setShowRollbackConfirm(false);
    setInstallingVersion("");
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
      showError("Rollback failed: " + parseApiError(e));
    }
  }, []);

  const canSelfUpdate = status?.can_self_update ?? false;
  const changelog = checkResult?.changelog ?? "";
  const hasUpdate = !!updateAvailable;
  const hasStaged = !!stagedVersion;
  const hasWarning = hasUpdateWarning(status);

  return (
    <ViewContainer title="System Updates">
      <div style={{ maxWidth: 700 }}>
        {/* Version info */}
        <div style={{ ...cardStyle, marginBottom: "var(--space-xl)" }}>
          <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "var(--space-sm) var(--space-xl)", fontSize: "var(--font-size-sm)" }}>
            <span style={{ color: "var(--text-secondary)" }}>Current version</span>
            <span style={{ fontWeight: 600 }}>{"v" + currentVersion}</span>

            {hasUpdate && <>
              <span style={{ color: "var(--text-secondary)" }}>Available</span>
              <span style={{ fontWeight: 600, color: "var(--accent)" }}>{"v" + updateAvailable}</span>
            </>}

            {hasStaged && <>
              <span style={{ color: "var(--text-secondary)" }}>Staged from cloud</span>
              <span style={{ fontWeight: 600, color: "var(--accent)" }}>{"v" + stagedVersion}</span>
            </>}

            {!firmwareManaged && <>
              <span style={{ color: "var(--text-secondary)" }}>Channel</span>
              <span>{status?.update_channel ?? "stable"}</span>
            </>}
            {firmwareManaged && status?.firmware_version && <>
              <span style={{ color: "var(--text-secondary)" }}>Product firmware</span>
              <span>{status.firmware_version}</span>
            </>}

            <span style={{ color: "var(--text-secondary)" }}>Deployment</span>
            <span>{firmwareManaged ? "Built into product firmware" : deploymentLabel(status?.deployment_type ?? "")}</span>
          </div>
        </div>

        {statusLoadFailed && !status && (
          <div style={{ ...cardStyle, marginBottom: "var(--space-xl)" }}>
            Update settings could not be loaded. Reload this page to try again.
          </div>
        )}
        {firmwareManaged && (
          <div style={{ ...cardStyle, marginBottom: "var(--space-xl)" }}>
            <div style={{ fontWeight: 500, marginBottom: "var(--space-sm)" }}>Product firmware updates</div>
            <div style={{ fontSize: "var(--font-size-sm)", lineHeight: 1.6 }}>{status?.update_guidance}</div>
          </div>
        )}

        {/* Cloud-staged update */}
        {hasStaged && (
          <div style={{ ...cardStyle, marginBottom: "var(--space-xl)", borderColor: "var(--accent-bg)", display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
            <CloudDownload size={20} style={{ color: "var(--accent)", flexShrink: 0 }} />
            <div>
              <div style={{ fontWeight: 500, fontSize: "var(--font-size-sm)" }}>{"Update to v" + stagedVersion + " staged from the cloud"}</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                It's ready to install whenever you are. Installing restarts the server.
              </div>
            </div>
          </div>
        )}

        <UpdateWarnings status={status} currentVersion={currentVersion} />

        {/* Up to date message. A failed check disqualifies it the same way an
            update error does: the status still reads "no update available",
            but nothing has confirmed that, so the card below says so instead. */}
        {status && !firmwareManaged && !hasUpdate && !hasStaged && !hasWarning && updateStatus === "idle" && !updateError && !checkResult?.error && (
          <div style={{ ...cardStyle, marginBottom: "var(--space-xl)", display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
            <CheckCircle size={20} style={{ color: "var(--color-success)", flexShrink: 0 }} />
            <div>
              <div style={{ fontWeight: 500, fontSize: "var(--font-size-sm)" }}>You're up to date</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>{"Running OpenAVC v" + currentVersion}</div>
            </div>
          </div>
        )}

        {/* Error message */}
        {updateError && updateStatus === "error" && (
          <div style={{ ...cardStyle, marginBottom: "var(--space-xl)", borderColor: "rgba(239,68,68,0.3)", display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
            <XCircle size={20} style={{ color: "var(--color-error)", flexShrink: 0 }} />
            <div>
              <div style={{ fontWeight: 500, fontSize: "var(--font-size-sm)", color: "var(--color-error)" }}>Update error</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>{updateError}</div>
            </div>
          </div>
        )}

        {/* Check failed: say so, and say that a file-based update is possible */}
        {checkResult?.error && (
          <div style={{ ...cardStyle, marginBottom: "var(--space-xl)", borderColor: "rgba(239,68,68,0.3)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
              <XCircle size={20} style={{ color: "var(--color-error)", flexShrink: 0 }} />
              <div>
                <div style={{ fontWeight: 500, fontSize: "var(--font-size-sm)", color: "var(--color-error)" }}>Could not check for updates</div>
                <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>{checkResult.error}</div>
              </div>
            </div>
            {checkResult.offline_instructions && (
              <div style={{ fontSize: "var(--font-size-sm)", marginTop: "var(--space-md)", lineHeight: 1.6 }}>
                {checkResult.offline_instructions}
              </div>
            )}
          </div>
        )}

        {/* Changelog */}
        {hasUpdate && changelog && (
          <div style={{ marginBottom: "var(--space-xl)" }}>
            <h3 style={sectionTitle}>Changelog</h3>
            <div style={{ ...cardStyle, fontSize: "var(--font-size-sm)", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
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
        {status && !firmwareManaged && <div style={{ display: "flex", gap: "var(--space-md)", marginBottom: "var(--space-xl)" }}>
          <button
            style={{ ...secondaryBtn, opacity: checking ? 0.7 : 1 }}
            onClick={handleCheck}
            disabled={checking}
          >
            {checking ? <Loader size={14} style={{ animation: "spin 1s linear infinite" }} /> : <RefreshCw size={14} />}
            <span>{checking ? "Checking..." : "Check for Updates"}</span>
          </button>

          {(hasUpdate || hasStaged) && canSelfUpdate && installTarget && (
            <button
              style={primaryBtn}
              onClick={handleApply}
              disabled={updateStatus !== "idle" && updateStatus !== "error"}
            >
              <Download size={14} />
              <span>{"Install v" + installTarget}</span>
            </button>
          )}
        </div>}

        <PanelSoftwareSection liveState={liveState} />

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
                    {display.succeeded ? (
                      <CheckCircle size={14} style={{ color: "var(--color-success)", flexShrink: 0 }} />
                    ) : (
                      <XCircle size={14} style={{ color: "var(--color-error)", flexShrink: 0 }} />
                    )}
                    <span style={{ fontWeight: 500 }}>
                      {display.label}
                    </span>
                    {display.isRollback && (
                      <span style={{
                        fontSize: 10,
                        fontWeight: 600,
                        padding: "1px 6px",
                        borderRadius: 3,
                        textTransform: "uppercase",
                        letterSpacing: "0.5px",
                        background: "rgba(59,130,246,0.15)",
                        color: "#3b82f6",
                      }}>
                        rollback
                      </span>
                    )}
                    <span style={{ color: "var(--text-muted)", fontSize: 12, marginLeft: "auto" }}>
                      {new Date(entry.timestamp).toLocaleDateString()}
                    </span>
                    <span style={{
                      fontSize: 10,
                      fontWeight: 600,
                      padding: "1px 6px",
                      borderRadius: 3,
                      textTransform: "uppercase",
                      letterSpacing: "0.5px",
                      background: display.succeeded
                        ? "rgba(76,175,80,0.15)"
                        : "rgba(239,68,68,0.15)",
                      color: display.succeeded
                        ? "var(--color-success)"
                        : "var(--color-error)",
                    }}>
                      {display.statusLabel}
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
                <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
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
            <div style={{ fontSize: "var(--font-size-lg)", fontWeight: 600, marginBottom: "var(--space-lg)" }}>
              {installingVersion ? "Installing OpenAVC v" + installingVersion : "Updating OpenAVC"}
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
              <div style={{ height: 4, background: "var(--bg-hover)", borderRadius: 2, overflow: "hidden", marginBottom: "var(--space-lg)" }}>
                <div style={{
                  height: "100%",
                  width: updateProgress + "%",
                  background: "var(--accent-bg)",
                  transition: "width 0.3s ease",
                  borderRadius: 2,
                }} />
              </div>
            )}

            <InstallWaitFooter
              startFailure={startFailure}
              startingAgain={startingAgain}
              watchdogTripped={watchdogTripped}
              errored={updateStatus === "error"}
              onClose={closeProgress}
            />

            {/* Error in modal */}
            {updateStatus === "error" && (
              <div style={{ marginTop: "var(--space-lg)" }}>
                <div style={{ fontSize: "var(--font-size-sm)", color: "var(--color-error)", marginBottom: "var(--space-sm)" }}>
                  {updateError || "An error occurred during the update."}
                </div>
                <button style={secondaryBtn} onClick={closeProgress}>
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
