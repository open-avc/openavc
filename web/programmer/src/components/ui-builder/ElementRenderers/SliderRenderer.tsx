import { useState, useEffect } from "react";
import type { UIElement } from "../../../api/types";
import type { ValueBinding } from "../uiBuilderHelpers";
import * as wsClient from "../../../api/wsClient";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

export function SliderRenderer({ element, previewMode, liveState }: Props) {
  const [localValue, setLocalValue] = useState(element.min ?? 0);

  // Reset local value when exiting preview mode or when min changes
  useEffect(() => {
    if (!previewMode) setLocalValue(element.min ?? 0);
  }, [previewMode, element.min]);

  const varBinding = element.bindings.variable as { key?: string } | undefined;
  const valBinding = element.bindings.value as unknown as ValueBinding | undefined;
  const stateKey = varBinding?.key || valBinding?.key;

  let displayValue = localValue;
  if (previewMode && stateKey) {
    const stateValue = liveState[stateKey];
    if (stateValue !== undefined && stateValue !== null) {
      displayValue = Number(stateValue);
    }
  }

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = parseFloat(e.target.value);
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
        type="range"
        min={element.min ?? 0}
        max={element.max ?? 100}
        step={element.step ?? 1}
        value={displayValue}
        onChange={handleChange}
        disabled={!previewMode}
        style={{
          width: "100%",
          cursor: previewMode ? "pointer" : "default",
        }}
      />
    </div>
  );
}
