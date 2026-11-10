/**
 * A line of muted text in the middle of the space it is given: what the UI
 * Builder says where there is nothing to draw yet (the project still loading,
 * a project with no pages), one look for both.
 */
import type { ReactNode } from "react";

export function BuilderNote({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        color: "var(--text-muted)",
      }}
    >
      {children}
    </div>
  );
}
