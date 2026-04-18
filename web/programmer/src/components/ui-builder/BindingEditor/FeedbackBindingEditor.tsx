/**
 * Visual feedback binding editor — configures state-driven appearance changes.
 *
 * Features:
 *   - Two-level state key picker (category → specific key)
 *   - Smart condition: boolean toggle, observed-values dropdown, or text input
 *   - Live value indicator showing current state
 *   - Active/inactive color pickers with preview
 *   - Conditional label text (active/inactive)
 */
import { useState, useEffect, useMemo } from "react";
import { Plus, Trash2 } from "lucide-react";
import { useProjectStore } from "../../../store/projectStore";
import { useConnectionStore } from "../../../store/connectionStore";
import { IconPicker } from "../IconPicker";
import { AssetPicker } from "../AssetPicker";
import { InlineColorPicker } from "../../shared/InlineColorPicker";

interface FeedbackBindingEditorProps {
  value: Record<string, unknown> | null;
  onChange: (value: Record<string, unknown>) => void;
  onClear: () => void;
  /** Show conditional label fields (for Stream Deck / physical buttons) */
  showConditionalLabel?: boolean;
  /** Show per-state image picker (for image-capable buttons) */
  showImageField?: boolean;
}

export function FeedbackBindingEditor({
  value,
  onChange,
  onClear,
  showConditionalLabel = false,
  showImageField = false,
}: FeedbackBindingEditorProps) {
  const project = useProjectStore((s) => s.project);
  const liveState = useConnectionStore.getState().liveState;

  const current = value || {
    source: "state",
    key: "",
    condition: { equals: "" },
    style_active: {},
    style_inactive: {},
  };

  const stateKey = String(current.key || "");
  const condition = (current.condition as Record<string, unknown>) || { equals: "" };
  const styleActive = (current.style_active as Record<string, string>) || {};
  const styleInactive = (current.style_inactive as Record<string, string>) || {};
  const labelActive = String(current.label_active ?? "");
  const labelInactive = String(current.label_inactive ?? "");

  const handleChange = (patch: Record<string, unknown>) => {
    onChange({ ...current, ...patch });
  };

  // ──── Two-Level State Key Picker ────

  const variables = project?.variables ?? [];
  const devices = project?.devices ?? [];

  // Build categories
  const categories = useMemo(() => {
    const cats: { id: string; label: string; keys: { key: string; label: string; value: unknown }[] }[] = [];

    // Variables
    const varKeys = variables.map((v) => ({
      key: `var.${v.id}`,
      label: v.label || v.id,
      value: liveState[`var.${v.id}`],
    }));
    if (varKeys.length > 0) {
      cats.push({ id: "variables", label: "Variables", keys: varKeys });
    }

    // Devices — group by device
    for (const d of devices) {
      const prefix = `device.${d.id}.`;
      const deviceKeys: { key: string; label: string; value: unknown }[] = [];
      for (const k of Object.keys(liveState)) {
        if (k.startsWith(prefix)) {
          deviceKeys.push({
            key: k,
            label: k.slice(prefix.length),
            value: liveState[k],
          });
        }
      }
      if (deviceKeys.length > 0) {
        cats.push({ id: `device:${d.id}`, label: d.name || d.id, keys: deviceKeys });
      }
    }

    // Plugin state
    const pluginKeys: { key: string; label: string; value: unknown }[] = [];
    for (const k of Object.keys(liveState)) {
      if (k.startsWith("plugin.")) {
        pluginKeys.push({ key: k, label: k, value: liveState[k] });
      }
    }
    if (pluginKeys.length > 0) {
      cats.push({ id: "plugins", label: "Plugins", keys: pluginKeys });
    }

    // System state
    const sysKeys: { key: string; label: string; value: unknown }[] = [];
    for (const k of Object.keys(liveState)) {
      if (k.startsWith("system.")) {
        sysKeys.push({ key: k, label: k.slice(7), value: liveState[k] });
      }
    }
    if (sysKeys.length > 0) {
      cats.push({ id: "system", label: "System", keys: sysKeys });
    }

    return cats;
  }, [variables, devices, liveState]);

  // Determine selected category from current key
  const derivedCategory = useMemo(() => {
    if (!stateKey) return "";
    for (const cat of categories) {
      if (cat.keys.some((k) => k.key === stateKey)) return cat.id;
    }
    return "";
  }, [stateKey, categories]);

  // Track category selection locally so it persists before a key is chosen
  const [localCategory, setLocalCategory] = useState(derivedCategory);

  // Sync local category when derived category changes (e.g. key set externally)
  useEffect(() => {
    if (derivedCategory) setLocalCategory(derivedCategory);
  }, [derivedCategory]);

  const selectedCategory = localCategory || derivedCategory;

  const categoryKeys = useMemo(() => {
    const cat = categories.find((c) => c.id === selectedCategory);
    return cat?.keys ?? [];
  }, [selectedCategory, categories]);

  // Live value of selected key
  const liveValue = stateKey ? liveState[stateKey] : undefined;

  // Detect value type for smart condition
  const observedValues = useMemo(() => {
    // Collect unique values we've seen for this key type
    const vals = new Set<string>();
    if (liveValue !== undefined) vals.add(String(liveValue));
    // For booleans, always show both
    if (liveValue === true || liveValue === false || liveValue === "true" || liveValue === "false") {
      vals.add("true");
      vals.add("false");
    }
    // For common AV states
    const v = String(liveValue ?? "").toLowerCase();
    if (v === "on" || v === "off") { vals.add("on"); vals.add("off"); }
    if (v === "open" || v === "closed") { vals.add("open"); vals.add("closed"); }
    if (v === "playing" || v === "stopped" || v === "paused") { vals.add("playing"); vals.add("stopped"); vals.add("paused"); }
    return Array.from(vals).sort();
  }, [liveValue]);

  const isBooleanKey = liveValue === true || liveValue === false;

  // Is the condition currently met?
  const conditionMet = useMemo(() => {
    if (!stateKey || liveValue === undefined) return null;
    const expected = condition.equals;
    if (expected === undefined || expected === "") return null;
    return String(liveValue).toLowerCase() === String(expected).toLowerCase();
  }, [stateKey, liveValue, condition.equals]);

  // Multi-state mode detection
  const isMultiState = !!(current.states as Record<string, unknown> | undefined);
  const statesMap = (current.states as Record<string, Record<string, unknown>>) || {};
  const defaultState = String(current.default_state ?? "");

  const handleAddState = () => {
    const newKey = `state_${Object.keys(statesMap).length + 1}`;
    handleChange({
      states: { ...statesMap, [newKey]: { bg_color: "#424242", label: newKey } },
      default_state: defaultState || newKey,
      // Clear legacy fields when switching to multi-state
      condition: undefined,
      style_active: undefined,
      style_inactive: undefined,
    });
  };

  const handleRemoveState = (key: string) => {
    const next = { ...statesMap };
    delete next[key];
    const patch: Record<string, unknown> = { states: next };
    if (defaultState === key) {
      patch.default_state = Object.keys(next)[0] || "";
    }
    handleChange(patch);
  };

  const handleUpdateStateAppearance = (stateKey2: string, patch: Record<string, unknown>) => {
    handleChange({
      states: {
        ...statesMap,
        [stateKey2]: { ...statesMap[stateKey2], ...patch },
      },
    });
  };

  const handleRenameState = (oldKey: string, newKey: string) => {
    if (newKey === oldKey || !newKey) return;
    const next: Record<string, Record<string, unknown>> = {};
    for (const [k, v] of Object.entries(statesMap)) {
      next[k === oldKey ? newKey : k] = v;
    }
    const patch: Record<string, unknown> = { states: next };
    if (defaultState === oldKey) patch.default_state = newKey;
    handleChange(patch);
  };

  const switchToMultiState = () => {
    handleChange({
      states: {
        on: { bg_color: "#4CAF50", text_color: "#ffffff", label: "ON" },
        off: { bg_color: "#424242", text_color: "#999999", label: "OFF" },
      },
      default_state: "off",
      condition: undefined,
      style_active: undefined,
      style_inactive: undefined,
    });
  };

  const switchToSimple = () => {
    handleChange({
      states: undefined,
      default_state: undefined,
      condition: { equals: "" },
      style_active: {},
      style_inactive: {},
    });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-md)" }}>

      {/* Step 1: Category picker */}
      <div>
        <label style={labelStyle}>Source</label>
        <select
          value={selectedCategory}
          onChange={(e) => {
            const newCat = e.target.value;
            setLocalCategory(newCat);
            // When category changes, clear the key
            handleChange({ key: "" });
          }}
          style={selectStyle}
        >
          <option value="">Select a source...</option>
          {categories.map((cat) => (
            <option key={cat.id} value={cat.id}>{cat.label}</option>
          ))}
        </select>
      </div>

      {/* Step 2: Specific key picker */}
      {selectedCategory && (
        <div>
          <label style={labelStyle}>State Key</label>
          <select
            value={stateKey}
            onChange={(e) => {
              const newKey = e.target.value;
              handleChange({ key: newKey });
              // Auto-set condition for booleans
              const newLive = liveState[newKey];
              if (newLive === true || newLive === false) {
                handleChange({ key: newKey, condition: { equals: true } });
              }
            }}
            style={selectStyle}
          >
            <option value="">Select state key...</option>
            {categoryKeys.map((k) => (
              <option key={k.key} value={k.key}>
                {k.label}{k.value !== undefined ? ` (${String(k.value)})` : ""}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Live value indicator */}
      {stateKey && liveValue !== undefined && (
        <div style={{
          display: "flex", alignItems: "center", gap: "var(--space-sm)",
          padding: "4px 8px", borderRadius: "var(--border-radius)",
          background: "var(--bg-hover)", fontSize: 12,
        }}>
          <span style={{ color: "var(--text-muted)" }}>Current value:</span>
          <span style={{ fontWeight: 600, fontFamily: "var(--font-mono, monospace)" }}>
            {String(liveValue)}
          </span>
          {conditionMet !== null && (
            <span style={{
              marginLeft: "auto", fontSize: 11, fontWeight: 500,
              color: conditionMet ? "var(--color-success, #4caf50)" : "var(--text-muted)",
            }}>
              {conditionMet ? "Active" : "Inactive"}
            </span>
          )}
        </div>
      )}

      {/* Mode toggle: Multi-State (default) vs Simple (legacy) */}
      {stateKey && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <label style={labelStyle}>
            Feedback Mode: <strong>{isMultiState ? "Multi-State" : "Simple (On/Off)"}</strong>
          </label>
          <button
            onClick={() => isMultiState ? switchToSimple() : switchToMultiState()}
            style={{
              fontSize: 11, color: "var(--text-muted)", background: "none",
              border: "none", cursor: "pointer", textDecoration: "underline",
              padding: 0,
            }}
          >
            {isMultiState ? "Switch to simple mode" : "Switch to multi-state"}
          </button>
        </div>
      )}

      {/* Multi-State Editor */}
      {stateKey && isMultiState && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <label style={labelStyle}>States</label>
            <button
              onClick={handleAddState}
              style={{
                display: "flex", alignItems: "center", gap: 3,
                padding: "2px 8px", borderRadius: "var(--border-radius)",
                fontSize: 11, color: "var(--accent)", cursor: "pointer",
                background: "transparent", border: "none",
              }}
            >
              <Plus size={12} /> Add State
            </button>
          </div>

          {Object.entries(statesMap).map(([sk, appearance]) => {
            const isDefault = sk === defaultState;
            const isLive = String(liveValue) === sk;
            return (
              <div
                key={sk}
                style={{
                  padding: "var(--space-sm)", borderRadius: "var(--border-radius)",
                  border: `1px solid ${isLive ? "var(--accent)" : "var(--border-color)"}`,
                  display: "flex", flexDirection: "column", gap: 6,
                  background: isLive ? "var(--accent-dim, rgba(33,150,243,0.05))" : undefined,
                }}
              >
                {/* State key row */}
                <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <input
                    value={sk}
                    onBlur={(e) => handleRenameState(sk, e.target.value.trim())}
                    onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                    style={{ flex: 1, padding: "3px 6px", fontSize: 12, fontWeight: 600, borderRadius: 3, border: "1px solid var(--border-color)" }}
                  />
                  <button
                    onClick={() => handleChange({ default_state: sk })}
                    title="Set as default"
                    style={{
                      padding: "2px 6px", fontSize: 10, borderRadius: 3, cursor: "pointer",
                      background: isDefault ? "var(--accent)" : "var(--bg-hover)",
                      color: isDefault ? "#fff" : "var(--text-muted)",
                      border: "none",
                    }}
                  >
                    {isDefault ? "Default" : "Set Default"}
                  </button>
                  <button
                    onClick={() => handleRemoveState(sk)}
                    style={{ padding: 2, color: "var(--text-muted)", background: "none", border: "none", cursor: "pointer" }}
                    title="Remove state"
                  >
                    <Trash2 size={12} />
                  </button>
                  {isLive && (
                    <span style={{ fontSize: 10, color: "var(--accent)", fontWeight: 600 }}>LIVE</span>
                  )}
                </div>

                {/* Appearance editors */}
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", flexWrap: "wrap" }}>
                  <span style={colorLabelStyle}>Background</span>
                  <InlineColorPicker
                    value={String(appearance.bg_color || "")}
                    onChange={(c) => handleUpdateStateAppearance(sk, { bg_color: c })}
                  />
                  <span style={colorLabelStyle}>Text</span>
                  <InlineColorPicker
                    value={String(appearance.text_color || "")}
                    onChange={(c) => handleUpdateStateAppearance(sk, { text_color: c })}
                  />
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                  <span style={{ ...colorLabelStyle, width: 56 }}>Label</span>
                  <input
                    value={String(appearance.label ?? "")}
                    onChange={(e) => handleUpdateStateAppearance(sk, { label: e.target.value })}
                    placeholder="Display text"
                    style={{ ...inputStyle, flex: 1 }}
                  />
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                  <span style={{ ...colorLabelStyle, width: 56 }}>Icon</span>
                  <IconPicker
                    value={String(appearance.icon || "")}
                    onChange={(v) => handleUpdateStateAppearance(sk, { icon: v || undefined })}
                  />
                </div>
                {showImageField && (
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                    <span style={{ ...colorLabelStyle, width: 56 }}>Image</span>
                    <AssetPicker
                      value={String(appearance.button_image || "")}
                      onChange={(v) => handleUpdateStateAppearance(sk, { button_image: v || undefined })}
                    />
                  </div>
                )}

                {/* Preview */}
                <div style={{
                  display: "flex", alignItems: "center", justifyContent: "center",
                  padding: "6px 12px", borderRadius: 4,
                  background: String(appearance.bg_color || "var(--bg-hover)"),
                  color: String(appearance.text_color || "var(--text-primary)"),
                  fontSize: 12, fontWeight: 500, minHeight: 28,
                }}>
                  {String(appearance.label || sk)}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Quick presets (simple mode only) */}
      {stateKey && !isMultiState && !styleActive.bg_color && !styleInactive.bg_color && (
        <div>
          <label style={labelStyle}>Quick Presets</label>
          <div style={{ display: "flex", gap: "var(--space-xs)", flexWrap: "wrap" }}>
            {[
              { label: "On / Off", condition: true, active: { bg_color: "#2e7d32", text_color: "#ffffff" }, inactive: { bg_color: "#c62828", text_color: "#ffffff" }, labelA: "ON", labelI: "OFF" },
              { label: "Connected", condition: true, active: { bg_color: "#1b5e20", text_color: "#ffffff" }, inactive: { bg_color: "#424242", text_color: "#9e9e9e" }, labelA: "Connected", labelI: "Disconnected" },
              { label: "Active / Idle", condition: true, active: { bg_color: "#1565c0", text_color: "#ffffff" }, inactive: { bg_color: "#263238", text_color: "#90a4ae" }, labelA: "Active", labelI: "Idle" },
            ].map((preset) => (
              <button
                key={preset.label}
                onClick={() => {
                  const patch: Record<string, unknown> = {
                    condition: { equals: preset.condition },
                    style_active: preset.active,
                    style_inactive: preset.inactive,
                  };
                  if (showConditionalLabel) {
                    patch.label_active = preset.labelA;
                    patch.label_inactive = preset.labelI;
                  }
                  handleChange(patch);
                }}
                style={{
                  padding: "4px 10px", borderRadius: "var(--border-radius)",
                  fontSize: 11, cursor: "pointer",
                  background: "var(--bg-hover)", border: "1px solid var(--border-color)",
                  color: "var(--text-secondary)",
                }}
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Step 3: Condition (simple mode only) */}
      {stateKey && !isMultiState && (
        <div>
          <label style={labelStyle}>Active when value equals</label>
          {isBooleanKey ? (
            // Boolean: simple toggle
            <div style={{ display: "flex", gap: "var(--space-sm)" }}>
              {["true", "false"].map((v) => (
                <button
                  key={v}
                  onClick={() => handleChange({ condition: { equals: v === "true" } })}
                  style={{
                    flex: 1, padding: "6px 12px", borderRadius: "var(--border-radius)",
                    fontSize: "var(--font-size-sm)", cursor: "pointer",
                    fontWeight: String(condition.equals) === v ? 600 : 400,
                    background: String(condition.equals) === v ? "var(--accent)" : "var(--bg-hover)",
                    color: String(condition.equals) === v ? "var(--text-on-accent, #fff)" : "var(--text-secondary)",
                    border: "1px solid " + (String(condition.equals) === v ? "var(--accent)" : "var(--border-color)"),
                  }}
                >
                  {v === "true" ? "ON / True" : "OFF / False"}
                </button>
              ))}
            </div>
          ) : observedValues.length > 0 ? (
            // Known values: dropdown + custom option
            <select
              value={String(condition.equals ?? "")}
              onChange={(e) => {
                let parsed: unknown = e.target.value;
                if (parsed === "true") parsed = true;
                else if (parsed === "false") parsed = false;
                else if (parsed !== "" && !isNaN(Number(parsed))) parsed = Number(parsed);
                handleChange({ condition: { equals: parsed } });
              }}
              style={selectStyle}
            >
              <option value="">Select value...</option>
              {observedValues.map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          ) : (
            // Fallback: text input
            <input
              value={String(condition.equals ?? "")}
              onChange={(e) => {
                let parsed: unknown = e.target.value;
                if (parsed === "true") parsed = true;
                else if (parsed === "false") parsed = false;
                else if (parsed !== "" && !isNaN(Number(parsed))) parsed = Number(parsed);
                handleChange({ condition: { equals: parsed } });
              }}
              placeholder="Value to match"
              style={inputStyle}
            />
          )}
        </div>
      )}

      {/* Step 4: Active appearance (simple mode only) */}
      {stateKey && !isMultiState && (
        <div>
          <label style={labelStyle}>When active (condition matches)</label>
          <div style={{
            padding: "var(--space-sm)", borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)", display: "flex", flexDirection: "column", gap: 6,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
              <span style={colorLabelStyle}>Background</span>
              <InlineColorPicker
                value={styleActive.bg_color || ""}
                onChange={(c) => handleChange({ style_active: { ...styleActive, bg_color: c } })}
              />
              <span style={colorLabelStyle}>Text</span>
              <InlineColorPicker
                value={styleActive.text_color || ""}
                onChange={(c) => handleChange({ style_active: { ...styleActive, text_color: c } })}
              />
            </div>
            {showConditionalLabel && (
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                <span style={{ ...colorLabelStyle, width: 56 }}>Label</span>
                <input
                  value={labelActive}
                  onChange={(e) => handleChange({ label_active: e.target.value })}
                  placeholder="e.g. ON"
                  style={{ ...inputStyle, flex: 1 }}
                />
              </div>
            )}
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
              <span style={{ ...colorLabelStyle, width: 56 }}>Icon</span>
              <IconPicker
                value={styleActive.icon || ""}
                onChange={(v) => handleChange({ style_active: { ...styleActive, icon: v || undefined } })}
              />
            </div>
            {showImageField && (
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                <span style={{ ...colorLabelStyle, width: 56 }}>Image</span>
                <AssetPicker
                  value={styleActive.button_image || ""}
                  onChange={(v) => handleChange({ style_active: { ...styleActive, button_image: v || undefined } })}
                />
              </div>
            )}
            {/* Preview */}
            <div style={{
              display: "flex", alignItems: "center", justifyContent: "center",
              padding: "6px 12px", borderRadius: 4, marginTop: 2,
              background: styleActive.bg_color || "var(--bg-hover)",
              color: styleActive.text_color || "var(--text-primary)",
              fontSize: 12, fontWeight: 500, minHeight: 28,
            }}>
              {showConditionalLabel && labelActive ? labelActive : "Active Preview"}
            </div>
          </div>
        </div>
      )}

      {/* Step 5: Inactive appearance (simple mode only) */}
      {stateKey && !isMultiState && (
        <div>
          <label style={labelStyle}>When inactive (condition doesn't match)</label>
          <div style={{
            padding: "var(--space-sm)", borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)", display: "flex", flexDirection: "column", gap: 6,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
              <span style={colorLabelStyle}>Background</span>
              <InlineColorPicker
                value={styleInactive.bg_color || ""}
                onChange={(c) => handleChange({ style_inactive: { ...styleInactive, bg_color: c } })}
              />
              <span style={colorLabelStyle}>Text</span>
              <InlineColorPicker
                value={styleInactive.text_color || ""}
                onChange={(c) => handleChange({ style_inactive: { ...styleInactive, text_color: c } })}
              />
            </div>
            {showConditionalLabel && (
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                <span style={{ ...colorLabelStyle, width: 56 }}>Label</span>
                <input
                  value={labelInactive}
                  onChange={(e) => handleChange({ label_inactive: e.target.value })}
                  placeholder="e.g. OFF"
                  style={{ ...inputStyle, flex: 1 }}
                />
              </div>
            )}
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
              <span style={{ ...colorLabelStyle, width: 56 }}>Icon</span>
              <IconPicker
                value={styleInactive.icon || ""}
                onChange={(v) => handleChange({ style_inactive: { ...styleInactive, icon: v || undefined } })}
              />
            </div>
            {showImageField && (
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                <span style={{ ...colorLabelStyle, width: 56 }}>Image</span>
                <AssetPicker
                  value={styleInactive.button_image || ""}
                  onChange={(v) => handleChange({ style_inactive: { ...styleInactive, button_image: v || undefined } })}
                />
              </div>
            )}
            {/* Preview */}
            <div style={{
              display: "flex", alignItems: "center", justifyContent: "center",
              padding: "6px 12px", borderRadius: 4, marginTop: 2,
              background: styleInactive.bg_color || "var(--bg-surface)",
              color: styleInactive.text_color || "var(--text-muted)",
              fontSize: 12, fontWeight: 500, minHeight: 28,
            }}>
              {showConditionalLabel && labelInactive ? labelInactive : "Inactive Preview"}
            </div>
          </div>
        </div>
      )}

      {value && (
        <button
          onClick={onClear}
          style={{
            padding: "4px 8px", borderRadius: "var(--border-radius)",
            fontSize: "var(--font-size-sm)", color: "var(--color-error)",
            background: "transparent", border: "1px solid var(--border-color)",
            alignSelf: "flex-start", cursor: "pointer",
          }}
        >
          Remove Feedback
        </button>
      )}
    </div>
  );
}


// ──── Styles ────

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  fontWeight: 500,
  color: "var(--text-muted)",
  marginBottom: 3,
};

const selectStyle: React.CSSProperties = {
  width: "100%",
  padding: "5px 8px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-surface)",
  color: "var(--text-primary)",
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "5px 8px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-surface)",
  color: "var(--text-primary)",
};

const colorLabelStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  width: 56,
  flexShrink: 0,
};
