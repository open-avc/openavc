import { VariableKeyPicker } from "../../shared/VariableKeyPicker";
import { useConnectionStore } from "../../../store/connectionStore";

interface VariableBindingEditorProps {
  value: Record<string, unknown> | null;
  onChange: (value: Record<string, unknown>) => void;
  onClear: () => void;
}

export function VariableBindingEditor({
  value,
  onChange,
  onClear,
}: VariableBindingEditorProps) {
  const valueKey = String(value?.key || "");
  const liveValue = useConnectionStore((s) => valueKey ? s.liveState[valueKey] : undefined);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm)",
      }}
    >
      <div
        style={{
          fontSize: 11,
          color: "var(--text-muted)",
          lineHeight: 1.4,
          fontStyle: "italic",
        }}
      >
        Two-way binding: when the user changes this element, the value is
        updated. When the value changes from any source (device, macro, script),
        the element reflects it. You can bind to device properties or your own
        variables.
      </div>

      <div>
        <label style={labelStyle}>State Variable</label>
        <VariableKeyPicker
          value={String(value?.key || "")}
          onChange={(key) => onChange({ key })}
          placeholder="Select variable..."
        />
        {valueKey && liveValue !== undefined && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 8px", background: "var(--bg-surface)", borderRadius: 4, fontSize: 11, marginTop: 4 }}>
            <span style={{ color: "var(--text-muted)" }}>Current value:</span>
            <span style={{ fontWeight: 500 }}>{String(liveValue)}</span>
          </div>
        )}
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
            cursor: "pointer",
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
