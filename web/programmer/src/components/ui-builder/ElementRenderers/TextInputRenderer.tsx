import type { UIElement } from "../../../api/types";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

/**
 * TextInputRenderer — mirrors panel.js renderTextInput().
 * Uses .panel-text-input from panel-elements.css.
 */
export function TextInputRenderer({ element, liveState }: Props) {
  const varBinding = element.bindings.variable as { key?: string } | undefined;
  const valBinding = element.bindings.value as { key?: string } | undefined;
  const stateKey = varBinding?.key || valBinding?.key;

  let displayValue = "";
  if (stateKey) {
    const stateValue = liveState[stateKey];
    if (stateValue !== undefined && stateValue !== null) {
      displayValue = String(stateValue);
    }
  }

  return (
    <div
      className="panel-element panel-text-input"
      style={{ width: "100%", height: "100%", justifyContent: "center" }}
    >
      {element.label && <label>{element.label}</label>}
      <input
        type="text"
        value={displayValue}
        readOnly
        placeholder={element.placeholder || ""}
        style={{ pointerEvents: "none" }}
      />
    </div>
  );
}
