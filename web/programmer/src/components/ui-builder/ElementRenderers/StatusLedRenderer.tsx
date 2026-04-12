import type { UIElement } from "../../../api/types";
import type { ColorBinding } from "../uiBuilderHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

/**
 * StatusLedRenderer — mirrors panel.js renderStatusLed().
 * Uses .panel-status-led + .led-dot from panel-elements.css.
 */
export function StatusLedRenderer({ element, previewMode, liveState }: Props) {
  let color = "#9E9E9E";
  let glowing = false;

  if (previewMode && element.bindings.color) {
    const cb = element.bindings.color as unknown as ColorBinding;
    if (cb.key) {
      const value = liveState[cb.key];
      const colorMap = cb.map || {};
      const defaultColor = cb.default || "#9E9E9E";
      color = colorMap[String(value)] || defaultColor;
      glowing = color !== defaultColor;
    }
  }

  return (
    <div
      className="panel-element panel-status-led"
      style={{ width: "100%", height: "100%" }}
    >
      <div
        className={`led-dot${glowing ? " active" : ""}`}
        style={{ backgroundColor: color }}
      />
    </div>
  );
}
