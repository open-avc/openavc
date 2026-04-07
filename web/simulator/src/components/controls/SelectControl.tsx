import type { SelectControlDef } from "../../store/api";

interface Props {
  control: SelectControlDef;
  state: Record<string, unknown>;
  onStateChange: (key: string, value: unknown) => void;
}

export function SelectControl({ control, state, onStateChange }: Props) {
  const current = String(state[control.key] ?? "");

  return (
    <div className="ctrl-select">
      {control.label && <span className="ctrl-label">{control.label}</span>}
      <div className="ctrl-select-options">
        {control.options.map((opt) => {
          const label = control.labels?.[opt] ?? String(opt);
          return (
            <button
              key={String(opt)}
              className={`ctrl-btn ${String(current) === String(opt) ? "active" : ""}`}
              onClick={() => onStateChange(control.key, opt)}
            >
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
