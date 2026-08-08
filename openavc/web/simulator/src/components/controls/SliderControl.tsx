import type { SliderControlDef } from "../../store/api";

interface Props {
  control: SliderControlDef;
  state: Record<string, unknown>;
  onStateChange: (key: string, value: unknown) => void;
}

export function SliderControl({ control, state, onStateChange }: Props) {
  const raw = Number(state[control.key] ?? control.min);
  const value = Math.max(control.min, Math.min(control.max, raw));
  const step = control.step ?? (control.max - control.min > 1 ? 1 : 0.01);
  // Readout precision follows the step so fine-grained sliders don't round away
  const decimals = step >= 1 ? 0 : Math.min(4, (String(step).split(".")[1] ?? "1").length);

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
        {value.toFixed(decimals)}
        {control.unit ? ` ${control.unit}` : ""}
      </span>
    </div>
  );
}
