/**
 * The screen the UI Builder draws on: the preset chosen in the toolbar, as the
 * Builder store holds it (`useChosenScreen`), which both previews draw at as it
 * is; and the canvas's screen, that preset turned for a portrait arrangement
 * (`canvasScreen`; `presetForOrientation` says why). The canvas column sizes
 * the canvas by it and the toolbar's preset list says it in its tooltip, so the
 * two cannot disagree about the size on screen.
 */
import type { UIPage } from "../../api/types";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import { layoutOrientation, presetForOrientation } from "./pageGeometry";
import { SCREEN_PRESETS } from "./uiBuilderHelpers";

export interface ScreenSize {
  width: number;
  height: number;
}

/** The screen preset chosen in the toolbar, if the store's index names one. */
export function useChosenScreen(): ScreenSize | undefined {
  return SCREEN_PRESETS[useUIBuilderStore((s) => s.screenPresetIndex)];
}

export function canvasScreen(chosen: ScreenSize | undefined, page: UIPage | null, layoutId: string | null): ScreenSize {
  return presetForOrientation(chosen, layoutOrientation(page ?? undefined, layoutId));
}
