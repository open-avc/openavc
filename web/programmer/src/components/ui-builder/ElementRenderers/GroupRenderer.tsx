import type { CSSProperties } from "react";
import type { UIElement } from "../../../api/types";
import { buildElementStyle } from "./styleHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
}

export function GroupRenderer({ element }: Props) {
  const position = element.label_position || "top-left";
  const label = element.label || "";

  // Build container style from the element's style dict
  const css = buildElementStyle(element.style, {
    width: "100%",
    height: "100%",
    display: "flex",
    flexDirection: "column",
    boxSizing: "border-box",
    position: "relative",
  });

  // Apply subtle defaults when the user hasn't set explicit values
  if (!element.style.bg_color && !element.style.background_gradient) {
    css.backgroundColor = "rgba(255, 255, 255, 0.03)";
  }
  if (!element.style.border_width) {
    css.borderWidth = "1px";
    css.borderStyle = "solid";
    css.borderColor = "rgba(255, 255, 255, 0.12)";
  }
  if (element.style.border_radius == null) {
    css.borderRadius = "6px";
  }

  // Label alignment mapping
  const alignMap: Record<string, CSSProperties["textAlign"]> = {
    "top-left": "left",
    "top-center": "center",
    "top-right": "right",
    "bottom-left": "left",
    "bottom-center": "center",
    "bottom-right": "right",
  };

  const isBottom = position.startsWith("bottom");
  const textAlign = alignMap[position] || "left";

  const labelStyle: CSSProperties = {
    fontSize: "12px",
    color: "#888888",
    textTransform: "uppercase",
    letterSpacing: "1px",
    textAlign,
    padding: "6px 10px",
    lineHeight: "1",
    flexShrink: 0,
    userSelect: "none",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };

  const labelEl = label ? <div style={labelStyle}>{label}</div> : null;

  return (
    <div style={css}>
      {!isBottom && labelEl}
      <div style={{ flex: 1 }} />
      {isBottom && labelEl}
    </div>
  );
}
