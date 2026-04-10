import type { UIElement } from "../../../api/types";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

export function KeypadRenderer({ element }: Props) {
  const maxDigits = element.digits ?? 4;
  const keypadStyle = element.keypad_style ?? "numeric";
  const showDisplay = element.show_display ?? true;

  // Build button grid rows
  const rows: string[][] = [
    ["1", "2", "3"],
    ["4", "5", "6"],
    ["7", "8", "9"],
  ];
  const bottomRow =
    keypadStyle === "phone" ? ["*", "0", "#"] : ["C", "0", "\u23CE"];

  const displayText = "\u2014".repeat(maxDigits);

  const textColor = String(element.style.text_color || "#ffffff");
  const fontSize = element.style.font_size
    ? Number(element.style.font_size)
    : 14;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        height: "100%",
        padding: "6px",
        gap: "4px",
        boxSizing: "border-box",
      }}
    >
      {/* Label */}
      {element.label && (
        <div
          style={{
            fontSize: Math.max(fontSize - 2, 10),
            color: "#cccccc",
            textAlign: "center",
            flexShrink: 0,
            lineHeight: 1.2,
          }}
        >
          {element.label}
        </div>
      )}

      {/* Display area */}
      {showDisplay && (
        <div
          style={{
            background: "#1a1a1a",
            borderRadius: "4px",
            padding: "6px 10px",
            fontFamily: "'Courier New', Courier, monospace",
            fontSize: Math.max(fontSize + 2, 16),
            color: textColor,
            textAlign: "right",
            minHeight: "28px",
            display: "flex",
            alignItems: "center",
            justifyContent: "flex-end",
            flexShrink: 0,
            border: "1px solid rgba(255,255,255,0.1)",
            overflow: "hidden",
          }}
        >
          {displayText || "\u00A0"}
        </div>
      )}

      {/* Keypad grid */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "3px",
          flex: 1,
          minHeight: 0,
        }}
      >
        {[...rows, bottomRow].map((row, rowIdx) => (
          <div
            key={rowIdx}
            style={{
              display: "flex",
              gap: "3px",
              flex: 1,
              minHeight: 0,
            }}
          >
            {row.map((key) => {
              const isAction = key === "C" || key === "\u23CE";
              return (
                <div
                  key={key}
                  style={{
                    flex: 1,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    background: "#3a3a3a",
                    borderRadius: "6px",
                    color: isAction ? "#90caf9" : textColor,
                    fontSize: "14px",
                    fontWeight: isAction ? 600 : 500,
                    userSelect: "none",
                    minHeight: 0,
                  }}
                >
                  {key}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
