/**
 * Whether a page hands the whole screen to markup the author wrote, as the
 * UI Builder shows it: the canvas draws the author's page in place of its
 * controls, the page's tab is marked, and the palette closes, since controls
 * placed on it are not drawn.
 *
 * It needs a file to be one: a page that says custom and names none still
 * draws its controls (panel.js keeps the same rule for the panel itself).
 */
import type { UIPage } from "../../api/types";

export function isCustomPage(page: Pick<UIPage, "render_mode" | "custom_file">): boolean {
  return page.render_mode === "custom" && !!page.custom_file;
}
