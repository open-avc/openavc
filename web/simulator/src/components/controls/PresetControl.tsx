import type { PresetControlDef } from "../../store/api";

interface Props {
  control: PresetControlDef;
  state: Record<string, unknown>;
  onStateChange: (key: string, value: unknown) => void;
}

export function PresetControl({ control, state, onStateChange }: Props) {
  const current = state[control.key];
  const presets: { value: string | number; label: string }[] = [];

  if (control.names) {
    control.names.forEach((name, i) => {
      presets.push({ value: control.count ? i + 1 : name, label: name });
    });
  } else if (control.count) {
    for (let i = 1; i <= control.count; i++) {
      presets.push({ value: i, label: String(i) });
    }
  }

  return (
    <div className="ctrl-presets">
      {control.label && <span className="ctrl-label">{control.label}</span>}
      <div className="ctrl-select-options">
        {presets.map((p) => (
          <button
            key={String(p.value)}
            className={`ctrl-btn ${String(current) === String(p.value) ? "active" : ""}`}
            onClick={() => onStateChange(control.key, p.value)}
          >
            {p.label}
          </button>
        ))}
      </div>
    </div>
  );
}
