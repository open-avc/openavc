/**
 * The UI Builder's frame: the toolbar across the top, and below it three
 * columns side by side (the left panel, the canvas and the
 * right panel), each side panel resizable within its limits by the handle
 * between it and the canvas.
 *
 * Which parts are drawn is one rule, `frameParts`, which the page asks once as
 * it draws: preview takes away every part that edits (both side panels, and the
 * toolbar's buttons that open the Builder's dialogs) and gives the canvas the
 * whole width; Ctrl+E takes away the left panel alone, and the canvas keeps its
 * share. The frame draws what the rule says and holds no state of its own: what
 * fills each part, and the state that has to outlive a part taken away (Ctrl+E's
 * and the left panel's tab), are the page's.
 *
 * Each column carries an id and its place (`order`): the panel library lists
 * the columns in the order they mounted unless told their places, so a side
 * panel coming back after Ctrl+E or preview was listed after the canvas, and
 * each handle then resized the wrong two columns.
 */
import type { ReactNode } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

export interface FrameParts {
  /** The left panel: the palette and the outline. */
  left: boolean;
  /** The right panel: the Properties panel. */
  right: boolean;
  /** The buttons at the toolbar's end that open the Builder's dialogs. */
  dialogButtons: boolean;
  /** The canvas's share of the width as the columns are first laid out, in percent. */
  canvasSize: number;
}

/** What the frame draws, in preview or not, and with the palette open or not. */
export function frameParts(preview: boolean, paletteOpen: boolean): FrameParts {
  if (preview) return { left: false, right: false, dialogButtons: false, canvasSize: 100 };
  return { left: paletteOpen, right: true, dialogButtons: true, canvasSize: 60 };
}

const HANDLE_STYLE = { width: 4, background: "var(--border-color)", cursor: "col-resize" };

export interface BuilderFrameProps {
  parts: FrameParts;
  toolbar: ReactNode;
  left: ReactNode;
  canvas: ReactNode;
  right: ReactNode;
}

export function BuilderFrame({ parts, toolbar, left, canvas, right }: BuilderFrameProps) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {toolbar}
      <PanelGroup direction="horizontal" style={{ flex: 1 }}>
        {parts.left && (
          <>
            <Panel id="builder-left" order={1} defaultSize={15} minSize={10} maxSize={25}>
              {left}
            </Panel>
            <PanelResizeHandle style={HANDLE_STYLE} />
          </>
        )}
        <Panel id="builder-canvas" order={2} defaultSize={parts.canvasSize}>{canvas}</Panel>
        {parts.right && (
          <>
            <PanelResizeHandle style={HANDLE_STYLE} />
            <Panel id="builder-right" order={3} defaultSize={25} minSize={15} maxSize={40}>
              {right}
            </Panel>
          </>
        )}
      </PanelGroup>
    </div>
  );
}
