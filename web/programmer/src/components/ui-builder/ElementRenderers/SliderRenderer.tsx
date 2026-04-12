import type { UIElement } from "../../../api/types";
import type { ValueBinding } from "../uiBuilderHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

function valueToFraction(value: number, min: number, max: number): number {
  if (max === min) return 0;
  return Math.max(0, Math.min(1, (value - min) / (max - min)));
}

/**
 * SliderRenderer — mirrors the DOM structure of panel.js renderSlider().
 * Uses .panel-slider CSS classes from panel-elements.css (shared with the panel).
 */
export function SliderRenderer({ element, liveState }: Props) {
  const min = element.min ?? 0;
  const max = element.max ?? 100;
  const step = element.step ?? 1;
  const isVertical = element.orientation === "vertical";
  const thumbSize = element.thumb_size ?? 44;
  const showValue = (element.style.show_value as boolean) ?? false;

  const varBinding = element.bindings.variable as { key?: string } | undefined;
  const valBinding = element.bindings.value as unknown as ValueBinding | undefined;
  const stateKey = varBinding?.key || valBinding?.key;

  let displayValue = min;
  if (stateKey) {
    const stateValue = liveState[stateKey];
    if (stateValue !== undefined && stateValue !== null) {
      displayValue = Number(stateValue);
    }
  }

  const fraction = valueToFraction(displayValue, min, max);
  const pct = fraction * 100;
  const decimals = step < 1 ? 1 : 0;

  // Same DOM structure as panel.js renderSlider():
  // div.panel-element.panel-slider[.panel-slider-vertical]
  //   label? (optional)
  //   div.slider-track-wrapper
  //     div.slider-track
  //       div.slider-fill
  //     input[type=range] (read-only in builder)
  //   div.slider-value? (optional)
  return (
    <div
      className={`panel-element panel-slider${isVertical ? " panel-slider-vertical" : ""}`}
      style={{ "--thumb-size": thumbSize + "px", width: "100%", height: "100%" } as React.CSSProperties}
    >
      {element.label && <label>{element.label}</label>}

      <div className="slider-track-wrapper">
        <div className="slider-track">
          <div
            className="slider-fill"
            style={isVertical ? { height: pct + "%" } : { width: pct + "%" }}
          />
        </div>
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={displayValue}
          readOnly
          style={{ pointerEvents: "none" }}
        />
      </div>

      {showValue && (
        <div className="slider-value">{displayValue.toFixed(decimals)}</div>
      )}
    </div>
  );
}
