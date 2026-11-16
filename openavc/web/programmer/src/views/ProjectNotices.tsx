/**
 * The Dashboard's notices about the project itself, above everything else on
 * the page: what the last boot had to do to get a project (a recovery), and
 * what loading it changed when an earlier release saved it (an update). The
 * figures below either may be describing other settings than the ones whoever
 * is reading this last saved.
 */
import { useCallback, useState } from "react";
import { AlertTriangle, ArrowRight, X } from "lucide-react";
import * as api from "../api/restClient";
import { parseApiError } from "../api/errors";
import { useConnectionStore } from "../store/connectionStore";
import { useNavigationStore } from "../store/navigationStore";
import { showError } from "../store/toastStore";
import { recoveryNotice, type RecoveryNotice as RecoveryNoticeCopy } from "./recoveryNotice";
import { UpdateNotice } from "./UpdateNotice";

/** The recovery notice, then the update notice; each draws nothing when it
 *  has nothing to say. */
export function ProjectNotices() {
  const liveState = useConnectionStore((s) => s.liveState);
  const recovery = recoveryNotice(liveState);
  return (
    <>
      {recovery.show && <RecoveryNotice notice={recovery} />}
      <UpdateNotice />
    </>
  );
}

/** What a startup recovery left behind, said where somebody will see it.
 *
 *  The recovery itself works. Before this card the only trace was two log
 *  lines, so the integrator whose last edit fell between the backup and the
 *  corrupt save had no way to learn it was gone. The record on the server
 *  survives restarts, and dismissing here is the only thing that clears it.
 */
function RecoveryNotice({ notice }: { notice: RecoveryNoticeCopy }) {
  const [dismissing, setDismissing] = useState(false);
  const tone = notice.severe ? "var(--color-error)" : "#f59e0b";

  const dismiss = useCallback(() => {
    setDismissing(true);
    api.dismissRecoveryNotice()
      .catch((e) => showError(`Could not dismiss the notice: ${parseApiError(e)}`))
      .finally(() => setDismissing(false));
  }, []);

  return (
    <div
      style={{
        background: "var(--bg-surface)",
        border: "1px solid " + tone,
        borderLeft: "3px solid " + tone,
        borderRadius: "var(--border-radius)",
        padding: "var(--space-lg)",
        marginBottom: "var(--space-lg)",
        display: "flex",
        alignItems: "flex-start",
        gap: "var(--space-md)",
      }}
    >
      <AlertTriangle size={18} style={{ color: tone, flexShrink: 0, marginTop: 2 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: "var(--font-size-sm)" }}>{notice.title}</div>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>
          {notice.body}
        </div>
        {!!notice.backupFile && (
          <code style={{
            display: "block",
            fontSize: 11,
            color: "var(--text-muted)",
            fontFamily: "var(--font-mono)",
            marginTop: 6,
            wordBreak: "break-all",
          }}>
            {notice.backupFile}
          </code>
        )}
        <button
          type="button"
          onClick={() => useNavigationStore.getState().navigateTo("project", { type: "backups", id: "" })}
          style={{
            marginTop: "var(--space-md)",
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            background: "var(--bg-hover)",
            border: "1px solid var(--border-color)",
            color: "var(--text-primary)",
            borderRadius: "var(--border-radius)",
            padding: "6px 12px",
            cursor: "pointer",
            fontSize: "var(--font-size-sm)",
          }}
        >
          {notice.action}
          <ArrowRight size={13} />
        </button>
      </div>
      <button
        type="button"
        onClick={dismiss}
        disabled={dismissing}
        title="Dismiss"
        aria-label="Dismiss"
        style={{
          background: "none",
          border: "none",
          color: "var(--text-muted)",
          cursor: dismissing ? "default" : "pointer",
          padding: 4,
          flexShrink: 0,
        }}
      >
        <X size={15} />
      </button>
    </div>
  );
}
