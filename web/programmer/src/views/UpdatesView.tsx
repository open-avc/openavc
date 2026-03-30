import { useState, useEffect, useRef } from "react";
import { RefreshCw, Download, RotateCcw, CheckCircle, XCircle, Loader, ArrowUpCircle } from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { useConnectionStore } from "../store/connectionStore";
import { showError, showSuccess } from "../store/toastStore";
import * as api from "../api/restClient";
import type { UpdateStatus, UpdateCheckResult, UpdateHistoryEntry } from "../api/restClient";

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
  letterSpacing: "0.5px",
  fontWeight: 600,
  marginBottom: "var(--space-md)",
};

const btnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "var(--space-xs)",
  padding: "var(--space-sm) var(--space-lg)",
  borderRadius: "var(--border-radius)",
  fontSize: "var(--font-size-sm)",
  fontWeight: 500,
  cursor: "pointer",
  transition: "all var(--transition-fast)",
};

const primaryBtn: React.CSSProperties = {
  ...btnStyle,
  background: "var(--accent)",
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
  const prevVersionRef = useRef<string>("");

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
  const currentVersion = String(liveState["system.version"] ?? status?.current_version ?? "");

  // Show progress modal when update is in progress
  useEffect(() => {
    const active = ["backing_up", "downloading", "verifying", "applying", "restarting"].includes(updateStatus);
    if (active) setShowProgressModal(true);
  }, [updateStatus]);

  // Detect version change after update (WebSocket reconnect delivers new version)
  useEffect(() => {
    if (prevVersionRef.current && currentVersion && currentVersion !== prevVersionRef.current) {
      showSuccess("Updated to v" + currentVersion);
      setShowProgressModal(false);
      // Refresh status and history
      api.getUpdateStatus().then(setStatus).catch(console.error);
      api.getUpdateHistory().then(setHistory).catch(console.error);
    }
    prevVersionRef.current = currentVersion;
  }, [currentVersion]);

  // Detect error state
  useEffect(() => {
    if (updateStatus === "error" && updateError) {
      setShowProgressModal(false);
    }
  }, [updateStatus, updateError]);

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
    try {
      const result = await api.applyUpdate();
      if (!result.success) {
        showError(result.error ?? "Update failed");
      }
    } catch (e) {
      showError("Failed to start update: " + String(e));
    }
  };

  const handleRollback = async () => {
    if (!confirm("Roll back to the previous version? The server will restart.")) return;
    try {
      const result = await api.rollbackUpdate();
      if (result.success) {
        showSuccess(result.message ?? "Rollback initiated");
      } else {
        showError(result.error ?? "Rollback failed");
      }
    } catch (e) {
      showError("Rollback failed: " + String(e));
    }
  };

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

            <span style={{ color: "var(--text-secondary)" }}>Channel</span>
            <span>{status?.update_channel ?? "stable"}</span>

            <span style={{ color: "var(--text-secondary)" }}>Deployment</span>
            <span>{deploymentLabel(status?.deployment_type ?? "")}</span>
          </div>
        </div>

        {/* Up to date message */}
        {!hasUpdate && updateStatus === "idle" && !updateError && (
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
          <div style={{ ...cardStyle, marginBottom: "var(--space-xl)", borderColor: "var(--accent)", background: "var(--color-info-bg)" }}>
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

          {hasUpdate && canSelfUpdate && (
            <button
              style={primaryBtn}
              onClick={handleApply}
              disabled={updateStatus !== "idle" && updateStatus !== "error"}
            >
              <Download size={14} />
              <span>{"Install v" + updateAvailable}</span>
            </button>
          )}
        </div>

        {/* Update History */}
        {history.length > 0 && (
          <div style={{ marginBottom: "var(--space-xl)" }}>
            <h3 style={sectionTitle}>Update History</h3>
            <div style={{ ...cardStyle, padding: 0, overflow: "hidden" }}>
              {history.map((entry, i) => (
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
                  <span style={{ fontWeight: 500 }}>
                    {"v" + entry.from_version + " \u2192 v" + entry.to_version}
                  </span>
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
              ))}
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
                onClick={handleRollback}
                disabled={updateStatus !== "idle"}
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
        <div style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0,0,0,0.7)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 1000,
        }}>
          <div style={{
            background: "var(--bg-elevated)",
            borderRadius: "var(--border-radius)",
            padding: "var(--space-xl)",
            width: 400,
            boxShadow: "var(--shadow-md)",
          }}>
            <div style={{ fontSize: "var(--font-size-lg)", fontWeight: 600, marginBottom: "var(--space-lg)" }}>
              {"Installing OpenAVC v" + updateAvailable}
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)", marginBottom: "var(--space-lg)" }}>
              {STEPS.map((step) => {
                const currentStep = statusToStep(updateStatus);
                const stepIndex = STEPS.findIndex(s => s.id === step.id);
                const currentIndex = currentStep ? STEPS.findIndex(s => s.id === currentStep) : -1;
                const isDone = stepIndex < currentIndex;
                const isActive = step.id === currentStep;
                const isPending = stepIndex > currentIndex;

                return (
                  <div key={step.id} style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", fontSize: "var(--font-size-sm)" }}>
                    {isDone && <CheckCircle size={16} style={{ color: "var(--color-success)", flexShrink: 0 }} />}
                    {isActive && <Loader size={16} style={{ color: "var(--accent)", flexShrink: 0, animation: "spin 1s linear infinite" }} />}
                    {isPending && <div style={{ width: 16, height: 16, borderRadius: "50%", border: "2px solid var(--border-color)", flexShrink: 0 }} />}
                    <span style={{ color: isPending ? "var(--text-muted)" : "var(--text-primary)", fontWeight: isActive ? 500 : 400 }}>
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
                  background: "var(--accent)",
                  transition: "width 0.3s ease",
                  borderRadius: 2,
                }} />
              </div>
            )}

            <div style={{ fontSize: 12, color: "var(--text-muted)", textAlign: "center" }}>
              Do not close this window or power off the system.
            </div>

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
          </div>
        </div>
      )}

      {/* CSS keyframe for spinner */}
      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </ViewContainer>
  );
}
