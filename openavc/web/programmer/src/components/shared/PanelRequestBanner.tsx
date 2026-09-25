import { useState } from "react";
import { usePanelDevicesStore } from "../../store/panelDevicesStore";
import { useNavigationStore } from "../../store/navigationStore";
import { PromptDialog } from "./PromptDialog";
import { defaultPanelName, panelRequestSentence } from "./panelDevicesCopy";
import type { PanelDevice } from "../../api/restClient";

/**
 * The notice on every view while a panel is waiting for approval.
 *
 * One waiting panel is named in full (its code, what it is, its address) and
 * can be approved or denied right here; several are counted, and Show all
 * goes to the Dashboard's Panels card, which tells them apart. Nothing is
 * shown while Panel access is Anyone on the network, because nothing waits.
 */
export function PanelRequestBanner() {
  const access = usePanelDevicesStore((s) => s.access);
  const pending = usePanelDevicesStore((s) => s.pending);
  const approve = usePanelDevicesStore((s) => s.approve);
  const deny = usePanelDevicesStore((s) => s.deny);
  const [naming, setNaming] = useState<PanelDevice | null>(null);

  if (access !== "approved" || pending.length === 0) return null;
  const one = pending.length === 1 ? pending[0] : null;

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
    <>
      <div
        role="status"
        data-testid="panel-request-banner"
        style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          gap: "var(--space-md)",
          padding: "var(--space-sm) var(--space-md)",
          background: "var(--bg-surface)",
          borderBottom: "1px solid var(--border-color)",
          borderLeft: "3px solid var(--accent)",
          fontSize: 13, color: "var(--text-primary)", flexShrink: 0,
        }}
      >
        <span>{panelRequestSentence(pending)}</span>
        <div style={{ display: "flex", gap: "var(--space-sm)", flexShrink: 0 }}>
          {one && (
            <>
              <button
                type="button"
                onClick={() => setNaming(one)}
                style={{ ...buttonStyle, border: "none", background: "var(--accent-bg)", color: "var(--text-on-accent)" }}
              >
                Approve
              </button>
              <button type="button" onClick={() => { void deny(one.id); }} style={buttonStyle}>
                Deny
              </button>
            </>
          )}
          <button
            type="button"
            onClick={() => useNavigationStore.getState().navigateTo("dashboard")}
            style={buttonStyle}
          >
            Show all
          </button>
        </div>
      </div>
      {naming && (
        <PromptDialog
          title="Name this panel"
          defaultValue={defaultPanelName(naming)}
          submitLabel="Approve"
          onSubmit={(name) => {
            const device = naming;
            setNaming(null);
            void approve(device.id, name);
          }}
          onCancel={() => setNaming(null)}
        />
      )}
    </>
  );
}
