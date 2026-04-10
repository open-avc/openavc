import type { UIElement } from "../../../api/types";
import type { ValueBinding } from "../uiBuilderHelpers";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

export function SelectRenderer({ element, liveState }: Props) {
  const options = element.options ?? [];

  // Resolve the value source: "variable" binding (two-way) or "value" binding (read-only)
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

  // Evaluate per-option feedback styling
  let bgColor = String(element.style.bg_color || "#333");
  let textColor = String(element.style.text_color || "#fff");

  if (element.bindings.feedback) {
    const fb = element.bindings.feedback as {
      key?: string;
      style_map?: Record<string, Record<string, string>>;
    };
    if (fb.key && fb.style_map) {
      const stateValue = String(liveState[fb.key] ?? "");
      const matchStyle = fb.style_map[stateValue];
      if (matchStyle) {
        if (matchStyle.bg_color) bgColor = matchStyle.bg_color;
        if (matchStyle.text_color) textColor = matchStyle.text_color;
      }
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
      <select
        value={displayValue}
        disabled
        style={{
          width: "100%",
          padding: "6px 8px",
          borderRadius: "6px",
          border: "1px solid rgba(255,255,255,0.15)",
          background: bgColor,
          color: textColor,
          fontSize: element.style.font_size
            ? `${element.style.font_size}px`
            : "14px",
        }}
      >
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
