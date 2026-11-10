/**
 * The UI Builder's middle column: the canvas for the page on screen, or a note
 * saying there are no pages yet.
 *
 * The canvas is drawn as the toolbar sets it: preview, the grid, the zoom and
 * the screen (the chosen preset turned for the arrangement being authored,
 * `useChosenScreen` and `canvasScreen`). Those are read from the Builder store
 * here, as the two side panels read what is selected, so a view setting reaches
 * the canvas without passing through the page. The page hands it the page on screen and what the
 * project holds for it.
 */
import type { MasterElement, UIPage } from "../../api/types";
import { Canvas } from "../../components/ui-builder/Canvas";
import { canvasScreen, useChosenScreen } from "../../components/ui-builder/canvasScreen";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import { BuilderNote } from "./BuilderNote";

export interface BuilderCanvasProps {
  /** The page on screen, or null when the project has none. */
  page: UIPage | null;
  /** The masters as the project holds them: none at all, rather than an
   *  empty list, when it has no list. */
  masters: MasterElement[] | undefined;
}

export function BuilderCanvas({ page, masters }: BuilderCanvasProps) {
  const previewMode = useUIBuilderStore((s) => s.previewMode);
  const showGrid = useUIBuilderStore((s) => s.showGrid);
  const zoom = useUIBuilderStore((s) => s.zoom);
  const chosen = useChosenScreen();
  const layoutId = useUIBuilderStore((s) => s.activeLayoutId);

  if (!page) return <BuilderNote>No pages yet. Click + to add a page.</BuilderNote>;
  const screen = canvasScreen(chosen, page, layoutId);
  return (
    <Canvas
      page={page}
      previewMode={previewMode}
      showGrid={showGrid}
      zoom={zoom}
      screenWidth={screen.width}
      screenHeight={screen.height}
      masterElements={masters}
    />
  );
}
