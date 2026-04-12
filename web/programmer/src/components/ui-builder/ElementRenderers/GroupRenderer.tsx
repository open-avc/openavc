import type { CSSProperties } from "react";
import type { UIElement } from "../../../api/types";
import { buildElementStyle } from "./styleHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
}

/**
 * GroupRenderer — mirrors panel.js renderGroup().
 * Uses .panel-group + .group-label from panel-elements.css.
 */
export function GroupRenderer({ element }: Props) {
  const labelPos = element.label_position || "top-left";
  const overrides = buildElementStyle(element.style);

  // Label positioning (matches panel.js logic)
  const labelStyle: CSSProperties = {};
  if (labelPos.startsWith("top")) {
    labelStyle.top = 0;
  } else {
    labelStyle.bottom = 0;
  }
  if (labelPos.endsWith("left")) {
    labelStyle.left = 8;
  } else if (labelPos.endsWith("center")) {
    labelStyle.left = "50%";
    labelStyle.transform = "translateX(-50%)";
  } else if (labelPos.endsWith("right")) {
    labelStyle.right = 8;
  }

  return (
    <div
      className="panel-element panel-group"
      style={{ width: "100%", height: "100%", ...overrides }}
    >
      {element.label && (
        <div className="group-label" style={labelStyle}>
          {element.label}
        </div>
      )}
    </div>
  );
}
