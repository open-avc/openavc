import { useEffect, useRef, useState } from "react";
import { getTunnelPrefix, type ThemeDefinition } from "../../api/restClient";
import { resolveProjectMatrices } from "../../api/matrixPreview";
import type { ProjectConfig } from "../../api/types";

/**
 * The real panel, in a box, at panel dimensions, scaled to fit the space it is
 * given.
 *
 * A dialog that changes how the panel looks -- the Theme Studio, the project
 * stylesheet editor -- covers the design canvas while it is open, so it has to
 * bring its own view of the panel or the author is editing blind. This is that
 * view: the same `/panel/?edit=1` document the canvas embeds, seeded and then
 * re-seeded over postMessage, so a change shows up in a frame without a save
 * and without a round trip to the server.
 *
 * `edit=1` is what keeps it honest as a preview rather than a live panel: the
 * panel opens no WebSocket in edit mode, so state is whatever `demoState`
 * carries and a control here cannot command a real room.
 */
export interface PanelPreviewFrameProps {
  /** The project as it stands in the editor, unsaved edits and all. */
  project: ProjectConfig;
  pageId: string | null;
  /** A working-copy theme applied without saving it. Null uses the project's own. */
  inlineTheme?: ThemeDefinition | null;
  /** State the panel should pretend it has, so controls draw with values in them. */
  demoState?: Record<string, unknown>;
  panelWidth: number;
  panelHeight: number;
  title?: string;
}

export function PanelPreviewFrame({
  project,
  pageId,
  inlineTheme = null,
  demoState,
  panelWidth,
  panelHeight,
  title = "Panel preview",
}: PanelPreviewFrameProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);

  const projectRef = useRef(project);
  const inlineThemeRef = useRef(inlineTheme);
  const demoStateRef = useRef(demoState);
  useEffect(() => { projectRef.current = project; }, [project]);
  useEffect(() => { inlineThemeRef.current = inlineTheme; }, [inlineTheme]);
  useEffect(() => { demoStateRef.current = demoState; }, [demoState]);

  useEffect(() => {
    const update = () => {
      const el = containerRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const padding = 24;
      const sx = (rect.width - padding) / panelWidth;
      const sy = (rect.height - padding) / panelHeight;
      setScale(Math.max(0.05, Math.min(sx, sy, 1)));
    };
    update();
    if (typeof ResizeObserver === "undefined" || !containerRef.current) return;
    const ro = new ResizeObserver(update);
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, [panelWidth, panelHeight]);

  const handleLoad = () => {
    if (!pageId) return;
    // The preview iframe is served from this same origin; target it explicitly
    // rather than "*" so credential-bearing project config isn't broadcast.
    // The panel reads a matrix as two finished lists and has no expander, so
    // what goes over is the resolved copy (matrix plan D6).
    void resolveProjectMatrices(projectRef.current).then((resolved) =>
      iframeRef.current?.contentWindow?.postMessage(
        {
          type: "openavc:editor-init",
          project: resolved,
          pageId,
          showGrid: false,
          demoState: demoStateRef.current,
          inlineTheme: inlineThemeRef.current,
        },
        window.location.origin,
      ),
    );
  };

  useEffect(() => {
    if (!pageId) return;
    let live = true;
    const timer = setTimeout(() => {
      void resolveProjectMatrices(project).then((resolved) => {
        if (!live) return;
        iframeRef.current?.contentWindow?.postMessage(
          {
            type: "openavc:editor-project",
            project: resolved,
            pageId,
            showGrid: false,
            demoState,
            inlineTheme,
          },
          window.location.origin,
        );
      });
    }, 40);
    return () => { live = false; clearTimeout(timer); };
  }, [project, pageId, demoState, inlineTheme]);

  return (
    <div
      ref={containerRef}
      style={{
        height: "100%",
        width: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        overflow: "hidden",
        background: "var(--bg-base)",
      }}
    >
      {pageId ? (
        <div
          style={{
            width: panelWidth,
            height: panelHeight,
            transform: `scale(${scale})`,
            transformOrigin: "center center",
            flexShrink: 0,
            borderRadius: "var(--radius-lg)",
            overflow: "hidden",
            boxShadow: "0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.08)",
          }}
        >
          <iframe
            key={pageId}
            ref={iframeRef}
            src={`${getTunnelPrefix()}/panel/?page=${encodeURIComponent(pageId)}&edit=1`}
            onLoad={handleLoad}
            title={title}
            style={{
              width: panelWidth,
              height: panelHeight,
              border: "none",
              background: "var(--bg-base)",
              display: "block",
            }}
          />
        </div>
      ) : (
        <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-base)" }}>No page to preview.</div>
      )}
    </div>
  );
}
