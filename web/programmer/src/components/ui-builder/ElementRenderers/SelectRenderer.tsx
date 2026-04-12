import type { UIElement } from "../../../api/types";
import type { ValueBinding } from "../uiBuilderHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

/**
 * SelectRenderer — mirrors panel.js renderSelect().
 * Uses .panel-select from panel-elements.css.
 */
export function SelectRenderer({ element, liveState }: Props) {
  const options = element.options ?? [];

  const varBinding = element.bindings.variable as { key?: string } | undefined;
  const valBinding = element.bindings.value as unknown as ValueBinding | undefined;
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
      className="panel-element panel-select"
      style={{ width: "100%", height: "100%", justifyContent: "center" }}
    >
      {element.label && <label>{element.label}</label>}
      <select value={displayValue} disabled style={{ pointerEvents: "none" }}>
        {options.length === 0 && (
          <option value="">No options configured</option>
        )}
        {options.map((opt, i) => (
          <option key={i} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </div>
  );
}
