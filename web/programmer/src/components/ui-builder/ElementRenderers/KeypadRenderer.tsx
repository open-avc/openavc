import type { UIElement } from "../../../api/types";
import { buildElementStyle } from "./styleHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

/**
 * KeypadRenderer — mirrors panel.js renderKeypad().
 * Uses .panel-keypad + child classes from panel-elements.css.
 */
export function KeypadRenderer({ element }: Props) {
  const keypadStyle = element.keypad_style ?? "numeric";
  const showDisplay = element.show_display !== false;
  const maxDigits = element.digits ?? 4;
  const overrides = buildElementStyle(element.style);

  const keys =
    keypadStyle === "phone"
      ? ["1","2","3","4","5","6","7","8","9","*","0","#"]
      : ["1","2","3","4","5","6","7","8","9","C","0","\u23CE"];

  const displayText = "\u2014".repeat(maxDigits);

  return (
    <div
      className="panel-element panel-keypad"
      style={{ width: "100%", height: "100%", ...overrides }}
    >
      {element.label && (
        <div className="keypad-label">{element.label}</div>
      )}

      {showDisplay && (
        <div className="keypad-display">{displayText || "\u00A0"}</div>
      )}

      <div className="keypad-grid">
        {keys.map((key) => {
          let cls = "keypad-key";
          if (key === "C") cls += " keypad-clear";
          if (key === "\u23CE") cls += " keypad-enter";
          return (
            <div key={key} className={cls}>
              {key}
            </div>
          );
        })}
      </div>
    </div>
  );
}
