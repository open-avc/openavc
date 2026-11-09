/**
 * What the header's two checks leave on a device page, below the banners: why
 * Audit this device did not open the wizard (until dismissed), and the line
 * Test Connection wrote (until the device connects or drops). The checks
 * themselves run in `useDeviceChecks.ts`.
 */
import { ClipboardCheck, X } from "lucide-react";
import type { DeviceConfig, DeviceConnectionTest } from "../../api/types";
import { connectionTestText } from "./deviceAddress";

export function AuditRefusalLine({ refusal, onDismiss }: { refusal: string; onDismiss: () => void }) {
  if (!refusal) return null;
  return (
    <div
      role="status"
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-sm)",
        padding: "var(--space-sm) var(--space-md)",
        borderRadius: "var(--border-radius)",
        marginBottom: "var(--space-md)",
        fontSize: "var(--font-size-sm)",
        background: "var(--bg-hover)",
        color: "var(--text-secondary)",
      }}
    >
      <ClipboardCheck size={14} style={{ flexShrink: 0 }} />
      <span style={{ flex: 1 }}>{refusal}</span>
      <button
        onClick={onDismiss}
        aria-label="Dismiss"
        title="Dismiss"
        style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: "var(--text-muted)" }}
      >
        <X size={14} />
      </button>
    </div>
  );
}

/** Test Connection's result. A bridge it went through is named as the
 *  project names it. */
export function ConnectionTestLine({
  result,
  devices,
}: {
  result: DeviceConnectionTest | null;
  devices: DeviceConfig[] | undefined;
}) {
  if (!result) return null;
  return (
    <div
      style={{
        padding: "var(--space-sm) var(--space-md)",
        borderRadius: "var(--border-radius)",
        marginBottom: "var(--space-md)",
        fontSize: "var(--font-size-sm)",
        background: result.success ? "rgba(76,175,80,0.15)" : "var(--color-error-bg)",
        color: result.success ? "var(--color-success)" : "var(--color-error)",
      }}
    >
      {connectionTestText(result, (id) => devices?.find((d) => d.id === id)?.name || id)}
      {result.note && (
        <div style={{ marginTop: "var(--space-xs)", color: "var(--text-muted)" }}>
          {result.note}
        </div>
      )}
    </div>
  );
}
