import { CheckCircle, XCircle, CloudDownload } from "lucide-react";
import { panelSoftwareDisplay } from "./updatesHelpers";
import { cardStyle, sectionTitle } from "./updatesStyles";

/** When the panel reported its last install attempt, in this browser's own
 *  format; "" when there is no time or it cannot be read. */
function resultTime(at: string | undefined): string {
  const when = at ? new Date(at) : null;
  return when && !isNaN(when.getTime()) ? when.toLocaleString() : "";
}

/**
 * Panel software on appliance hardware, on the System Updates page: updated
 * from the cloud and installed by the panel itself, so there is nothing to
 * press here. Draws nothing on a system without panel software.
 */
export function PanelSoftwareSection({ liveState }: { liveState: Record<string, unknown> }) {
  const panelSoftware = panelSoftwareDisplay(liveState);
  if (!panelSoftware) return null;
  const panelResultAt = resultTime(panelSoftware.lastResult?.at);

  return (
    <div style={{ marginBottom: "var(--space-xl)" }}>
      <h3 style={sectionTitle}>Panel Software</h3>
      <div style={cardStyle}>
        <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "var(--space-sm) var(--space-xl)", fontSize: "var(--font-size-sm)" }}>
          <span style={{ color: "var(--text-secondary)" }}>Version</span>
          <span style={{ fontWeight: 600 }}>{panelSoftware.version}</span>
          {panelSoftware.lastResult && <>
            <span style={{ color: "var(--text-secondary)" }}>Last update</span>
            <span style={{ display: "flex", alignItems: "flex-start", gap: "var(--space-xs)" }}>
              {panelSoftware.lastResult.ok
                ? <CheckCircle size={14} style={{ color: "var(--color-success)", flexShrink: 0, marginTop: 2 }} />
                : <XCircle size={14} style={{ color: "var(--color-error)", flexShrink: 0, marginTop: 2 }} />}
              <span>
                {panelSoftware.lastResult.text}
                {panelResultAt && <span style={{ color: "var(--text-muted)" }}>{" (" + panelResultAt + ")"}</span>}
              </span>
            </span>
          </>}
        </div>
        {panelSoftware.pending && (
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-md)", marginTop: "var(--space-md)", paddingTop: "var(--space-md)", borderTop: "1px solid var(--border-color)", fontSize: "var(--font-size-sm)" }}>
            <CloudDownload size={16} style={{ color: "var(--accent)", flexShrink: 0 }} />
            <span>{panelSoftware.pending}</span>
          </div>
        )}
      </div>
    </div>
  );
}
