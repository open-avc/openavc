/**
 * A macro's own Cooldown: the least time between two starts of it, whatever
 * fires it. Its floor and blank are the project schema's, from the generated
 * table (projectValues.gen.ts), so the box never offers a value the save
 * refuses. A cooldown equal to the blank is stored as no value.
 */
import { HelpCircle } from "lucide-react";
import { PROJECT_VALUES } from "../../api/projectValues.gen";
import type { MacroConfig } from "../../api/types";
import { NumericInput } from "../shared/NumericInput";

const COOLDOWN = PROJECT_VALUES.MacroConfig.cooldown_seconds;

export function MacroCooldown({
  macro,
  onUpdate,
}: {
  macro: MacroConfig;
  onUpdate: (macro: MacroConfig) => void;
}) {
  return (
    <label style={{ fontSize: 11, color: "var(--text-muted)", display: "flex", alignItems: "center", gap: 4 }}>
      Cooldown:
      <span title="Minimum seconds between starts of this macro, enforced no matter what fires it. 0 = no cooldown.">
        <HelpCircle size={11} style={{ opacity: 0.5 }} />
      </span>
      <NumericInput
        owner={`macro ${macro.id} cooldown_seconds`}
        min={COOLDOWN.min}
        step={0.5}
        value={macro.cooldown_seconds ?? COOLDOWN.default}
        onCommit={(v) => {
          if (v === undefined) return;
          onUpdate({ ...macro, cooldown_seconds: v === COOLDOWN.default ? undefined : v });
        }}
        title="Minimum seconds between starts of this macro (0 = off)."
        style={{
          width: 48,
          padding: "1px 4px",
          fontSize: 11,
          fontFamily: "var(--font-mono)",
          background: "var(--bg-primary)",
          border: "1px solid var(--border-color)",
          borderRadius: 3,
          color: "var(--text-primary)",
        }}
      />
      s
    </label>
  );
}
