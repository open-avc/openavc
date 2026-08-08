import type { ReactNode } from "react";

interface ViewContainerProps {
  title: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}

export function ViewContainer({ title, actions, children }: ViewContainerProps) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      <header
        style={{
          height: "var(--header-height)",
          padding: "0 var(--space-lg)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          borderBottom: "1px solid var(--border-color)",
          flexShrink: 0,
        }}
      >
        <h1
          style={{
            fontSize: "var(--font-size-lg)",
            fontWeight: 600,
          }}
        >
          {title}
        </h1>
        {actions && <div style={{ display: "flex", gap: "var(--space-sm)" }}>{actions}</div>}
      </header>
      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflow: "auto",
          padding: "var(--space-lg)",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {children}
      </div>
    </div>
  );
}
