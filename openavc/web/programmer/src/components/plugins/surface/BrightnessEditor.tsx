/**
 * Brightness rules and idle dim for a deck.
 */
import { ConditionGroupEditor, type ConditionGroup } from "../../shared/ConditionGroupEditor";

interface BrightnessRule {
  level?: number;
  when?: ConditionGroup;
}

export function BrightnessEditor({
  config,
  onConfigChange,
  baseBrightness,
  onBaseBrightnessChange,
}: {
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
  // Base level lives in the flat plugin settings (config.brightness). Only
  // passed when editing the main config — a customized deck overrides it
  // via the "This deck's settings" row instead.
  baseBrightness?: number;
  onBaseBrightnessChange?: (value: number | undefined) => void;
}) {
  const rules = (config.auto_brightness as BrightnessRule[] | undefined) ?? [];
  const idleDim = config.idle_dim as { after_seconds?: number; level?: number } | undefined;

  const setRules = (next: BrightnessRule[]) => {
    onConfigChange({ ...config, auto_brightness: next });
  };
  const updateRule = (i: number, patch: Partial<BrightnessRule>) =>
    setRules(rules.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const removeRule = (i: number) => setRules(rules.filter((_, j) => j !== i));
  const moveRule = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= rules.length) return;
    const next = [...rules];
    [next[i], next[j]] = [next[j], next[i]];
    setRules(next);
  };

  const setIdleDim = (next: { after_seconds?: number; level?: number } | undefined) => {
    if (next) {
      onConfigChange({ ...config, idle_dim: next });
    } else {
      const { idle_dim: _drop, ...rest } = config;
      onConfigChange(rest);
    }
  };

  const numInputStyle: React.CSSProperties = {
    width: 64, padding: "var(--space-xs) var(--space-sm)",
    borderRadius: "var(--border-radius)",
    border: "1px solid var(--border-color)",
    background: "var(--bg-surface)", color: "var(--text-primary)",
    fontSize: "var(--font-size-sm)",
  };

  const reorderBtn: React.CSSProperties = {
    padding: "var(--space-2xs) var(--space-xs)", borderRadius: "var(--border-radius)", fontSize: "var(--font-size-2xs)",
    color: "var(--text-muted)", background: "transparent",
    border: "1px solid var(--border-color)", cursor: "pointer", lineHeight: 1,
  };

  return (
    <div style={{ maxWidth: 560 }}>
      {/* Base level (flat plugin setting) */}
      {onBaseBrightnessChange && (
        <label
          style={{
            display: "flex", alignItems: "center", gap: "var(--space-sm)",
            fontSize: "var(--font-size-sm)", color: "var(--text-secondary)",
            marginBottom: "var(--space-sm)",
          }}
        >
          Base brightness
          <input
            type="number" min={0} max={100}
            value={baseBrightness ?? ""}
            placeholder="70"
            onChange={(e) =>
              onBaseBrightnessChange(
                e.target.value === ""
                  ? undefined
                  : Math.max(0, Math.min(100, Number(e.target.value)))
              )
            }
            style={numInputStyle}
          />
          <span style={{ fontSize: "var(--font-size-sm)" }}>% applies when no rule below matches.</span>
        </label>
      )}

      {/* Idle dim */}
      <label style={{
        display: "flex", alignItems: "center", gap: "var(--space-sm)",
        fontSize: "var(--font-size-sm)", color: "var(--text-secondary)",
        marginBottom: "var(--space-sm)", cursor: "pointer", flexWrap: "wrap",
      }}>
        <input
          type="checkbox"
          checked={!!idleDim}
          onChange={(e) =>
            setIdleDim(e.target.checked ? { after_seconds: 300, level: 10 } : undefined)
          }
        />
        Dim when idle
        {idleDim && (
          <span style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", fontSize: "var(--font-size-sm)" }}>
            to
            <input
              type="number" min={0} max={100}
              value={idleDim.level ?? 10}
              onChange={(e) => setIdleDim({ ...idleDim, level: Number(e.target.value) })}
              style={numInputStyle}
            />
            % after
            <input
              type="number" min={5} step={5}
              value={idleDim.after_seconds ?? 300}
              onChange={(e) => setIdleDim({ ...idleDim, after_seconds: Number(e.target.value) })}
              style={numInputStyle}
            />
            seconds without input. Any press, turn, or tap wakes it.
          </span>
        )}
      </label>

      {/* State-driven rules */}
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
        {rules.map((rule, i) => (
          <div
            key={i}
            style={{
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              padding: "var(--space-sm)",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-xs)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
              <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>Set brightness to</span>
              <input
                type="number" min={0} max={100}
                value={rule.level ?? 70}
                onChange={(e) => updateRule(i, { level: Number(e.target.value) })}
                style={numInputStyle}
              />
              <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>%</span>
              <div style={{ marginLeft: "auto", display: "flex", gap: "var(--space-xs)", alignItems: "center" }}>
                {i > 0 && (
                  <button onClick={() => moveRule(i, -1)} title="Move up" style={reorderBtn}>&#9650;</button>
                )}
                {i < rules.length - 1 && (
                  <button onClick={() => moveRule(i, 1)} title="Move down" style={reorderBtn}>&#9660;</button>
                )}
                <button
                  onClick={() => removeRule(i)}
                  title="Remove rule"
                  style={{
                    padding: "var(--space-2xs) var(--space-sm)", borderRadius: "var(--border-radius)", fontSize: "var(--font-size-xs)",
                    color: "var(--color-error)", background: "transparent",
                    border: "1px solid var(--border-color)", cursor: "pointer",
                  }}
                >
                  &times;
                </button>
              </div>
            </div>
            <span style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)" }}>when</span>
            <ConditionGroupEditor
              value={rule.when}
              onChange={(when) => updateRule(i, { when })}
              required
              anyHint="Applies this brightness when any condition is true."
              allHint="Applies this brightness when all conditions are true."
            />
          </div>
        ))}
      </div>

      <button
        onClick={() => setRules([...rules, { level: 30, when: { key: "", operator: "truthy" } }])}
        style={{
          marginTop: "var(--space-sm)",
          display: "flex", alignItems: "center", justifyContent: "center", gap: "var(--space-xs)",
          padding: "var(--space-xs) var(--space-md)", borderRadius: "var(--border-radius)",
          border: "1px dashed var(--border-color)", background: "transparent",
          color: "var(--text-muted)", fontSize: "var(--font-size-sm)", cursor: "pointer",
        }}
      >
        + Add brightness rule
      </button>
    </div>
  );
}
