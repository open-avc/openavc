import type { CSSProperties } from "react";

/** The treatment for the buttons in a view header's action corner.
 *
 * There are exactly two of them and the difference between them carries the
 * whole hierarchy: the primary action wears the sage, and everything beside it
 * wears a transparent ground and one hairline. That is the point -- when three
 * buttons are all filled, none of them is the one to press, and the accent has
 * been spent on the two that are not.
 *
 * These are style objects rather than a Button component on purpose. A shared
 * primitives layer is the real fix for the drift and it is refactoring, not
 * appearance; this is the smallest thing that makes every header agree.
 */
export const headerButton: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-xs)",
  height: 25,
  padding: "0 var(--space-md)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "transparent",
  color: "var(--text-secondary)",
  fontSize: "var(--font-size-sm)",
  cursor: "pointer",
  whiteSpace: "nowrap",
};

/** The one button in the corner with any weight. */
export const headerPrimaryButton: CSSProperties = {
  ...headerButton,
  border: "1px solid transparent",
  background: "var(--accent-bg)",
  color: "var(--text-on-accent-bg)",
  fontWeight: "var(--font-weight-semibold)",
};
