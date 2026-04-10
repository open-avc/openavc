import type { UIElement } from "../../../api/types";
import type { ValueBinding } from "../uiBuilderHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

export function SliderRenderer({ element, liveState }: Props) {
  const varBinding = element.bindings.variable as { key?: string } | undefined;
  const valBinding = element.bindings.value as unknown as ValueBinding | undefined;
  const stateKey = varBinding?.key || valBinding?.key;

  let displayValue = element.min ?? 0;
  if (stateKey) {
    const stateValue = liveState[stateKey];
    if (stateValue !== undefined && stateValue !== null) {
      displayValue = Number(stateValue);
    }
  }

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
        readOnly
        style={{
          width: "100%",
          pointerEvents: "none",
        }}
      />
    </div>
  );
}
