import type { UIElement } from "../../../api/types";
import { buildElementStyle } from "./styleHelpers";
import { IconTextLayout } from "./ElementIcon";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

/**
 * CameraPresetRenderer — mirrors panel.js renderCameraPreset().
 * Panel uses .panel-button class for camera presets.
 */
export function CameraPresetRenderer({ element }: Props) {
  const presetNum = element.preset_number ?? "";
  const overrides = buildElementStyle(element.style);

  return (
    <div
      className="panel-element panel-button"
      style={{ width: "100%", height: "100%", flexDirection: "column", gap: 2, ...overrides }}
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
