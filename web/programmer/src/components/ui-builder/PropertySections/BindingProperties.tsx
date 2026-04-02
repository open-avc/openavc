import { useState } from "react";
import { Play, AlertTriangle, HelpCircle } from "lucide-react";
import type { UIElement, UIPage } from "../../../api/types";
import type { ProjectConfig } from "../../../api/types";
import { BINDING_SLOTS } from "../uiBuilderHelpers";
import { ButtonBindingEditor } from "../../shared/ButtonBindingEditor";
import type { ButtonBindings } from "../../shared/ButtonBindingEditor";
import { PressBindingEditor } from "../BindingEditor/PressBindingEditor";
import { TextBindingEditor } from "../BindingEditor/TextBindingEditor";
import { FeedbackBindingEditor } from "../BindingEditor/FeedbackBindingEditor";
import { ColorBindingEditor } from "../BindingEditor/ColorBindingEditor";
import { SliderBindingEditor } from "../BindingEditor/SliderBindingEditor";
import { SelectChangeEditor } from "../BindingEditor/SelectChangeEditor";
import { SelectFeedbackEditor } from "../BindingEditor/SelectFeedbackEditor";
import { VariableBindingEditor } from "../BindingEditor/VariableBindingEditor";
import { VariableKeyPicker } from "../../shared/VariableKeyPicker";
import * as api from "../../../api/restClient";
import { showSuccess, showError } from "../../../store/toastStore";

interface BindingPropertiesProps {
  element: UIElement;
  project: ProjectConfig;
  onChange: (patch: Partial<UIElement>) => void;
}

