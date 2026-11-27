import type { CSSProperties } from "react";
import { useConnectionStore } from "../../store/connectionStore";
import { valueText } from "./booleanWords";

/** Beside a condition whose value names a key ($var.wanted): which key it is
 *  compared with, and what that key holds now. */
export function ComparedWith({ stateKey, style }: { stateKey: string; style?: CSSProperties }) {
  const value = useConnectionStore((s) => s.liveState[stateKey]);
  return (
    <span style={{ color: "var(--text-secondary)", ...style }}>
      Compared with {stateKey}
      {value !== undefined && <>: <strong>{valueText(value)}</strong></>}
    </span>
  );
}
