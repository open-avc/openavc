import type { UIElement } from "../../../api/types";
import * as wsClient from "../../../api/wsClient";
import { buildElementStyle } from "./styleHelpers";
import { IconTextLayout } from "./ElementIcon";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

export function PageNavRenderer({ element, previewMode }: Props) {
  const handleClick = () => {
    if (!previewMode || !element.target_page) return;
    wsClient.send({ type: "ui.page", page_id: element.target_page });
  };

  const css = buildElementStyle(element.style, {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "#2196F3",
    fontSize: "14px",
    border: "1px solid rgba(255,255,255,0.1)",
    borderRadius: "8px",
    cursor: previewMode ? "pointer" : "default",
    width: "100%",
    height: "100%",
    userSelect: "none",
  });

  return (
    <div onClick={handleClick} style={css}>
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