export function BindingProperties({
  element,
  project,
  onChange,
}: BindingPropertiesProps) {
  const [editingSlot, setEditingSlot] = useState<string | null>(null);
  const slots = BINDING_SLOTS[element.type] || [];

  if (slots.length === 0) {
    return (
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          padding: "var(--space-sm)",
        }}
      >
        No bindings available for this element type.
      </div>
    );
  }

  // Buttons use the shared ButtonBindingEditor
  if (element.type === "button") {
    const btnBindings: ButtonBindings = {
      press: element.bindings.press as Record<string, unknown>[] | undefined,
      release: element.bindings.release as Record<string, unknown>[] | undefined,
      hold: element.bindings.hold as Record<string, unknown>[] | undefined,
      feedback: element.bindings.feedback as Record<string, unknown> | undefined,
    };
    return (
      <ButtonBindingEditor
        bindings={btnBindings}
        project={project}
        onBindingsChange={(newBindings) => {
          const merged = { ...element.bindings };
          for (const [slot, val] of Object.entries(newBindings)) {
            if (val) {
              merged[slot] = val;
            } else {
              delete merged[slot];
            }
          }
          // Clean removed slots
          for (const slot of ["press", "release", "hold", "feedback"]) {
            if (!(slot in newBindings)) {
              delete merged[slot];
            }
          }
          onChange({ bindings: merged });
        }}
        showRelease={true}
        showLabel={false}
      />
    );
  }

  const handleBindingChange = (slot: string, value: unknown) => {
    const newBindings = { ...element.bindings };
    if (value === null || value === undefined) {
      delete newBindings[slot];
    } else {
      newBindings[slot] = value;
    }
    onChange({ bindings: newBindings });
  };

  const summarizeAction = (b: Record<string, unknown>): string => {
    if (b.action === "macro") return `Macro: ${b.macro}`;
    if (b.action === "device.command") return `${b.device}.${b.command}`;
    if (b.action === "state.set") return `Set ${b.key}`;
    if (b.action === "navigate") return `Go to ${b.page}`;
    return String(b.action || "Configured");
  };

  const getSlotSummary = (slot: string): string => {
    const binding = element.bindings[slot];
    if (!binding) return "Not configured";

    switch (slot) {
      case "variable": {
        const b = binding as Record<string, unknown>;
        return b.key ? String(b.key) : "Not configured";
      }
      case "press":
      case "release":
      case "hold":
      case "change":
      case "submit":
      case "route":
      case "select": {
        const arr = binding as Record<string, unknown>[];
        if (!Array.isArray(arr) || arr.length === 0) return "Not configured";
        const first = summarizeAction(arr[0]);
        return arr.length > 1 ? `${first} +${arr.length - 1} more` : first;
      }
      case "items": {
        const b = binding as Record<string, unknown>;
        return b.key_pattern ? String(b.key_pattern) : "Not configured";
      }
      case "meter":
      case "feedback":
      case "text":
      case "selected":
      case "color":
      case "value": {
        const b = binding as Record<string, unknown>;
        return b.key ? `State: ${b.key}` : "Not configured";
      }
      default:
        return "Configured";
    }
  };

  const renderEditor = (slot: string) => {
    switch (slot) {
      case "variable": {
        const binding = element.bindings[slot] as Record<string, unknown> | undefined;
        return (
          <VariableBindingEditor
            value={binding || null}
            project={project}
            onChange={(v) => handleBindingChange(slot, v)}
            onClear={() => handleBindingChange(slot, null)}
          />
        );
      }
      case "press":
      case "release":
      case "hold": {
        const actions = (element.bindings[slot] as Record<string, unknown>[] | undefined) ?? [];
        return (
          <PressBindingEditor
            value={actions}
            project={project}
            onChange={(v) => handleBindingChange(slot, v)}
            onClear={() => handleBindingChange(slot, null)}
          />
        );
      }
      case "change": {
        const changeBinding = element.bindings[slot];
        if (element.type === "select") {
          return (
            <SelectChangeEditor
              value={(changeBinding as Record<string, unknown>) || null}
              project={project}
              options={element.options ?? []}
              onChange={(v) => handleBindingChange(slot, v)}
              onClear={() => handleBindingChange(slot, null)}
            />
          );
        }
        const changeActions = (changeBinding as Record<string, unknown>[] | undefined) ?? [];
        return (
          <div>
            <PressBindingEditor
              value={changeActions}
              project={project}
              onChange={(v) => handleBindingChange(slot, v)}
              onClear={() => handleBindingChange(slot, null)}
              forChangeBinding
            />
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4, fontStyle: "italic" }}>
              Use <strong>$value</strong> in command parameters to reference the new value.
            </div>
          </div>
        );
      }
      case "text": {
        const binding = element.bindings[slot] as Record<string, unknown> | undefined;
        return (
          <TextBindingEditor
            value={binding || null}
            project={project}
            onChange={(v) => handleBindingChange(slot, v)}
            onClear={() => handleBindingChange(slot, null)}
          />
        );
      }
      case "feedback": {
        const binding = element.bindings[slot] as Record<string, unknown> | undefined;
        if (element.type === "select") {
          return (
            <SelectFeedbackEditor
              value={binding || null}
              options={element.options ?? []}
              onChange={(v) => handleBindingChange(slot, v)}
              onClear={() => handleBindingChange(slot, null)}
            />
          );
        }
        return (
          <FeedbackBindingEditor
            value={binding || null}
            onChange={(v) => handleBindingChange(slot, v)}
            onClear={() => handleBindingChange(slot, null)}
          />
        );
      }
      case "color": {
        const binding = element.bindings[slot] as Record<string, unknown> | undefined;
        return (
          <ColorBindingEditor
            value={binding || null}
            onChange={(v) => handleBindingChange(slot, v)}
            onClear={() => handleBindingChange(slot, null)}
          />
        );
      }
      case "value": {
        const binding = element.bindings[slot] as Record<string, unknown> | undefined;
        return (
          <SliderBindingEditor
            value={binding || null}
            project={project}
            onChange={(v) => handleBindingChange(slot, v)}
            onClear={() => handleBindingChange(slot, null)}
          />
        );
      }
      case "submit":
      case "select": {
        const slotActions = (element.bindings[slot] as Record<string, unknown>[] | undefined) ?? [];
        return (
          <div>
            <PressBindingEditor
              value={slotActions}
              project={project}
              onChange={(v) => handleBindingChange(slot, v)}
              onClear={() => handleBindingChange(slot, null)}
            />
            {slot === "submit" && (
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4, fontStyle: "italic" }}>
                Use $value in command parameters to reference the submitted value.
              </div>
            )}
            {slot === "select" && (
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4, fontStyle: "italic" }}>
                Action triggered when a list item is selected. Use $value for the selected item's value.
              </div>
            )}
          </div>
        );
      }
      case "route": {
        const routeActions = (element.bindings[slot] as Record<string, unknown>[] | undefined) ?? [];
        return (
          <div>
            <PressBindingEditor
              value={routeActions}
              project={project}
              onChange={(v) => handleBindingChange(slot, v)}
              onClear={() => handleBindingChange(slot, null)}
            />
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4, fontStyle: "italic" }}>
              Use $input and $output in command parameters to reference the routed input/output numbers.
            </div>
          </div>
        );
      }
      case "selected": {
        const binding = element.bindings[slot] as Record<string, unknown> | undefined;
        return (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>Selected Item Key</div>
            <VariableKeyPicker
              value={String(binding?.key || "")}
              onChange={(key) => handleBindingChange(slot, { source: "state", key })}
              placeholder="Select state key..."
            />
            <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
              Two-way binding: when the user selects a list item, this key is updated.
              When the key changes externally, the list selection follows.
            </div>
            {binding && (
              <button
                onClick={() => handleBindingChange(slot, null)}
                style={{ fontSize: 11, color: "var(--color-danger)", background: "none", border: "none", cursor: "pointer", textAlign: "left", padding: 0 }}
              >
                Remove Binding
              </button>
            )}
          </div>
        );
      }
      case "items": {
        const binding = element.bindings[slot] as Record<string, unknown> | undefined;
        return (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>Key Pattern</div>
            <input
              value={String(binding?.key_pattern || "")}
              onChange={(e) => handleBindingChange(slot, { ...binding, source: "state", key_pattern: e.target.value })}
              placeholder="device.matrix.input_*_name"
              style={{ fontSize: 12, padding: "4px 6px" }}
            />
            <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
              State key pattern to populate list items dynamically. Use * as wildcard.
            </div>
            {binding && (
              <button
                onClick={() => handleBindingChange(slot, null)}
                style={{ fontSize: 11, color: "var(--color-danger)", background: "none", border: "none", cursor: "pointer", textAlign: "left", padding: 0 }}
              >
                Remove Binding
              </button>
            )}
          </div>
        );
      }
      case "meter": {
        const binding = element.bindings[slot] as Record<string, unknown> | undefined;
        return (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>Meter Source</div>
            <VariableKeyPicker
              value={String(binding?.key || "")}
              onChange={(key) => handleBindingChange(slot, { source: "state", key })}
              placeholder="Select meter state key..."
            />
            <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
              Read-only state key for the integrated level meter display.
            </div>
            {binding && (
              <button
                onClick={() => handleBindingChange(slot, null)}
                style={{ fontSize: 11, color: "var(--color-danger)", background: "none", border: "none", cursor: "pointer", textAlign: "left", padding: 0 }}
              >
                Remove Binding
              </button>
            )}
          </div>
        );
      }
      default:
        return null;
    }
  };

  const slotHelp: Record<string, string> = {
    press: "Action to perform when the element is pressed or clicked.",
    release: "Action to perform when the element is released (after press).",
    hold: "Action that repeats while the element is held down.",
    feedback: "Changes the element's appearance based on a state value (e.g., green when on, red when off).",
    text: "Displays a live state value as the element's text content.",
    color: "Changes the element's color based on a state value.",
    value: "Binds a slider's value to a state key for two-way control.",
    change: "Action to perform when the element's value changes (select, slider).",
    variable: "Binds the element to a project variable for two-way state sync.",
    submit: "Action to perform when the keypad value is submitted.",
    route: "Action to perform when a matrix route is made.",
    items: "Bind list items to a state key pattern for dynamic population.",
    select: "Action to perform when a list item is selected.",
    selected: "State key that tracks the currently selected item in the list.",
    meter: "Read-only state key for the fader's meter display.",
  };

  const isBindingIncomplete = (slot: string, binding: Record<string, unknown> | undefined): boolean => {
    if (!binding) return false;
    if (slot === "press" || slot === "release" || slot === "hold" || slot === "change" || slot === "submit" || slot === "route" || slot === "select") {
      if (binding.action === "device.command") return !binding.device || !binding.command;
      if (binding.action === "macro") return !binding.macro;
      if (binding.action === "state.set") return !binding.key;
      if (binding.action === "navigate") return !binding.page;
      return !binding.action;
    }
    if (slot === "feedback") return !binding.key;
    if (slot === "text") return !binding.key;
    if (slot === "variable") return !binding.key;
    if (slot === "selected") return !binding.key;
    if (slot === "items") return !binding.key_pattern;
    if (slot === "meter") return !binding.key;
    return false;
  };

  const testAction = async (binding: Record<string, unknown>) => {
    try {
      if (binding.action === "device.command" && binding.device && binding.command) {
        await api.sendCommand(String(binding.device), String(binding.command), (binding.params as Record<string, unknown>) ?? {});
        showSuccess("Command sent");
      } else if (binding.action === "macro" && binding.macro) {
        await api.executeMacro(String(binding.macro));
        showSuccess("Macro triggered");
      } else {
        showError("Cannot test this action type inline");
      }
    } catch (e) {
      showError(`Test failed: ${e}`);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm)",
      }}
    >
      {slots.map((slot) => {
        const isEditing = editingSlot === slot;
        const hasBinding = !!element.bindings[slot];
        const binding = element.bindings[slot] as Record<string, unknown> | undefined;
        const isTestable = (slot === "press" || slot === "release" || slot === "hold" || slot === "change" || slot === "submit" || slot === "route" || slot === "select") && hasBinding;
        const incomplete = hasBinding && isBindingIncomplete(slot, binding);

        return (
          <div
            key={slot}
            style={{
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              overflow: "hidden",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", background: "var(--bg-surface)" }}>
              <button
                onClick={() => setEditingSlot(isEditing ? null : slot)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  flex: 1,
                  padding: "6px 10px",
                  fontSize: "var(--font-size-sm)",
                  background: "transparent",
                  textAlign: "left",
                }}
              >
                <span style={{ fontWeight: 500, textTransform: "capitalize", display: "flex", alignItems: "center", gap: 4 }}>
                  {slot}
                  {slotHelp[slot] && (
                    <span title={slotHelp[slot]}>
                      <HelpCircle size={12} style={{ color: "var(--text-muted)", opacity: 0.6 }} />
                    </span>
                  )}
                </span>
                <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  {incomplete && (
                    <span title="Incomplete binding">
                      <AlertTriangle size={12} style={{ color: "var(--color-warning)" }} />
                    </span>
                  )}
                  <span
                    style={{
                      fontSize: 11,
                      color: incomplete ? "var(--color-warning)" : hasBinding ? "var(--accent)" : "var(--text-muted)",
                    }}
                  >
                    {incomplete ? "Incomplete" : getSlotSummary(slot)}
                  </span>
                </span>
              </button>
              {isTestable && binding && (
                <button
                  onClick={(e) => { e.stopPropagation(); testAction(binding); }}
                  title="Test this action"
                  style={{
                    display: "flex", alignItems: "center", justifyContent: "center",
                    padding: "4px 6px", background: "transparent", border: "none",
                    cursor: "pointer", color: "var(--accent)", flexShrink: 0,
                  }}
                >
                  <Play size={12} />
                </button>
              )}
            </div>
            {isEditing && (
              <div
                style={{
                  padding: "var(--space-sm)",
                  background: "var(--bg-base)",
                  borderTop: "1px solid var(--border-color)",
                }}
              >
                {renderEditor(slot)}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
