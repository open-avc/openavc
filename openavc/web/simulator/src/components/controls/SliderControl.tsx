import { useState } from "react";
import type { SliderControlDef } from "../../store/api";
import { useEditable } from "./useEditable";

interface Props {
  control: SliderControlDef;
  state: Record<string, unknown>;
  onStateChange: (key: string, value: unknown) => void;
}

export function SliderControl({ control, state, onStateChange }: Props) {
  const raw = Number(state[control.key] ?? control.min);
  const serverValue = Math.max(control.min, Math.min(control.max, raw));
  const step = control.step ?? (control.max - control.min > 1 ? 1 : 0.01);
  // Readout precision follows the step so fine-grained sliders don't round away
  const decimals = step >= 1 ? 0 : Math.min(4, (String(step).split(".")[1] ?? "1").length);

  // While the thumb is held, the slider shows where it was dragged to. The
  // write still goes out on every change (throttled upstream), so the device
  // follows the drag rather than only learning about it on release.
  const slider = useEditable(serverValue);

  // The readout is typeable, which is how an exact value survives a step
  // chosen for draggability -- 440 Hz on a tone slider that moves in 50s.
  const [typed, setTyped] = useState<string | null>(null);
  const shown = typed ?? slider.value.toFixed(decimals);

  const commitTyped = () => {
    if (typed === null) return;
    const parsed = Number(typed);
    if (Number.isFinite(parsed)) {
      const clamped = Math.max(control.min, Math.min(control.max, parsed));
      onStateChange(control.key, clamped);
    }
    setTyped(null);
  };

  return (
    <div className="ctrl-slider">
      {control.label && <span className="ctrl-label">{control.label}</span>}
      <input
        type="range"
        min={control.min}
        max={control.max}
        step={step}
        value={slider.value}
        onChange={(e) => {
          const next = Number(e.target.value);
          slider.edit(next);
          setTyped(null);
          onStateChange(control.key, next);
        }}
        // Release the drafted position on release, from either input device,
        // so the slider goes back to reporting the device.
        onPointerUp={slider.commit}
        onPointerCancel={slider.commit}
        onBlur={slider.commit}
        // The visible label is a sibling span, so name the control itself --
        // without this a screen reader (and a test) sees an unnamed range.
        aria-label={control.label || control.key}
      />
      <input
        className="value"
        value={shown}
        size={Math.max(4, shown.length)}
        onChange={(e) => setTyped(e.target.value)}
        onBlur={commitTyped}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") setTyped(null);
        }}
        aria-label={control.label ? `${control.label} value` : "value"}
      />
      {control.unit && <span className="unit">{control.unit}</span>}
    </div>
  );
}
