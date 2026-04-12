import type { UIElement } from "../../../api/types";
import { buildElementStyle } from "./styleHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
}

/**
 * SpacerRenderer — mirrors panel.js renderSpacer().
 * Uses .panel-spacer from panel-elements.css.
 */
export function SpacerRenderer({ element, previewMode }: Props) {
  // Per-element style overrides (gradient, etc.)
  const overrides = buildElementStyle(element.style);

  // Show a visible indicator in edit mode so the spacer can be found
  if (!previewMode && !element.style.bg_color) {
    overrides.border = "1px dashed rgba(255,255,255,0.2)";
    overrides.borderRadius = "4px";
  }

  return (
    <div
      className="panel-element panel-spacer"
      style={{ width: "100%", height: "100%", ...overrides }}
    >
      {!previewMode && !element.style.bg_color && (
        <span style={{ fontSize: 9, color: "rgba(255,255,255,0.25)", pointerEvents: "none" }}>
          Spacer
        </span>
      )}
    </div>
  );
}
