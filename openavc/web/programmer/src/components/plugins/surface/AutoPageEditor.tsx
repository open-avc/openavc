/**
 * Page automation — jump to a button page when system state changes.
 */
import { ConditionGroupEditor, type ConditionGroup } from "../../shared/ConditionGroupEditor";
import type { SurfaceLayout } from "./types";

interface AutoPageRule {
  page?: number;
  when?: ConditionGroup;
}

export function AutoPageEditor({
  layout,
  config,
  onConfigChange,
}: {
  layout: SurfaceLayout;
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
}) {
  const rules = (config.auto_page as AutoPageRule[] | undefined) ?? [];
  const maxPages = layout.max_pages ?? 10;
  const pageNames = (config.page_names as Record<string, string> | undefined) ?? {};

  const setRules = (next: AutoPageRule[]) => {
    onConfigChange({ ...config, auto_page: next });
  };
  const updateRule = (i: number, patch: Partial<AutoPageRule>) =>
    setRules(rules.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const removeRule = (i: number) => setRules(rules.filter((_, j) => j !== i));
  const moveRule = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= rules.length) return;
    const next = [...rules];
    [next[i], next[j]] = [next[j], next[i]];
    setRules(next);
  };

  const reorderBtn: React.CSSProperties = {
    padding: "2px 5px", borderRadius: "var(--border-radius)", fontSize: 9,
    color: "var(--text-muted)", background: "transparent",
    border: "1px solid var(--border-color)", cursor: "pointer", lineHeight: 1,
  };

  return (
    <div style={{ maxWidth: 560 }}>
      {rules.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
          No rules yet. Example: switch to an "Off Hours" page when the room
          powers down.
        </div>
      ) : (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
          Rules are checked in order; the first match wins.
        </div>
      )}

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
              <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>Switch to</span>
              <select
                value={rule.page ?? 0}
                onChange={(e) => updateRule(i, { page: Number(e.target.value) })}
                style={{
                  padding: "4px 8px",
                  borderRadius: "var(--border-radius)",
                  border: "1px solid var(--border-color)",
                  background: "var(--bg-primary)",
                  color: "var(--text-primary)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                {Array.from({ length: maxPages }, (_, p) => (
                  <option key={p} value={p}>{pageNames[String(p)] || `Page ${p + 1}`}</option>
                ))}
              </select>
              <div style={{ marginLeft: "auto", display: "flex", gap: 4, alignItems: "center" }}>
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
                    padding: "2px 6px", borderRadius: "var(--border-radius)", fontSize: 11,
                    color: "var(--color-error)", background: "transparent",
                    border: "1px solid var(--border-color)", cursor: "pointer",
                  }}
                >
                  &times;
                </button>
              </div>
            </div>
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>when</span>
            <ConditionGroupEditor
              value={rule.when}
              onChange={(when) => updateRule(i, { when })}
              required
              anyHint="Switches to this page when any condition is true."
              allHint="Switches to this page when all conditions are true."
            />
          </div>
        ))}
      </div>

      <button
        onClick={() => setRules([...rules, { page: 0, when: { key: "", operator: "truthy" } }])}
        style={{
          marginTop: "var(--space-sm)",
          display: "flex", alignItems: "center", justifyContent: "center", gap: 4,
          padding: "5px 10px", borderRadius: "var(--border-radius)",
          border: "1px dashed var(--border-color)", background: "transparent",
          color: "var(--text-muted)", fontSize: 12, cursor: "pointer",
        }}
      >
        + Add paging rule
      </button>
    </div>
  );
}
