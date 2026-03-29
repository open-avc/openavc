import type { UIElement } from "../../../api/types";
import * as wsClient from "../../../api/wsClient";
import { buildElementStyle } from "./styleHelpers";
import { IconTextLayout } from "./ElementIcon";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

export function CameraPresetRenderer({
  element,
  previewMode,
}: Props) {
  const handlePress = () => {
    if (!previewMode) return;
    wsClient.send({ type: "ui.press", element_id: element.id });
  };

  const handleRelease = () => {
    if (!previewMode) return;
    wsClient.send({ type: "ui.release", element_id: element.id });
  };

  const presetNum = element.preset_number ?? "";

  const css = buildElementStyle(element.style, {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    gap: "4px",
    cursor: previewMode ? "pointer" : "default",
    userSelect: "none",
    width: "100%",
    height: "100%",
    fontWeight: "500",
  });

  // Defaults
  if (!element.style.bg_color && !element.style.background_gradient) {
    css.backgroundColor = "#424242";
  }
  if (!element.style.text_color) css.color = "#CCCCCC";
  if (!element.style.border_radius) css.borderRadius = "8px";
  if (!element.style.font_size) css.fontSize = "14px";
  if (!element.style.padding && !element.style.padding_horizontal && !element.style.padding_vertical) {
    css.padding = "8px";
  }

  return (
    <div
      onMouseDown={handlePress}
      onMouseUp={handleRelease}
      style={css}
    >
      <IconTextLayout
        icon={element.icon}
        iconPosition={element.icon_position}
        iconSize={element.icon_size}
        iconColor={element.icon_color}
      >
        <>
          {presetNum !== "" && (
            <span style={{ fontSize: "1.2em", fontWeight: "bold" }}>
              {presetNum}{" "}
            </span>
          )}
          {element.label || "Preset"}
        </>
      </IconTextLayout>
    </div>
  );
}
