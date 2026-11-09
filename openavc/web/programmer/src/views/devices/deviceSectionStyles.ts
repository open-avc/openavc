/**
 * The frame every section of a device's page is drawn in: the gap below a
 * section, its small uppercase heading, and the bordered surface its rows sit
 * on. One copy, so the sections stay alike when one of them changes.
 */
import type { CSSProperties } from "react";

export const sectionStyle: CSSProperties = {
  marginBottom: "var(--space-xl)",
};

export const sectionTitleStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
  marginBottom: "var(--space-md)",
  fontWeight: 600,
};

export const surfaceStyle: CSSProperties = {
  background: "var(--bg-surface)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
};
