/**
 * Shared button binding editor — used by both the web UI Builder and the
 * Surface Configurator (Stream Deck, etc.).
 *
 * Provides a consistent interface for configuring:
 *   - Button mode (tap, toggle, hold repeat, tap/hold)
 *   - Press action (macro, device command, state set, navigate, script call)
 *   - Visual feedback (state-driven color/label changes)
 *   - Button label (static text)
 *
 * Mode-specific behavior:
 *   - Tap: shows "Press Action"
 *   - Toggle: shows "On Action" + "Off Action", uses feedback state to know current state
 *   - Hold Repeat: shows "Action" + repeat interval
 *   - Tap/Hold: shows "Tap Action" + "Long Press Action" + threshold
 */
import { useState } from "react";
import type { ProjectConfig } from "../../api/types";
import { ActionPicker } from "../ui-builder/BindingEditor/ActionPicker";
import { FeedbackBindingEditor } from "../ui-builder/BindingEditor/FeedbackBindingEditor";
import { VariableKeyPicker } from "./VariableKeyPicker";
import { useConnectionStore } from "../../store/connectionStore";

export interface ButtonBindings {
  press?: Record<string, unknown>[] | null;
  release?: Record<string, unknown>[] | null;
  hold?: Record<string, unknown>[] | null;
  feedback?: Record<string, unknown> | null;
}

interface ButtonBindingEditorProps {
  bindings: ButtonBindings;
  label?: string;
  project: ProjectConfig;
  onBindingsChange: (bindings: ButtonBindings) => void;
  onLabelChange?: (label: string) => void;
  showRelease?: boolean;
  showLabel?: boolean;
  showToggleLabels?: boolean;
}

