/**
 * The screen the UI Builder's canvas draws: the chosen preset, turned for a
 * portrait arrangement (`presetForOrientation` says why). The page sizes the
 * canvas by it and the toolbar's preset list says it in its tooltip, so the
 * two cannot disagree about the size on screen.
 */
import type { UIPage } from "../../api/types";
import { layoutOrientation, presetForOrientation } from "./pageGeometry";
import { SCREEN_PRESETS } from "./uiBuilderHelpers";

export function canvasScreen(
  presetIndex: number,
  page: UIPage | null,
  layoutId: string | null,
): { width: number; height: number } {
  return presetForOrientation(SCREEN_PRESETS[presetIndex], layoutOrientation(page ?? undefined, layoutId));
}
