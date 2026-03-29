import { useState, useEffect } from "react";
import type { UIElement } from "../../../api/types";
import * as wsClient from "../../../api/wsClient";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

export function TextInputRenderer({ element, previewMode, liveState }: Props) {
  const [localValue, setLocalValue] = useState("");

  // Reset local value when exiting preview mode
  useEffect(() => {
    if (!previewMode) setLocalValue("");
  }, [previewMode]);

  const varBinding = element.bindings.variable as { key?: string } | undefined;
  const valBinding = element.bindings.value as { key?: string } | undefined;
  const stateKey = varBinding?.key || valBinding?.key;

  let displayValue = localValue;
  if (previewMode && stateKey) {
    const stateValue = liveState[stateKey];
    if (stateValue !== undefined && stateValue !== null) {
      displayValue = String(stateValue);
    }
  }

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setLocalValue(val);
    if (previewMode) {
      wsClient.send({
        type: "ui.change",
        element_id: element.id,
        value: val,
      });
    }
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        padding: "8px 12px",
        gap: "4px",
        width: "100%",
        height: "100%",
        justifyContent: "center",
      }}
    >
      {element.label && (
        <label style={{ fontSize: 12, color: "#cccccc" }}>
          {element.label}
        </label>
      )}
      <input
        type="text"
        value={displayValue}
        onChange={handleChange}
        placeholder={element.placeholder || ""}
        disabled={!previewMode}
        style={{
          width: "100%",
          padding: "6px 8px",
          borderRadius: "6px",
          border: "1px solid rgba(255,255,255,0.15)",
          background: String(element.style.bg_color || "#333"),
          color: String(element.style.text_color || "#fff"),
          fontSize: element.style.font_size
            ? `${element.style.font_size}px`
            : "14px",
          cursor: previewMode ? "text" : "default",
        }}
      />
    </div>
  );
}
