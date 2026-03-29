import type { UIElement } from "../../../api/types";
import { Image } from "lucide-react";
import { buildElementStyle } from "./styleHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

export function ImageRenderer({ element }: Props) {
  const src = element.src || "";

  const css = buildElementStyle(element.style, {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    width: "100%",
    height: "100%",
    overflow: "hidden",
  });

  if (!element.style.bg_color && !element.style.background_gradient) {
    css.backgroundColor = "transparent";
  }
  if (!element.style.border_radius) css.borderRadius = "8px";

  return (
    <div style={css}>
      {src ? (
        <img
          src={src}
          alt={element.label || ""}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "contain",
          }}
        />
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
