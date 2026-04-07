import type { IndicatorControlDef } from "../../store/api";

interface Props {
  control: IndicatorControlDef;
  state: Record<string, unknown>;
}

export function IndicatorControl({ control, state }: Props) {
  const value = state[control.key];
  const valueStr = String(value ?? "—");
  const color = control.color_map?.[valueStr];

  return (
    <div className="ctrl-indicator">
      <span className="ctrl-label">{control.label}</span>
      <span className="ctrl-indicator-value" style={color ? { color } : undefined}>
        {color && <span className="ctrl-indicator-dot" style={{ background: color }} />}
        {valueStr}
      </span>
    </div>
  );
}
