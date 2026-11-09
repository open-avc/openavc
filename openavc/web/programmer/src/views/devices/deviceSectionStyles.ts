/**
 * The frame a section of a device's page is drawn in: the gap below it, its
 * small uppercase heading, and the bordered surface its rows sit on. The page
 * itself, Send Command and Device Settings draw theirs from here (the page
 * hands the first two to the SSH key section); the other sections on the page
 * still write the same values out for themselves.
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
