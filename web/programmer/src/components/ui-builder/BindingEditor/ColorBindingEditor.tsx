import { Plus, X } from "lucide-react";
import { VariableKeyPicker } from "../../shared/VariableKeyPicker";
import { InlineColorPicker } from "../../shared/InlineColorPicker";
import { useConnectionStore } from "../../../store/connectionStore";

interface ColorBindingEditorProps {
  value: Record<string, unknown> | null;
  onChange: (value: Record<string, unknown>) => void;
  onClear: () => void;
}

export function ColorBindingEditor({
  value,
  onChange,
  onClear,
}: ColorBindingEditorProps) {
  const current = value || {
    source: "state",
    key: "",
    map: {},
    default: "#9E9E9E",
  };

  const colorMap = (current.map as Record<string, string>) || {};
  const defaultColor = String(current.default || "#9E9E9E");
  const stateKey = String(current.key || "");
  const liveValue = useConnectionStore((s) => stateKey ? s.liveState[stateKey] : undefined);
  const matchedColor = liveValue !== undefined ? (colorMap[String(liveValue)] || defaultColor) : undefined;

  const handleChange = (patch: Record<string, unknown>) => {
    onChange({ ...current, ...patch });
  };

  const addMapEntry = () => {
    // Generate a unique placeholder key to avoid overwriting existing entries
    let key = "value";
    let counter = 1;
    while (key in colorMap) {
      key = `value_${counter++}`;
    }
    handleChange({ map: { ...colorMap, [key]: "#4CAF50" } });
  };

  const removeMapEntry = (key: string) => {
    const newMap = { ...colorMap };
    delete newMap[key];
    handleChange({ map: newMap });
  };

  const updateMapEntry = (
    oldKey: string,
    newKey: string,
    color: string,
  ) => {
    // Prevent renaming to an existing key (would silently merge entries)
    if (newKey !== oldKey && newKey in colorMap) return;
    const entries = Object.entries(colorMap);
    const newMap: Record<string, string> = {};
    for (const [k, v] of entries) {
      if (k === oldKey) {
        newMap[newKey] = color;
      } else {
        newMap[k] = v;
      }
    }
    handleChange({ map: newMap });
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm)",
      }}
    >
      <div>
        <label style={labelStyle}>State Key</label>
        <VariableKeyPicker
          value={String(current.key || "")}
          onChange={(key) => handleChange({ key })}
          placeholder="Select state key..."
        />
        <div style={helpStyle}>
          Map device or variable values to colors. Example: bind to
          device.projector.power and map &apos;on&apos; to green, &apos;warming&apos; to orange, &apos;off&apos; to gray.
        </div>
      </div>

      {/* Live value indicator */}
      {stateKey && liveValue !== undefined && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 8px", background: "var(--bg-surface)", borderRadius: 4, fontSize: 11 }}>
          <span style={{ color: "var(--text-muted)" }}>Current value:</span>
          <span style={{ fontWeight: 500 }}>{String(liveValue)}</span>
          {matchedColor && (
            <>
              <span style={{ color: "var(--text-muted)" }}>→</span>
              <div style={{ width: 14, height: 14, borderRadius: 3, backgroundColor: matchedColor, border: "1px solid var(--border-color)" }} />
            </>
          )}
        </div>
      )}

      {/* Quick presets */}
      {Object.keys(colorMap).length === 0 && (
        <div>
          <label style={labelStyle}>Quick Presets</label>
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            {[
              { label: "On / Off", map: { "true": "#4CAF50", "false": "#F44336" }, def: "#9E9E9E" },
              { label: "Connected", map: { "true": "#4CAF50", "false": "#F44336", "connecting": "#FFC107" }, def: "#9E9E9E" },
              { label: "Power", map: { on: "#4CAF50", warming: "#FFC107", cooling: "#FFC107", off: "#9E9E9E" }, def: "#9E9E9E" },
            ].map((preset) => (
              <button
                key={preset.label}
                onClick={() => handleChange({ map: preset.map, default: preset.def })}
                style={{
                  padding: "3px 8px", borderRadius: 4, fontSize: 11,
                  color: "var(--accent)", background: "var(--accent-dim)",
                  border: "1px solid var(--border-color)", cursor: "pointer",
                }}
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>
      )}

      <div>
        <label style={labelStyle}>Default Color</label>
        <InlineColorPicker
          value={defaultColor}
          onChange={(c) => handleChange({ default: c })}
        />
      </div>

      <div>
        <label style={labelStyle}>Value → Color Map</label>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          {Object.entries(colorMap).map(([mapKey, mapColor], idx) => (
            <div
              key={idx}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <input
                value={mapKey}
                onChange={(e) =>
                  updateMapEntry(mapKey, e.target.value, mapColor)
                }
                placeholder="Value"
                style={{
                  width: 80,
                  padding: "3px 4px",
                  fontSize: 11,
                }}
              />
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                →
              </span>
              <InlineColorPicker
                value={mapColor}
                onChange={(c) => updateMapEntry(mapKey, mapKey, c)}
              />
              <button
                onClick={() => removeMapEntry(mapKey)}
                style={{
                  display: "flex",
                  padding: 2,
                  color: "var(--text-muted)",
                }}
              >
                <X size={12} />
              </button>
            </div>
          ))}
          <button
            onClick={addMapEntry}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              padding: "3px 8px",
              borderRadius: "var(--border-radius)",
              fontSize: 11,
              color: "var(--accent)",
              background: "transparent",
              border: "1px dashed var(--border-color)",
              alignSelf: "flex-start",
            }}
          >
            <Plus size={12} /> Add
          </button>
        </div>
      </div>

      {value && (
        <button
          onClick={onClear}
          style={{
            padding: "4px 8px",
            borderRadius: "var(--border-radius)",
            fontSize: "var(--font-size-sm)",
            color: "var(--color-error)",
            background: "transparent",
            border: "1px solid var(--border-color)",
            alignSelf: "flex-start",
          }}
        >
          Remove Binding
        </button>
      )}
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  color: "var(--text-muted)",
  marginBottom: 2,
};

const helpStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  lineHeight: 1.4,
  marginTop: 4,
  fontStyle: "italic",
};
