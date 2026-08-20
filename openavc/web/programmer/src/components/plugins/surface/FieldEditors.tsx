/**
 * Field-level editors shared by the assignment panels.
 *
 * A key, a dial, a touch zone and an info item all end up editing the same few
 * things — an ordered list of actions, a level meter, and the colors that
 * follow a condition — so they are written once here and rendered by whichever
 * panel happens to be open.
 */
import { X } from "lucide-react";
import { InlineColorPicker } from "../../shared/InlineColorPicker";
import { VariableKeyPicker } from "../../shared/VariableKeyPicker";
import { ActionPicker } from "../../ui-builder/BindingEditor/ActionPicker";
import type { ProjectConfig } from "../../../api/types";
import { fieldInputStyle, panelHintStyle } from "./styles";
import type { DialAdjust, DisplayFeedback, MeterConfig } from "./types";

export function ActionListEditor({
  actions,
  onChange,
  project,
  allowedActions,
  navigateOptions,
  addLabel,
}: {
  actions: Record<string, unknown>[];
  onChange: (actions: Record<string, unknown>[]) => void;
  project: ProjectConfig;
  allowedActions?: string[];
  navigateOptions?: { value: string; label: string }[];
  addLabel: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      {actions.map((act, i) => (
        <div
          key={i}
          style={{
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            padding: "var(--space-sm)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-xs)" }}>
            <span style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)" }}>Action {i + 1}</span>
            <button
              onClick={() => onChange(actions.filter((_, j) => j !== i))}
              style={{
                padding: "var(--space-2xs) var(--space-sm)", borderRadius: "var(--border-radius)",
                fontSize: "var(--font-size-xs)", color: "var(--color-error)",
                background: "transparent", border: "1px solid var(--border-color)",
                cursor: "pointer",
              }}
            >
              Remove
            </button>
          </div>
          <ActionPicker
            value={act}
            project={project}
            onChange={(v) => onChange(actions.map((a, j) => (j === i ? v : a)))}
            allowedActions={allowedActions}
            navigateOptions={navigateOptions}
          />
        </div>
      ))}
      <button
        onClick={() => onChange([...actions, { action: "" }])}
        style={{
          display: "flex", alignItems: "center", justifyContent: "center", gap: "var(--space-xs)",
          padding: "var(--space-xs) var(--space-md)", borderRadius: "var(--border-radius)",
          border: "1px dashed var(--border-color)", background: "transparent",
          color: "var(--text-muted)", fontSize: "var(--font-size-sm)", cursor: "pointer",
        }}
      >
        + {addLabel}
      </button>
    </div>
  );
}

