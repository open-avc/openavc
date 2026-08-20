/**
 * The inspector for one dial: turn, press, and the value it adjusts.
 */
import { X, Trash2, Play } from "lucide-react";
import { useProjectStore } from "../../../store/projectStore";
import { VariableKeyPicker } from "../../shared/VariableKeyPicker";
import { IconPicker } from "../../ui-builder/IconPicker";
import { ActionListEditor, MeterFields } from "./FieldEditors";
import { dialTestBtnStyle, fieldInputStyle, panelHintStyle, panelLabelStyle } from "./styles";
import type { DialAdjust, DialAssignment } from "./types";

export function DialAssignmentPanel({
  dialIndex,
  dial,
  onUpdate,
  onClear,
  onClose,
  allowedActions,
  navigateOptions,
  onSimulate,
  onOpenStrip,
}: {
  dialIndex: number;
  dial: DialAssignment | undefined;
  onUpdate: (updates: Partial<DialAssignment>) => void;
  onClear: () => void;
  onClose: () => void;
  allowedActions?: string[];
  navigateOptions?: { value: string; label: string }[];
  // Workbench extra: fire real dial input (simulate_input) to test it.
  onSimulate?: (payload: Record<string, unknown>) => void;
  // Jump to the whole-strip zone editor.
  onOpenStrip?: () => void;
}) {
  const project = useProjectStore((s) => s.project);
  const adjust = dial?.adjust ?? {};
  const pressedAdjust = dial?.pressed_adjust ?? {};

  const updateAdjust = (patch: Partial<DialAdjust>) => {
    const next = { ...adjust, ...patch };
    // Strip empty fields so a cleared adjust disappears from the config
    if (!next.key) {
      onUpdate({ adjust: undefined });
    } else {
      onUpdate({ adjust: next });
    }
  };
  const updatePressedAdjust = (patch: Partial<DialAdjust>) => {
    const next = { ...pressedAdjust, ...patch };
    if (!next.key) {
      onUpdate({ pressed_adjust: undefined });
    } else {
      onUpdate({ pressed_adjust: next });
    }
  };

  const numberField = (
    label: string,
    field: "step" | "min" | "max",
    placeholder: string
  ) => (
    <div style={{ flex: 1 }}>
      <label style={panelHintStyle}>{label}</label>
      <input
        type="number"
        value={adjust[field] ?? ""}
        placeholder={placeholder}
        onChange={(e) => {
          const raw = e.target.value;
          updateAdjust({ [field]: raw === "" ? undefined : Number(raw) });
        }}
        style={{
          width: "100%", padding: "4px 6px",
          borderRadius: "var(--border-radius)",
          border: "1px solid var(--border-color)",
          background: "var(--bg-surface)", color: "var(--text-primary)",
          fontSize: "var(--font-size-sm)",
        }}
      />
    </div>
  );

  if (!project) {
    return (
      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Loading project...</div>
    );
  }

  return (
    <div
      style={{
        width: 300,
        flexShrink: 0,
        background: "var(--bg-surface)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
        padding: "var(--space-md)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-lg)",
        maxHeight: "100%",
        overflow: "auto",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h4 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600 }}>
          Dial {dialIndex + 1}
        </h4>
        <button onClick={onClose} style={{ color: "var(--text-muted)", cursor: "pointer" }}>
          <X size={14} />
        </button>
      </div>

      {/* Try it: real input through the same path as the hardware */}
      {onSimulate && (
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
          <span style={{ fontSize: 11, color: "var(--text-muted)", flex: 1 }}>Try it</span>
          <button
            onClick={() => onSimulate({ type: "dial_turn", index: dialIndex, amount: -1 })}
            title="Turn counter-clockwise"
            style={dialTestBtnStyle}
          >
            &#8634;
          </button>
          <button
            onClick={() => onSimulate({ type: "dial_push", index: dialIndex })}
            title="Press the dial"
            style={dialTestBtnStyle}
          >
            <Play size={11} />
          </button>
          <button
            onClick={() => {
              onSimulate({ type: "dial_push", index: dialIndex, pressed: true });
              setTimeout(
                () => onSimulate({ type: "dial_push", index: dialIndex, pressed: false }),
                700
              );
            }}
            title="Long-press the dial"
            style={dialTestBtnStyle}
          >
            Long
          </button>
          <button
            onClick={() => onSimulate({ type: "dial_turn", index: dialIndex, amount: 1 })}
            title="Turn clockwise"
            style={dialTestBtnStyle}
          >
            &#8635;
          </button>
        </div>
      )}

      {/* Label */}
      <div>
        <label style={panelLabelStyle}>Label</label>
        <input
          type="text"
          value={dial?.label ?? ""}
          placeholder="Shown on the touchscreen"
          onChange={(e) => onUpdate({ label: e.target.value || undefined })}
          style={{
            width: "100%", padding: "var(--space-sm) var(--space-md)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            background: "var(--bg-surface)", color: "var(--text-primary)",
            fontSize: "var(--font-size-sm)",
          }}
        />
      </div>

      {/* Readout — what the strip shows under this dial */}
      <div>
        <label style={panelLabelStyle}>Readout</label>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
          <div style={{ display: "flex", gap: "var(--space-sm)" }}>
            <div style={{ flex: 1 }}>
              <label style={panelHintStyle}>Icon (optional)</label>
              <IconPicker
                value={dial?.icon ?? ""}
                onChange={(icon) => onUpdate({ icon: icon || undefined })}
              />
            </div>
            <div style={{ width: 80 }}>
              <label style={panelHintStyle}>Unit</label>
              <input
                type="text"
                value={dial?.unit ?? ""}
                placeholder="dB, %"
                onChange={(e) => onUpdate({ unit: e.target.value || undefined })}
                style={fieldInputStyle}
              />
            </div>
          </div>
          <MeterFields
            meter={dial?.meter}
            bounds={dial?.adjust}
            allowAuto
            onChange={(meter) => onUpdate({ meter })}
          />
        </div>
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
          The strip under this dial shows the label, icon, live value, and
          level bar.
        </div>
      </div>

      {/* Adjust-a-value */}
      <div>
        <label style={panelLabelStyle}>Turning Adjusts a Value</label>
        <VariableKeyPicker
          value={adjust.key ?? ""}
          onChange={(key) => updateAdjust({ key })}
          placeholder="Pick a variable to adjust..."
        />
        {adjust.key && (
          <div style={{ display: "flex", gap: "var(--space-sm)", marginTop: "var(--space-sm)" }}>
            {numberField("Step", "step", "1")}
            {numberField("Min", "min", "none")}
            {numberField("Max", "max", "none")}
          </div>
        )}
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
          Each detent adds or subtracts the step, clamped to min/max. Use a
          variable, then have a macro or trigger watch it to drive a device.
          The live value shows on the touchscreen under this dial.
        </div>
      </div>

      {/* Push-and-turn: fine adjust while the dial is held */}
      <div>
        <label style={panelLabelStyle}>Push + Turn Adjusts (Fine)</label>
        <VariableKeyPicker
          value={pressedAdjust.key ?? ""}
          onChange={(key) => updatePressedAdjust({ key })}
          placeholder="Pick a variable for fine adjust..."
        />
        {pressedAdjust.key && (
          <div style={{ display: "flex", gap: "var(--space-sm)", marginTop: "var(--space-sm)" }}>
            {(["step", "min", "max"] as const).map((field) => (
              <div key={field} style={{ flex: 1 }}>
                <label style={panelHintStyle}>
                  {field[0].toUpperCase() + field.slice(1)}
                </label>
                <input
                  type="number"
                  value={pressedAdjust[field] ?? ""}
                  placeholder={field === "step" ? "1" : "none"}
                  onChange={(e) =>
                    updatePressedAdjust({
                      [field]: e.target.value === "" ? undefined : Number(e.target.value),
                    })
                  }
                  style={fieldInputStyle}
                />
              </div>
            ))}
          </div>
        )}
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
          Turning while the dial is held uses this instead, a smaller step
          for fine trim. A push that turned never fires the press actions.
        </div>
      </div>

      {/* Turn / press actions */}
      <div>
        <label style={panelLabelStyle}>Clockwise Turn Actions</label>
        <ActionListEditor
          actions={dial?.cw ?? []}
          onChange={(cw) => onUpdate({ cw: cw.length ? cw : undefined })}
          project={project}
          allowedActions={allowedActions}
          navigateOptions={navigateOptions}
          addLabel="Add clockwise action"
        />
      </div>
      <div>
        <label style={panelLabelStyle}>Counter-Clockwise Turn Actions</label>
        <ActionListEditor
          actions={dial?.ccw ?? []}
          onChange={(ccw) => onUpdate({ ccw: ccw.length ? ccw : undefined })}
          project={project}
          allowedActions={allowedActions}
          navigateOptions={navigateOptions}
          addLabel="Add counter-clockwise action"
        />
      </div>
      <div>
        <label style={panelLabelStyle}>Press Actions</label>
        <ActionListEditor
          actions={dial?.press ?? []}
          onChange={(press) => onUpdate({ press: press.length ? press : undefined })}
          project={project}
          allowedActions={allowedActions}
          navigateOptions={navigateOptions}
          addLabel="Add press action"
        />
      </div>
      <div>
        <label style={panelLabelStyle}>Long-Press Actions</label>
        <ActionListEditor
          actions={dial?.long_press ?? []}
          onChange={(long_press) =>
            onUpdate({ long_press: long_press.length ? long_press : undefined })
          }
          project={project}
          allowedActions={allowedActions}
          navigateOptions={navigateOptions}
          addLabel="Add long-press action"
        />
        {(dial?.long_press?.length ?? 0) > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", marginTop: "var(--space-xs)" }}>
            <span style={panelHintStyle}>Hold threshold (ms)</span>
            <input
              type="number"
              value={dial?.hold_threshold_ms ?? ""}
              placeholder="500"
              onChange={(e) =>
                onUpdate({
                  hold_threshold_ms:
                    e.target.value === "" ? undefined : Number(e.target.value),
                })
              }
              style={{ ...fieldInputStyle, width: 90 }}
            />
          </div>
        )}
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
          With a long-press set, a quick push fires Press on release; holding
          past the threshold fires this instead.
        </div>
      </div>

      {/* Touch — the dial's strip zone is its touch surface */}
      <div>
        <label style={panelLabelStyle}>Touch (the readout on the strip)</label>
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: "var(--space-xs)" }}>
          Tapping this dial's readout presses the dial; long-tapping runs the
          long-press. Override either below.
        </div>
        <label style={panelHintStyle}>Tap actions (override)</label>
        <ActionListEditor
          actions={dial?.touch ?? []}
          onChange={(touch) => onUpdate({ touch: touch.length ? touch : undefined })}
          project={project}
          allowedActions={allowedActions}
          navigateOptions={navigateOptions}
          addLabel="Add tap action"
        />
        <label style={{ ...panelHintStyle, marginTop: "var(--space-xs)", display: "block" }}>
          Long-tap actions (override)
        </label>
        <ActionListEditor
          actions={dial?.long_touch ?? []}
          onChange={(long_touch) =>
            onUpdate({ long_touch: long_touch.length ? long_touch : undefined })
          }
          project={project}
          allowedActions={allowedActions}
          navigateOptions={navigateOptions}
          addLabel="Add long-tap action"
        />
        <label
          style={{
            display: "flex", alignItems: "center", gap: "var(--space-sm)",
            fontSize: "var(--font-size-sm)", cursor: "pointer",
            color: "var(--text-primary)", marginTop: "var(--space-sm)",
          }}
        >
          <input
            type="checkbox"
            checked={!!dial?.fader}
            onChange={(e) => onUpdate({ fader: e.target.checked || undefined })}
            disabled={adjust.min === undefined || adjust.max === undefined}
            style={{ accentColor: "var(--accent)" }}
          />
          Touch fader
        </label>
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
          {adjust.min === undefined || adjust.max === undefined
            ? "Set Min and Max on the adjust to enable: touching the readout will jump straight to that position."
            : "Touching the readout sets the value to the touched position (replaces the tap-presses-the-dial default)."}
        </div>
        {onOpenStrip && (
          <button
            onClick={onOpenStrip}
            style={{
              marginTop: "var(--space-sm)",
              display: "flex", alignItems: "center", justifyContent: "center",
              width: "100%", padding: "5px 10px",
              borderRadius: "var(--border-radius)",
              border: "1px solid var(--border-color)",
              background: "var(--bg-hover)", color: "var(--text-secondary)",
              fontSize: 12, cursor: "pointer",
            }}
          >
            Customize the whole strip…
          </button>
        )}
      </div>

      {/* Clear */}
      <button
        onClick={onClear}
        style={{
          display: "flex", alignItems: "center", justifyContent: "center",
          gap: "var(--space-xs)", padding: "var(--space-sm)",
          borderRadius: "var(--border-radius)", background: "transparent",
          border: "1px solid var(--border-color)", color: "var(--color-error)",
          fontSize: "var(--font-size-sm)", cursor: "pointer",
        }}
      >
        <Trash2 size={12} />
        Clear Assignment
      </button>
    </div>
  );
}
