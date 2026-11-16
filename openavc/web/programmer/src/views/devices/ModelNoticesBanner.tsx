/**
 * What a product's description of itself holds that OpenAVC reads past or
 * works around (`DeviceInfo.model_notices`, one server sentence a kind). The
 * device runs; the description is the manufacturer's to correct, in the
 * product's firmware, so this is a note rather than a warning.
 */
import { Info } from "lucide-react";

export function ModelNoticesBanner({ notices }: { notices: string[] | undefined }) {
  if (!notices?.length) return null;
  return (
    <div
      data-testid="model-notices-banner"
      style={{
        padding: "var(--space-sm) var(--space-md)",
        borderRadius: "var(--border-radius)",
        marginBottom: "var(--space-md)",
        background: "var(--bg-surface)",
        border: "1px solid var(--border-color)",
        display: "flex",
        gap: "var(--space-sm)",
        alignItems: "flex-start",
      }}
    >
      <Info size={16} style={{ color: "var(--text-secondary)", flexShrink: 0, marginTop: 2 }} />
      <div style={{ fontSize: "var(--font-size-sm)" }}>
        {notices.map((notice) => (
          <div key={notice}>{notice}</div>
        ))}
        <div style={{ color: "var(--text-secondary)", marginTop: 2 }}>
          Its manufacturer can correct this in the product's firmware.
        </div>
      </div>
    </div>
  );
}
