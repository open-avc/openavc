import type { UIElement } from "../../../api/types";

export function GenericRenderer({ element }: { element: UIElement }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
        border: "1px dashed rgba(255,255,255,0.15)",
        borderRadius: "8px",
        color: "var(--text-muted)",
        fontSize: "12px",
        width: "100%",
        height: "100%",
        gap: "4px",
        userSelect: "none",
      }}
    >
      <span style={{ opacity: 0.6, textTransform: "uppercase", fontSize: 10 }}>
        {element.type}
      </span>
      {element.label && <span>{element.label}</span>}
    </div>
  );
}
