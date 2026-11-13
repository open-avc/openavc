/**
 * How the two project lines across the top of the app are drawn: a failed
 * save (SaveFailedBanner) and a failed load (ProjectLoadFailedBanner), alike
 * so they read as one kind of message.
 */
import type { CSSProperties } from "react";

export const bannerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  padding: "6px 16px",
  background: "rgba(244,67,54,0.15)",
  color: "var(--color-error)",
  fontSize: "var(--font-size-sm)",
  borderBottom: "1px solid var(--color-error)",
  flexShrink: 0,
};

export const BANNER_TEXT_STYLE: CSSProperties = { flex: 1 };

const button: CSSProperties = {
  padding: "2px 10px",
  fontSize: 11,
  borderRadius: 3,
  background: "transparent",
  cursor: "pointer",
};

export const bannerButtonStyle: Record<"strong" | "quiet", CSSProperties> = {
  strong: { ...button, fontWeight: 600, border: "1px solid var(--color-error)", color: "var(--color-error)" },
  quiet: { ...button, border: "1px solid var(--border-color)", color: "var(--text-secondary)" },
};
