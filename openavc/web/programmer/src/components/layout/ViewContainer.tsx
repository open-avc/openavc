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
          // STRETCH, not center. A title that is a tab strip has to reach the
          // full height of the bar so its selected mark can ride the bar's own
          // bottom edge; centred, the mark floats in the middle of the header
          // with a second hairline 13px below it.
          alignItems: "stretch",
          justifyContent: "space-between",
          borderBottom: "1px solid var(--border-chrome)",
          flexShrink: 0,
        }}
      >
        <h1
          style={{
            // The h1 is the full height of the bar and centres its own text, so
            // a plain string title looks exactly as it did while a tab strip
            // inside it can reach top and bottom.
            display: "flex",
            alignItems: "center",
            margin: 0,
            fontSize: "var(--font-size-lg)",
            fontWeight: "var(--font-weight-semibold)",
          }}
        >
          {title}
        </h1>
        {actions && (
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>{actions}</div>
        )}
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
