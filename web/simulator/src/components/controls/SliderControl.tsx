import type { SliderControlDef } from "../../store/api";

interface Props {
  control: SliderControlDef;
  state: Record<string, unknown>;
  onStateChange: (key: string, value: unknown) => void;
}

export function SliderControl({ control, state, onStateChange }: Props) {
  const value = Number(state[control.key] ?? control.min);
  const step = control.step ?? (Number.isInteger(control.min) && Number.isInteger(control.max) ? 1 : 0.1);

  return (
    <div className="ctrl-slider">
      {control.label && <span className="ctrl-label">{control.label}</span>}
      <input
        type="range"
        min={control.min}
        max={control.max}
        step={step}
        value={value}
        onChange={(e) => onStateChange(control.key, Number(e.target.value))}
      />
      <span className="value">
        {step >= 1 ? Math.round(value) : value.toFixed(1)}
        {control.unit ? ` ${control.unit}` : ""}
      </span>
    </div>
  );
}
