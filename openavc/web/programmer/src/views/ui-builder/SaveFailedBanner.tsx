/**
 * The line across the top of the UI Builder when the last save failed: the
 * reason, Retry (a direct save) and Dismiss (which clears the failure). It
 * stays away for a version conflict, which has its own banner
 * (store/saveState.ts, which the toolbar's status word follows too).
 */
import { useProjectStore } from "../../store/projectStore";
import { saveFailure } from "../../store/saveState";

export function SaveFailedBanner() {
  const failure = useProjectStore(saveFailure);
  const save = useProjectStore((s) => s.save);
  return failure && (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "6px 16px",
        background: "rgba(244,67,54,0.15)",
        color: "var(--color-error)",
        fontSize: "var(--font-size-sm)",
        borderBottom: "1px solid var(--color-error)",
        flexShrink: 0,
      }}
    >
      <span style={{ flex: 1 }}>Save failed: {failure}</span>
      <button
        onClick={() => save()}
        style={{
          padding: "2px 10px",
          fontSize: 11,
          fontWeight: 600,
          borderRadius: 3,
          border: "1px solid var(--color-error)",
          background: "transparent",
          color: "var(--color-error)",
          cursor: "pointer",
        }}
      >
        Retry
      </button>
      <button
        onClick={() => useProjectStore.setState({ error: null })}
        style={{
          padding: "2px 10px",
          fontSize: 11,
          borderRadius: 3,
          border: "1px solid var(--border-color)",
          background: "transparent",
          color: "var(--text-secondary)",
          cursor: "pointer",
        }}
      >
        Dismiss
      </button>
    </div>
  );
}
