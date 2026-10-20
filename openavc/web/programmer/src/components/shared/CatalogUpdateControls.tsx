// The Update affordances for an installed driver or plugin that has a newer
// catalog version, drawn the same on every screen that offers one. Which
// version and whether it can be installed come from catalogUpdates.ts.
import { AlertTriangle, ArrowUpCircle, Loader2 } from "lucide-react";
import { updateAvailableText, updatesAvailableText, type CatalogUpdate } from "./catalogUpdates";

function needsPlatformTitle(update: CatalogUpdate): string {
  return `v${update.version} needs OpenAVC ${update.blockedBy} or later. Update OpenAVC first.`;
}

/** "Update available: v1.5.0 (installed v1.4.3)" and an Update button, under
 *  a driver's or plugin's name. */
export function UpdateAvailableLine({
  update,
  prefix,
  updating,
  onUpdate,
  style,
}: {
  update: CatalogUpdate;
  /** What the line opens with; "Update available" by default. */
  prefix?: string;
  updating: boolean;
  onUpdate: () => void;
  style?: React.CSSProperties;
}) {
  return (
    <div
      data-testid="update-available"
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-sm)",
        flexWrap: "wrap",
        fontSize: "var(--font-size-sm)",
        ...style,
      }}
    >
      <span style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "var(--accent)" }}>
        <ArrowUpCircle size={14} style={{ flexShrink: 0 }} />
        {updateAvailableText(update, prefix)}
      </span>
      {update.blockedBy ? (
        <span
          title={needsPlatformTitle(update)}
          style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "var(--text-muted)" }}
        >
          <AlertTriangle size={14} />
          Needs OpenAVC v{update.blockedBy}
        </span>
      ) : updating ? (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "var(--text-muted)" }}>
          <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
          Updating...
        </span>
      ) : (
        <button
          onClick={onUpdate}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            padding: "2px var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: "var(--accent-bg)",
            color: "var(--text-on-accent)",
            fontSize: "var(--font-size-sm)",
            fontWeight: 500,
          }}
        >
          Update
        </button>
      )}
    </div>
  );
}

/** "Update to v1.5.0" on a row of an installed list. */
export function UpdateRowButton({
  update,
  updating,
  onUpdate,
}: {
  update: CatalogUpdate;
  updating: boolean;
  onUpdate: () => void;
}) {
  if (update.blockedBy) {
    return (
      <span
        title={needsPlatformTitle(update)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          fontSize: "var(--font-size-xs)",
          color: "var(--text-muted)",
        }}
      >
        <AlertTriangle size={12} />
        v{update.version} needs OpenAVC v{update.blockedBy}
      </span>
    );
  }
  if (updating) {
    return (
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          fontSize: "var(--font-size-xs)",
          color: "var(--text-muted)",
        }}
      >
        <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
        Updating...
      </span>
    );
  }
  return (
    <button
      onClick={(e) => {
        // The row behind it selects on click; this button does its own thing.
        e.stopPropagation();
        onUpdate();
      }}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "1px var(--space-sm)",
        borderRadius: "var(--border-radius)",
        background: "var(--accent-bg)",
        color: "var(--text-on-accent)",
        fontSize: "var(--font-size-xs)",
        fontWeight: 500,
      }}
    >
      <ArrowUpCircle size={12} />
      Update to v{update.version}
    </button>
  );
}

/** "2 updates available", at the top of an installed list. Nothing when 0. */
export function UpdatesAvailableCount({ count }: { count: number }) {
  if (count === 0) return null;
  return (
    <div
      data-testid="updates-available-count"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        padding: "var(--space-xs) var(--space-md)",
        borderBottom: "1px solid var(--border-color)",
        background: "var(--accent-dim, rgba(59,130,246,0.1))",
        color: "var(--accent)",
        fontSize: "var(--font-size-sm)",
        fontWeight: 500,
      }}
    >
      <ArrowUpCircle size={14} />
      {updatesAvailableText(count)}
    </div>
  );
}
