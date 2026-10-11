import { useEffect, useState } from "react";
import * as api from "../../api/restClient";
import { useNavigationStore } from "../../store/navigationStore";

/**
 * Keeps a change of who can connect, and says so once.
 *
 * When a change in Settings > Network > Accept connections from cut off the
 * browser making it, the server goes back to the previous choice after ten
 * minutes unless the Programmer is opened through the new one. Opening the
 * Programmer is what confirms it, so this asks the server on every page load,
 * whichever view is showing. The server answers with "Kept: ..." once after a
 * confirmation, or "Went back to ..." once after the deadline passed.
 */

type ConfirmAnswer = { kept: string | null; went_back: string | null };

// One call per page load: the component can mount again (signing in after a
// session ends) and the answer is handed over only once. A failed call (the
// session ended before it landed) is asked again on the next mount.
let confirmed: Promise<ConfirmAnswer> | null = null;

export function confirmOnLoad(): Promise<ConfirmAnswer> {
  if (!confirmed) {
    confirmed = api.confirmListen().catch((err) => {
      confirmed = null;
      throw err;
    });
  }
  return confirmed;
}

/** Tests only: forget this page load's call. */
export function resetConfirmOnLoad(): void {
  confirmed = null;
}

export function ListenNotice() {
  const [answer, setAnswer] = useState<ConfirmAnswer | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    confirmOnLoad().then(setAnswer).catch(() => {});
  }, []);

  if (!answer || dismissed) return null;
  const message = answer.went_back || answer.kept;
  if (!message) return null;
  const wentBack = !!answer.went_back;

  const buttonStyle: React.CSSProperties = {
    padding: "2px 10px",
    borderRadius: 4,
    border: "1px solid var(--border-color)",
    background: "transparent",
    color: "var(--text-primary)",
    fontSize: 12,
    cursor: "pointer",
  };

  return (
    <div
      role="status"
      data-testid="listen-notice"
      style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        gap: "var(--space-md)",
        padding: "var(--space-sm) var(--space-md)",
        background: "var(--bg-surface)",
        borderBottom: "1px solid var(--border-color)",
        borderLeft: `3px solid ${wentBack ? "rgb(255, 152, 0)" : "var(--accent)"}`,
        fontSize: 13, color: "var(--text-primary)", flexShrink: 0,
      }}
    >
      <span>{message}</span>
      <div style={{ display: "flex", gap: "var(--space-sm)", flexShrink: 0 }}>
        {wentBack && (
          <button
            type="button"
            onClick={() => useNavigationStore.getState().navigateTo("settings")}
            style={{ ...buttonStyle, border: "none", background: "var(--accent-bg)", color: "var(--text-on-accent)" }}
          >
            Settings
          </button>
        )}
        <button type="button" onClick={() => setDismissed(true)} style={buttonStyle}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
