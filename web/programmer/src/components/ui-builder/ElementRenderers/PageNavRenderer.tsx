import type { UIElement } from "../../../api/types";
import { buildElementStyle } from "./styleHelpers";
import { IconTextLayout } from "./ElementIcon";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

export function PageNavRenderer({ element }: Props) {
  const css = buildElementStyle(element.style, {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "#2196F3",
    fontSize: "14px",
    border: "1px solid rgba(255,255,255,0.1)",
    borderRadius: "8px",
    width: "100%",
    height: "100%",
    userSelect: "none",
  });

  return (
    <div style={css}>
      <IconTextLayout
        icon={element.icon}
        iconPosition={element.icon_position}
        iconSize={element.icon_size}
        iconColor={element.icon_color}
      >
        {element.label || element.target_page || "\u2192"}
      </IconTextLayout>
    </div>
  );
}
