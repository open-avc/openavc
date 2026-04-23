import type { ProjectConfig, UIElement } from "../../../api/types";
import { ActionPicker } from "./ActionPicker";
import { VariableKeyPicker } from "../../shared/VariableKeyPicker";
import { useConnectionStore } from "../../../store/connectionStore";
import { getDriverDefinition } from "../../../api/driverClient";

interface SliderBindingEditorProps {
  value: Record<string, unknown> | null;
  project: ProjectConfig;
  element: UIElement;
  onChange: (value: Record<string, unknown>) => void;
  onElementChange: (patch: Partial<UIElement>) => void;
  onClear: () => void;
}

export function SliderBindingEditor({
  value,
  project,
  element,
  onChange,
  onElementChange,
  onClear,
}: SliderBindingEditorProps) {
  const valueKey = String(value?.key || "");
  const liveValue = useConnectionStore((s) => valueKey ? s.liveState[valueKey] : undefined);

  const isChangeBinding = value && value.action;

  const autoPopulateFromStateKey = async (key: string) => {
    const match = key.match(/^device\.([^.]+)\.(.+)$/);
    if (!match) return;
    const [, deviceId, varName] = match;
    const device = project.devices?.find((d) => d.id === deviceId);
    if (!device) return;
    try {
      const def = await getDriverDefinition(device.driver);
      const sv = def.state_variables?.[varName];
      if (sv && sv.min != null && sv.max != null) {
        const patch: Partial<UIElement> = {
          output_min: sv.min,
          output_max: sv.max,
        };
        if (element.min == null || element.min === 0) {
          patch.min = sv.min;
        }
        if (element.max == null || element.max === 100) {
          patch.max = sv.max;
        }
        if (sv.step != null) {
          patch.step = sv.step;
        }
        onElementChange(patch);
      }
    } catch {
      // Driver lookup failed — skip auto-populate
    }
  };

  if (isChangeBinding) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-sm)",
        }}
      >
        <label style={labelStyle}>Change Action</label>
        <ActionPicker value={value} project={project} onChange={onChange} />
        {value && (
          <button
            onClick={onClear}
            style={clearBtnStyle}
          >
            Remove Binding
          </button>
        )}
      </div>
    );
  }

  // Value source binding
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm)",
      }}
    >
      <div>
        <label style={labelStyle}>State Key (value source)</label>
        <VariableKeyPicker
          value={String(value?.key || "")}
          onChange={(key) => {
            onChange({ source: "state", key });
            autoPopulateFromStateKey(key);
          }}
          placeholder="Select state key..."
        />
        <div style={helpStyle}>
          Bind this slider to a device level or variable. The slider position will
          follow the live value.
        </div>
        {valueKey && liveValue !== undefined && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 8px", background: "var(--bg-surface)", borderRadius: 4, fontSize: 11, marginTop: 4 }}>
            <span style={{ color: "var(--text-muted)" }}>Current value:</span>
            <span style={{ fontWeight: 500 }}>{String(liveValue)}</span>
          </div>
        )}
      </div>

      {value && (
        <button onClick={onClear} style={clearBtnStyle}>
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

const clearBtnStyle: React.CSSProperties = {
  padding: "4px 8px",
  borderRadius: "var(--border-radius)",
  fontSize: "var(--font-size-sm)",
  color: "var(--color-error)",
  background: "transparent",
  border: "1px solid var(--border-color)",
  alignSelf: "flex-start",
  cursor: "pointer",
};
