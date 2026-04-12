import type { UIElement } from "../../../api/types";
import { Image } from "lucide-react";
import { buildElementStyle } from "./styleHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

/**
 * ImageRenderer — mirrors panel.js renderImage().
 * Uses .panel-image from panel-elements.css.
 */
export function ImageRenderer({ element }: Props) {
  const src = element.src || "";

  // Per-element style overrides (bg, border, etc.)
  const overrides = buildElementStyle(element.style);

  return (
    <div
      className="panel-element panel-image"
      style={{ width: "100%", height: "100%", ...overrides }}
    >
      {src ? (
        <img src={src} alt={element.label || ""} />
      ) : (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: "4px",
            color: "rgba(255,255,255,0.3)",
          }}
        >
          <Image size={24} />
          <span style={{ fontSize: 11 }}>
            {element.label || "No image set"}
          </span>
        </div>
      )}
    </div>
  );
}
