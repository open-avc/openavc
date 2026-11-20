/** The cloud's upgrade notice, on this system's own screens.
 *
 *  The cloud tells a system below the release it needs so at every session
 *  start, and the agent keeps it as `system.cloud_upgrade_required` and
 *  `system.cloud_min_version` (cleared when the agent stops). The Cloud
 *  Connection page and the Dashboard both draw this one notice; the system
 *  stays connected and keeps what still works, so it is a warning, not the
 *  red of a stopped connection. Clicking it opens Updates.
 */
import { ArrowRight, ArrowUpCircle } from "lucide-react";
import { useState } from "react";
import { useConnectionStore } from "../../store/connectionStore";
import { useNavigationStore } from "../../store/navigationStore";

export function cloudUpgradeSentence(minVersion: unknown): string {
  const release = typeof minVersion === "string" ? minVersion.trim().replace(/^v/i, "") : "";
  return release
    ? `Cloud features need OpenAVC v${release} or later. Update this system.`
    : "Cloud features need a newer OpenAVC release. Update this system.";
}

export function CloudUpgradeNotice({ style }: { style?: React.CSSProperties }) {
  const required = useConnectionStore((s) => s.liveState["system.cloud_upgrade_required"] === true);
  const minVersion = useConnectionStore((s) => s.liveState["system.cloud_min_version"]);
  const [hover, setHover] = useState(false);
  if (!required) return null;

  const sentence = cloudUpgradeSentence(minVersion);
  const openUpdates = () => useNavigationStore.getState().navigateTo("updates");
  return (
    <div
      data-testid="cloud-upgrade-notice"
      role="button"
      tabIndex={0}
      aria-label={sentence}
      onClick={openUpdates}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openUpdates();
        }
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-md)",
        padding: "var(--space-md)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--color-warning)",
        background: hover ? "var(--bg-hover)" : "var(--color-warning-bg)",
        cursor: "pointer",
        ...style,
      }}
    >
      <ArrowUpCircle size={20} style={{ color: "var(--color-warning)", flexShrink: 0 }} />
      <div style={{ flex: 1, fontSize: "var(--font-size-sm)" }}>{sentence}</div>
      <ArrowRight size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
    </div>
  );
}