export function ButtonBindingEditor({
  bindings,
  label,
  project,
  onBindingsChange,
  onLabelChange,
  showRelease = false,
  showLabel = true,
  showToggleLabels = false,
}: ButtonBindingEditorProps) {
  const [expandedSlot, setExpandedSlot] = useState<string | null>(null);

  // Press binding is an array; mode/toggle/hold config lives on the first action
  const pressArray: Record<string, unknown>[] = Array.isArray(bindings.press) ? bindings.press : [];
  const press: Record<string, unknown> = pressArray[0] ?? {};
  const extraActions: Record<string, unknown>[] = pressArray.slice(1);
  const currentMode = String(press.mode || "tap");

  // Extract nested actions for toggle and tap/hold
  const offAction = (press.off_action as Record<string, unknown>) ?? null;
  const holdAction = (press.hold_action as Record<string, unknown>) ?? null;

  // Toggle state tracking
  const toggleKey = String(press.toggle_key || "");
  const toggleValue = press.toggle_value;
  const toggleOnLabel = String(press.on_label || "");
  const toggleOffLabel = String(press.off_label || "");

  // Get live value for toggle state key
  const toggleLiveValue = useConnectionStore((s) => toggleKey ? s.liveState[toggleKey] : undefined);
  const toggleIsActive = toggleKey && toggleLiveValue !== undefined && toggleValue !== undefined
    ? String(toggleLiveValue).toLowerCase() === String(toggleValue).toLowerCase()
    : null;

  const updatePress = (patch: Record<string, unknown>) => {
    const updated = { ...press, ...patch };
    onBindingsChange({ ...bindings, press: [updated, ...extraActions] });
  };

  const updateFeedback = (value: Record<string, unknown> | null) => {
    const next = { ...bindings };
    if (value) {
      next.feedback = value;
    } else {
      delete next.feedback;
    }
    onBindingsChange(next);
  };

  const updateRelease = (value: Record<string, unknown> | null) => {
    const next = { ...bindings };
    if (value) {
      next.release = [value];
    } else {
      delete next.release;
    }
    onBindingsChange(next);
  };

  const handleModeChange = (mode: string) => {
    const updated: Record<string, unknown> = { ...press };
    // Set or clear mode
    if (mode === "tap") {
      delete updated.mode;
    } else {
      updated.mode = mode;
    }
    // Clean mode-specific fields
    if (mode !== "toggle") {
      delete updated.off_action; delete updated.toggle_key;
      delete updated.toggle_value; delete updated.on_label; delete updated.off_label;
    }
    if (mode !== "hold_repeat") delete updated.hold_repeat_ms;
    if (mode !== "tap_hold") { delete updated.hold_action; delete updated.hold_threshold_ms; }
    // When switching away from tap mode, drop extra actions
    const keepExtras = mode === "tap" ? extraActions : [];
    onBindingsChange({ ...bindings, press: [updated, ...keepExtras] });
  };

  const summarizeAction = (action: Record<string, unknown> | null | undefined): string => {
    if (!action) return "Not configured";
    if (action.action === "macro") return `Macro: ${action.macro}`;
    if (action.action === "device.command") return `${action.device}.${action.command}`;
    if (action.action === "state.set") return `Set ${action.key}`;
    if (action.action === "navigate") return `Go to ${action.page}`;
    if (action.action === "script.call") return `Call ${action.function}`;
    return String(action.action || "Configured");
  };

  // Build the list of sections to show based on mode
  type Section = { id: string; label: string; type: "action" | "feedback" | "action_nested" };
  const sections: Section[] = [];

  if (currentMode === "tap") {
    sections.push({ id: "press", label: "Press Action", type: "action" });
  } else if (currentMode === "toggle") {
    sections.push({ id: "press", label: "On Action", type: "action" });
    sections.push({ id: "off_action", label: "Off Action", type: "action_nested" });
  } else if (currentMode === "hold_repeat") {
    sections.push({ id: "press", label: "Action", type: "action" });
  } else if (currentMode === "tap_hold") {
    sections.push({ id: "press", label: "Tap Action", type: "action" });
    sections.push({ id: "hold_action", label: "Long Press Action", type: "action_nested" });
  }

  if (showRelease && currentMode === "tap") {
    sections.push({ id: "release", label: "Release Action", type: "action" });
  }

  sections.push({ id: "feedback", label: "Visual Feedback", type: "feedback" });

  // Get the action value for a section
  const getActionValue = (sectionId: string): Record<string, unknown> | null => {
    if (sectionId === "press") {
      // Return press binding minus mode/config fields (just the action)
      const { mode: _m, off_action: _o, hold_action: _h, hold_repeat_ms: _r, hold_threshold_ms: _t,
              toggle_key: _tk, toggle_value: _tv, on_label: _ol, off_label: _ofl, ...actionFields } = press;
      return Object.keys(actionFields).length > 0 ? actionFields as Record<string, unknown> : null;
    }
    if (sectionId === "off_action") return offAction;
    if (sectionId === "hold_action") return holdAction;
    if (sectionId === "release") {
      const releaseArr = Array.isArray(bindings.release) ? bindings.release : [];
      return releaseArr[0] ?? null;
    }
    return null;
  };

  const setActionValue = (sectionId: string, value: Record<string, unknown> | null) => {
    if (sectionId === "press") {
      // Merge action fields into press[0], keeping mode/config
      const modeFields: Record<string, unknown> = {};
      if (press.mode) modeFields.mode = press.mode;
      if (press.off_action) modeFields.off_action = press.off_action;
      if (press.hold_action) modeFields.hold_action = press.hold_action;
      if (press.hold_repeat_ms) modeFields.hold_repeat_ms = press.hold_repeat_ms;
      if (press.hold_threshold_ms) modeFields.hold_threshold_ms = press.hold_threshold_ms;
      if (press.toggle_key) modeFields.toggle_key = press.toggle_key;
      if (press.toggle_value !== undefined) modeFields.toggle_value = press.toggle_value;
      if (press.on_label) modeFields.on_label = press.on_label;
      if (press.off_label) modeFields.off_label = press.off_label;
      const updated = value ? { ...modeFields, ...value } : modeFields;
      const hasContent = Object.keys(updated).length > 0;
      onBindingsChange({ ...bindings, press: hasContent ? [updated, ...extraActions] : null });
    } else if (sectionId === "off_action") {
      updatePress({ off_action: value ?? undefined });
    } else if (sectionId === "hold_action") {
      updatePress({ hold_action: value ?? undefined });
    } else if (sectionId === "release") {
      updateRelease(value);
    }
  };

  const getSummary = (sectionId: string): string => {
    if (sectionId === "feedback") {
      const fb = bindings.feedback as Record<string, unknown> | undefined;
      return fb?.key ? `State: ${fb.key}` : "Not configured";
    }
    return summarizeAction(getActionValue(sectionId));
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-md)" }}>
      {/* Label */}
      {showLabel && onLabelChange && (
        <div>
          <label style={sectionLabelStyle}>Button Label</label>
          <input
            type="text"
            value={label ?? ""}
            placeholder="Text shown on the button"
            onChange={(e) => onLabelChange(e.target.value)}
            style={inputStyle}
          />
        </div>
      )}

      {/* Button Mode */}
      <div>
        <label style={sectionLabelStyle}>Button Mode</label>
        <select
          value={currentMode}
          onChange={(e) => handleModeChange(e.target.value)}
          style={inputStyle}
        >
          <option value="tap">Tap — fires once on press</option>
          <option value="toggle">Toggle — on/off based on current state</option>
          <option value="hold_repeat">Hold Repeat — fires repeatedly while held</option>
          <option value="tap_hold">Tap / Long Press — different actions by press duration</option>
        </select>

        {/* Mode-specific settings */}
        {currentMode === "hold_repeat" && (
          <div style={{ marginTop: "var(--space-xs)", display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
            <span style={hintStyle}>Repeat every</span>
            <input
              type="number"
              value={Number(press.hold_repeat_ms || 200)}
              min={50} max={2000} step={50}
              onChange={(e) => updatePress({ hold_repeat_ms: parseInt(e.target.value) || 200 })}
              style={{ ...inputStyle, width: 70 }}
            />
            <span style={hintStyle}>ms</span>
          </div>
        )}
        {currentMode === "tap_hold" && (
          <div style={{ marginTop: "var(--space-xs)", display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
            <span style={hintStyle}>Long press threshold</span>
            <input
              type="number"
              value={Number(press.hold_threshold_ms || 500)}
              min={200} max={3000} step={100}
              onChange={(e) => updatePress({ hold_threshold_ms: parseInt(e.target.value) || 500 })}
              style={{ ...inputStyle, width: 70 }}
            />
            <span style={hintStyle}>ms</span>
          </div>
        )}
        {currentMode === "toggle" && (
          <div style={{
            marginTop: "var(--space-sm)", padding: "var(--space-md)",
            borderRadius: "var(--border-radius)", border: "1px solid var(--border-color)",
            display: "flex", flexDirection: "column", gap: "var(--space-sm)",
          }}>
            <div>
              <label style={hintStyle}>State key to watch</label>
              <VariableKeyPicker
                value={toggleKey}
                onChange={(key) => {
                  // Auto-detect boolean and set toggle_value
                  const live = useConnectionStore.getState().liveState[key];
                  const isBool = live === true || live === false;
                  updatePress({
                    toggle_key: key,
                    toggle_value: isBool ? true : (live !== undefined ? live : ""),
                  });
                }}
                placeholder="Pick a state key..."
              />
            </div>
            {toggleKey && (
              <div>
                <label style={hintStyle}>Value that means "on"</label>
                {(toggleLiveValue === true || toggleLiveValue === false) ? (
                  <div style={{ display: "flex", gap: "var(--space-sm)" }}>
                    {[true, false].map((v) => (
                      <button
                        key={String(v)}
                        onClick={() => updatePress({ toggle_value: v })}
                        style={{
                          flex: 1, padding: "5px 10px", borderRadius: "var(--border-radius)",
                          fontSize: "var(--font-size-sm)", cursor: "pointer",
                          fontWeight: String(toggleValue) === String(v) ? 600 : 400,
                          background: String(toggleValue) === String(v) ? "var(--accent)" : "var(--bg-hover)",
                          color: String(toggleValue) === String(v) ? "var(--text-on-accent, #fff)" : "var(--text-secondary)",
                          border: "1px solid " + (String(toggleValue) === String(v) ? "var(--accent)" : "var(--border-color)"),
                        }}
                      >
                        {v ? "ON / True" : "OFF / False"}
                      </button>
                    ))}
                  </div>
                ) : (
                  <input
                    value={String(toggleValue ?? "")}
                    onChange={(e) => {
                      let parsed: unknown = e.target.value;
                      if (parsed === "true") parsed = true;
                      else if (parsed === "false") parsed = false;
                      updatePress({ toggle_value: parsed });
                    }}
                    placeholder="Value that means on"
                    style={inputStyle}
                  />
                )}
              </div>
            )}
            {toggleKey && showToggleLabels && (
              <div style={{ display: "flex", gap: "var(--space-sm)" }}>
                <div style={{ flex: 1 }}>
                  <label style={hintStyle}>On Label</label>
                  <input
                    value={toggleOnLabel}
                    onChange={(e) => updatePress({ on_label: e.target.value })}
                    placeholder="e.g. Turn Off"
                    style={inputStyle}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={hintStyle}>Off Label</label>
                  <input
                    value={toggleOffLabel}
                    onChange={(e) => updatePress({ off_label: e.target.value })}
                    placeholder="e.g. Turn On"
                    style={inputStyle}
                  />
                </div>
              </div>
            )}
            {toggleKey && toggleIsActive !== null && (
              <div style={{
                display: "flex", alignItems: "center", gap: "var(--space-sm)",
                padding: "4px 8px", borderRadius: "var(--border-radius)",
                background: "var(--bg-hover)", fontSize: 11,
              }}>
                <span style={{ color: "var(--text-muted)" }}>Current:</span>
                <span style={{ fontWeight: 600 }}>{String(toggleLiveValue)}</span>
                <span style={{
                  marginLeft: "auto", fontWeight: 500,
                  color: toggleIsActive ? "var(--color-success, #4caf50)" : "var(--text-muted)",
                }}>
                  {toggleIsActive ? "ON" : "OFF"}
                </span>
              </div>
            )}
            {!toggleKey && (
              <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
                Pick a state key so the button knows when to fire the On vs Off action.
              </div>
            )}
          </div>
        )}
      </div>

      {/* Sections */}
      {sections.map((section) => {
        const isExpanded = expandedSlot === section.id;
        const summary = getSummary(section.id);
        const isConfigured = summary !== "Not configured";

        return (
          <div
            key={section.id}
            style={{
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              overflow: "hidden",
            }}
          >
            <button
              onClick={() => setExpandedSlot(isExpanded ? null : section.id)}
              style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                width: "100%", padding: "6px 10px", fontSize: "var(--font-size-sm)",
                background: "var(--bg-surface)", textAlign: "left", cursor: "pointer",
              }}
            >
              <span style={{ fontWeight: 500 }}>{section.label}</span>
              <span style={{
                fontSize: 11, maxWidth: 160, overflow: "hidden",
                textOverflow: "ellipsis", whiteSpace: "nowrap",
                color: isConfigured ? "var(--accent)" : "var(--text-muted)",
              }}>
                {summary}
              </span>
            </button>

            {isExpanded && (
              <div style={{
                padding: "var(--space-sm)",
                background: "var(--bg-base, var(--bg-primary))",
                borderTop: "1px solid var(--border-color)",
              }}>
                {section.type === "feedback" ? (
                  <FeedbackBindingEditor
                    value={(bindings.feedback as Record<string, unknown>) ?? null}
                    onChange={(v) => updateFeedback(v)}
                    onClear={() => updateFeedback(null)}
                    showConditionalLabel={true}
                    showImageField={true}
                  />
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
                    <ActionPicker
                      value={getActionValue(section.id)}
                      project={project}
                      onChange={(v) => setActionValue(section.id, v)}
                    />
                    {getActionValue(section.id) && (
                      <button
                        onClick={() => setActionValue(section.id, null)}
                        style={{
                          padding: "4px 8px", borderRadius: "var(--border-radius)",
                          fontSize: "var(--font-size-sm)", color: "var(--color-error)",
                          background: "transparent", border: "1px solid var(--border-color)",
                          alignSelf: "flex-start", cursor: "pointer",
                        }}
                      >
                        Remove
                      </button>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}

      {/* Extra actions (tap mode only) */}
      {currentMode === "tap" && extraActions.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
          <label style={{ ...sectionLabelStyle, marginBottom: 0 }}>Additional Actions</label>
          {extraActions.map((act, i) => (
            <div
              key={i}
              style={{
                border: "1px solid var(--border-color)",
                borderRadius: "var(--border-radius)",
                padding: "var(--space-sm)",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-xs)" }}>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Action {i + 2}</span>
                <button
                  onClick={() => {
                    const newExtra = extraActions.filter((_, j) => j !== i);
                    onBindingsChange({ ...bindings, press: [press, ...newExtra] });
                  }}
                  style={{
                    padding: "2px 6px", borderRadius: "var(--border-radius)",
                    fontSize: 11, color: "var(--color-error)",
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
                onChange={(v) => {
                  const newExtra = [...extraActions];
                  newExtra[i] = v;
                  onBindingsChange({ ...bindings, press: [press, ...newExtra] });
                }}
              />
            </div>
          ))}
        </div>
      )}

      {/* Add another action button (tap mode only) */}
      {currentMode === "tap" && getActionValue("press") && (
        <button
          onClick={() => {
            onBindingsChange({ ...bindings, press: [...pressArray, { action: "" }] });
          }}
          style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            gap: 4, padding: "5px 10px",
            borderRadius: "var(--border-radius)",
            border: "1px dashed var(--border-color)",
            background: "transparent",
            color: "var(--text-muted)",
            fontSize: 12, cursor: "pointer",
          }}
        >
          + Add another action
        </button>
      )}
    </div>
  );
}

const sectionLabelStyle: React.CSSProperties = {
  fontSize: 12, fontWeight: 600,
  color: "var(--text-secondary)",
  marginBottom: "var(--space-xs)", display: "block",
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "var(--space-sm) var(--space-md)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-surface)",
  color: "var(--text-primary)",
  fontSize: "var(--font-size-sm)",
};

const hintStyle: React.CSSProperties = {
  fontSize: 11, color: "var(--text-muted)",
};
