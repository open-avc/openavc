import type { UIElement } from "../../../api/types";
import { buildElementStyle } from "./styleHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
}

export function SpacerRenderer({ element, previewMode }: Props) {
  const css = buildElementStyle(element.style, {
    width: "100%",
    height: "100%",
  });

  if (!element.style.bg_color && !element.style.background_gradient) {
    css.backgroundColor = "transparent";
  }

  // Show a visible indicator in edit mode so the spacer can be found
  if (!previewMode && !element.style.bg_color) {
    css.border = "1px dashed rgba(255,255,255,0.2)";
    css.borderRadius = "4px";
    css.display = "flex";
    css.alignItems = "center";
    css.justifyContent = "center";
  }

  return (
    <div style={css}>
      {!previewMode && !element.style.bg_color && (
        <span style={{ fontSize: 9, color: "rgba(255,255,255,0.25)", pointerEvents: "none" }}>
          Spacer
        </span>
      )}
    </div>
  );
}
