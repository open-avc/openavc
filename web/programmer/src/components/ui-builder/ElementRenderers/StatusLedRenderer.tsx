import type { UIElement } from "../../../api/types";
import type { ColorBinding } from "../uiBuilderHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

export function StatusLedRenderer({ element, previewMode, liveState }: Props) {
  let color = "#9E9E9E";
  let glowing = false;

  if (previewMode && element.bindings.color) {
    const cb = element.bindings.color as unknown as ColorBinding;
    if (cb.key) {
      const value = liveState[cb.key];
      const colorMap = cb.map || {};
      const defaultColor = cb.default || "#9E9E9E";
      color = colorMap[String(value)] || defaultColor;
      glowing = color !== defaultColor;
    }
  }

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: "100%",
        height: "100%",
      }}
    >
      <div
        style={{
          width: 20,
          height: 20,
          borderRadius: "50%",
          backgroundColor: color,
          boxShadow: glowing
            ? `0 0 10px ${color}`
            : "0 0 6px rgba(0,0,0,0.3)",
          transition: "background-color 0.3s, box-shadow 0.3s",
        }}
      />
    </div>
  );
}
