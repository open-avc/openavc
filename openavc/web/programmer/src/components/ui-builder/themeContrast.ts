/**
 * The Theme Studio's contrast checks: seven pairs of a theme's colours, a text
 * colour on the background it is drawn over, each rated against WCAG's ratios
 * for normal text (AA from 4.5:1, AAA from 7:1). A colour the theme leaves unset
 * or sets empty is read as the one the panel draws in its place
 * (`CSS_VAR_FALLBACKS`), so clearing a colour is checked, not skipped. A colour
 * no ratio can be worked out for (a CSS keyword, anything see-through) rates
 * "na", which the section shows as neutral, never as a failure.
 */
import { contrastRatio, wcagLevel, CSS_VAR_FALLBACKS, type WcagLevel } from "./colorUtils";

export interface ContrastCheck {
  label: string;
  fg: string;
  bg: string;
  ratio: number | null;
  level: WcagLevel;
}

/** Each pair: its name, then the variables of the text colour and of the background. */
const PAIRS: readonly (readonly [label: string, fg: string, bg: string])[] = [
  ["Text on Background", "panel_text", "panel_bg"],
  ["Button Text on Button", "button_text", "button_bg"],
  ["Accent on Background", "accent", "panel_bg"],
  ["Button Text on Active", "button_text", "accent"],
  ["Danger on Background", "danger", "panel_bg"],
  ["Success on Background", "success", "panel_bg"],
  ["Warning on Background", "warning", "panel_bg"],
];

/** The colour the panel draws for a variable: the theme's, else the panel's own default, else none. */
function drawnColor(variables: Record<string, unknown>, key: string): string {
  return String(variables[key] ?? "") || CSS_VAR_FALLBACKS[key] || "";
}

/** Every pair the panel draws both colours of, rated, in the order above. */
export function contrastChecks(variables: Record<string, unknown>): ContrastCheck[] {
  const checks: ContrastCheck[] = [];
  for (const [label, fgKey, bgKey] of PAIRS) {
    const fg = drawnColor(variables, fgKey);
    const bg = drawnColor(variables, bgKey);
    // Every pair above has a panel default today; one added without would be skipped, not rated against "".
    if (fg && bg) {
      const ratio = contrastRatio(fg, bg);
      checks.push({ label, fg, bg, ratio, level: wcagLevel(ratio) });
    }
  }
  return checks;
}
