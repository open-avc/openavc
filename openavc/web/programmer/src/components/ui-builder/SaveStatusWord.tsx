/**
 * The UI Builder toolbar's word for where the project's save stands
 * (store/saveState.ts decides which): Save failed, with the reason on hover;
 * Retrying..., with when on hover; Saving..., Pending..., Unsaved or Saved.
 */
import { Check, Loader2 } from "lucide-react";
import { useProjectStore } from "../../store/projectStore";
import { retryWait, saveFailure, saveStage } from "../../store/saveState";

const MUTED = { color: "var(--text-secondary)" } as const;
const WITH_ICON = { display: "flex", alignItems: "center", gap: 3, ...MUTED } as const;

export function SaveStatusWord() {
  const stage = useProjectStore(saveStage);
  const failure = useProjectStore(saveFailure);
  const retryIn = useProjectStore((s) => s.saveRetryIn);
  switch (stage) {
    case "failed":
      return (
        <span style={{ color: "var(--color-error, #d33)", fontWeight: 500 }} title={failure ?? undefined}>
          Save failed
        </span>
      );
    case "retrying":
      return (
        <span style={{ ...MUTED, fontStyle: "italic" }} title={retryWait(retryIn ?? 0)}>
          Retrying...
        </span>
      );
    case "saving":
      return (
        <span style={WITH_ICON}>
          <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
          Saving...
        </span>
      );
    case "pending":
      return (
        <span style={{ ...MUTED, fontStyle: "italic" }} title="Will save shortly">
          Pending...
        </span>
      );
    case "unsaved":
      return <span style={{ color: "var(--color-warning)", fontWeight: 500 }}>Unsaved</span>;
    default:
      return (
        <span style={WITH_ICON}>
          <Check size={12} />
          Saved
        </span>
      );
  }
}
