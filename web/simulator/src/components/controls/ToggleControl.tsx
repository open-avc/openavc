import type { ToggleControlDef } from "../../store/api";

interface Props {
  control: ToggleControlDef;
  state: Record<string, unknown>;
  onStateChange: (key: string, value: unknown) => void;
}

export function ToggleControl({ control, state, onStateChange }: Props) {
  const active = Boolean(state[control.key]);

  return (
    <button
      className={`ctrl-btn ${active ? "active" : ""}`}
      onClick={() => onStateChange(control.key, !active)}
    >
      {active ? `${control.label}: ON` : `${control.label}: OFF`}
    </button>
  );
}