// Meter (level bar) fields. Zones and dial readouts auto-enable when their
// adjust declares min+max (mirrors the runtime), so they get a tri-state;
// keys are plain on/off.
export function MeterFields({
  meter,
  bounds,
  onChange,
  allowAuto = false,
}: {
  meter: MeterConfig | boolean | undefined;
  bounds?: DialAdjust;
  onChange: (meter: MeterConfig | boolean | undefined) => void;
  allowAuto?: boolean;
}) {
  const autoAvailable =
    allowAuto && bounds?.min !== undefined && bounds?.max !== undefined;
  const mode =
    meter === false || (meter === undefined && !allowAuto)
      ? "off"
      : meter === undefined
        ? "auto"
        : "on";
  const cfg: MeterConfig = typeof meter === "object" && meter !== null ? meter : {};
  const update = (patch: Partial<MeterConfig>) => {
    const next = { ...cfg, ...patch };
    (Object.keys(next) as (keyof MeterConfig)[]).forEach((k) => {
      if (next[k] === undefined) delete next[k];
    });
    onChange(next);
  };
  const thresholds = cfg.thresholds ?? [];

  return (
    <div>
      <label style={panelHintStyle}>Level bar (meter)</label>
      <select
        value={mode}
        onChange={(e) => {
          const v = e.target.value;
          onChange(v === "on" ? {} : v === "off" ? (allowAuto ? false : undefined) : undefined);
        }}
        style={fieldInputStyle}
      >
        {allowAuto && (
          <option value="auto">
            {autoAvailable ? "Automatic (from the adjust range)" : "Automatic (needs an adjust range)"}
          </option>
        )}
        <option value="on">On</option>
        <option value="off">Off</option>
      </select>
      {mode === "on" && (
        <div style={{ marginTop: "var(--space-xs)", display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
          <div style={{ display: "flex", gap: "var(--space-sm)" }}>
            {(["min", "max"] as const).map((field) => (
              <div key={field} style={{ flex: 1 }}>
                <label style={panelHintStyle}>{field === "min" ? "Min" : "Max"}</label>
                <input
                  type="number"
                  value={cfg[field] ?? ""}
                  placeholder={field === "min" ? "0" : "100"}
                  onChange={(e) =>
                    update({ [field]: e.target.value === "" ? undefined : Number(e.target.value) })
                  }
                  style={fieldInputStyle}
                />
              </div>
            ))}
            <div>
              <label style={panelHintStyle}>Color</label>
              <InlineColorPicker
                value={cfg.color ?? ""}
                onChange={(c) => update({ color: c || undefined })}
              />
            </div>
          </div>
          {thresholds.map((rule, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
              <span style={panelHintStyle}>Above</span>
              <input
                type="number"
                value={rule.above ?? ""}
                onChange={(e) =>
                  update({
                    thresholds: thresholds.map((r, j) =>
                      j === i
                        ? { ...r, above: e.target.value === "" ? undefined : Number(e.target.value) }
                        : r
                    ),
                  })
                }
                style={{ ...fieldInputStyle, width: 70 }}
              />
              <InlineColorPicker
                value={rule.color ?? ""}
                onChange={(c) =>
                  update({
                    thresholds: thresholds.map((r, j) =>
                      j === i ? { ...r, color: c || undefined } : r
                    ),
                  })
                }
              />
              <button
                onClick={() =>
                  update({
                    thresholds: thresholds.filter((_, j) => j !== i).length
                      ? thresholds.filter((_, j) => j !== i)
                      : undefined,
                  })
                }
                title="Remove this color rule"
                style={{ color: "var(--text-muted)", cursor: "pointer" }}
              >
                <X size={12} />
              </button>
            </div>
          ))}
          {thresholds.length < 3 && (
            <button
              onClick={() => update({ thresholds: [...thresholds, {}] })}
              style={{
                alignSelf: "flex-start", fontSize: "var(--font-size-xs)", color: "var(--text-muted)",
                border: "1px dashed var(--border-color)", borderRadius: "var(--border-radius)",
                padding: "var(--space-2xs) var(--space-sm)", background: "transparent", cursor: "pointer",
              }}
            >
              + Color above a level
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// Simple conditional styling for zones / info items (the runtime accepts the
// full key-feedback schema; this edits the common active/inactive pair).
export function ZoneFeedbackFields({
  feedback,
  onChange,
}: {
  feedback: DisplayFeedback | undefined;
  onChange: (fb: DisplayFeedback | undefined) => void;
}) {
  const fb = feedback ?? {};
  const update = (patch: Partial<DisplayFeedback>) => {
    const next = { ...fb, ...patch };
    if (!next.key) {
      onChange(undefined);
    } else {
      onChange(next);
    }
  };
  const colorPair = (
    label: string,
    styleKey: "style_active" | "style_inactive"
  ) => (
    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
      <span style={panelHintStyle}>{label}</span>
      <InlineColorPicker
        value={fb[styleKey]?.bg_color ?? ""}
        onChange={(c) =>
          update({ [styleKey]: { ...(fb[styleKey] ?? {}), bg_color: c || undefined } })
        }
      />
      <InlineColorPicker
        value={fb[styleKey]?.text_color ?? ""}
        onChange={(c) =>
          update({ [styleKey]: { ...(fb[styleKey] ?? {}), text_color: c || undefined } })
        }
      />
    </div>
  );
  return (
    <div>
      <label style={panelHintStyle}>Colors from state (optional)</label>
      <VariableKeyPicker
        value={fb.key ?? ""}
        onChange={(key) => update({ key: key || undefined })}
        placeholder="Watch a state key..."
      />
      {fb.key && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", marginTop: "var(--space-xs)" }}>
            <span style={{ ...panelHintStyle, whiteSpace: "nowrap" }}>Active when equals</span>
            <input
              type="text"
              value={fb.condition?.equals ?? ""}
              placeholder="any truthy value"
              onChange={(e) =>
                update({
                  condition: e.target.value === "" ? undefined : { equals: e.target.value },
                })
              }
              style={{ ...fieldInputStyle, flex: 1 }}
            />
          </div>
          <div style={{ display: "flex", gap: "var(--space-md)", marginTop: "var(--space-xs)", flexWrap: "wrap" }}>
            {colorPair("Active", "style_active")}
            {colorPair("Inactive", "style_inactive")}
          </div>
          <div style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)", marginTop: "var(--space-2xs)" }}>
            Each pair is background then text.
          </div>
        </>
      )}
    </div>
  );
}
