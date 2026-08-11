import { useMemo, useState } from "react";
import Editor from "@monaco-editor/react";
import { X, Save, FileCode } from "lucide-react";
import type { ProjectConfig } from "../../api/types";
import { Modal } from "../shared/Modal";
import { ConfirmDialog } from "../shared/ConfirmDialog";
import { PanelPreviewFrame } from "./PanelPreviewFrame";
import { stylesheetClassNames } from "./customCssHelpers";

/**
 * The project stylesheet, with the panel next to it.
 *
 * `ui.custom_css` ships to the panel as one <style> appended after the theme,
 * so it can restyle any control the platform draws. Until now the only way to
 * write one was to hand-edit the .avc file, which is not a thing we can ask an
 * integrator to do for something as ordinary as "make the buttons our green".
 *
 * The preview to the right is the real panel showing the real page, re-seeded
 * as you type, because a stylesheet you cannot see the effect of is a stylesheet
 * you write by trial and error with a save and a reload between each try.
 */
const STARTER_CSS = `/* Project stylesheet.
 *
 * Rules here apply to every panel in this project, on top of the theme.
 * Name a class on an element (Properties > Style > Custom classes) and
 * target it here.
 *
 * The panel writes colors, corner radius, borders, shadows and text sizes
 * straight onto each control as inline styles, so a rule that changes one
 * of THOSE needs !important to win. Anything the panel does not set
 * inline -- fonts, letter spacing, transitions, transforms, ::after
 * decorations -- works without it.
 *
 * Theme colors are available as CSS variables, so a rule can follow the
 * theme instead of fighting it:
 *   var(--panel-bg)  var(--panel-text)     var(--panel-accent)
 *   var(--panel-button-bg)  var(--panel-button-text)
 *   var(--panel-surface)    var(--panel-border-radius)
 */

.brand-button {
  background: #8AB493 !important;
  color: #10231a !important;
  border-radius: 2rem !important;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
`;

export interface StylesheetEditorProps {
  project: ProjectConfig;
  /** The page the preview shows -- the one the author was just looking at. */
  previewPageId: string | null;
  panelWidth: number;
  panelHeight: number;
  onSave: (css: string) => void;
  onClose: () => void;
}

export function StylesheetEditor({
  project,
  previewPageId,
  panelWidth,
  panelHeight,
  onSave,
  onClose,
}: StylesheetEditorProps) {
  const saved = project.ui.custom_css ?? "";
  const [draft, setDraft] = useState(saved);
  const [showDiscardConfirm, setShowDiscardConfirm] = useState(false);
  const dirty = draft !== saved;

  // The preview gets the draft, not the saved sheet, so it shows what is on
  // screen right now. Everything else about the project is untouched -- this
  // is the author's own page, restyled.
  const previewProject = useMemo<ProjectConfig>(
    () => ({ ...project, ui: { ...project.ui, custom_css: draft } }),
    [project, draft],
  );

  const classNames = useMemo(() => stylesheetClassNames(draft), [draft]);

  const handleClose = () => {
    if (dirty) { setShowDiscardConfirm(true); return; }
    onClose();
  };

  const handleSave = () => {
    if (dirty) onSave(draft);
    onClose();
  };

  return (
    <Modal
      onClose={handleClose}
      label="Project Stylesheet"
      closeOnBackdrop={false}
      initialFocus="none"
      panelStyle={{
        width: "min(1400px, 96vw)",
        height: "min(860px, 92vh)",
        display: "flex",
        flexDirection: "column",
        border: "1px solid var(--border-color)",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          padding: "10px 14px",
          borderBottom: "1px solid var(--border-color)",
          background: "var(--bg-surface)",
          flexShrink: 0,
        }}
      >
        <FileCode size={16} style={{ color: "var(--accent)" }} />
        <span style={{ fontSize: 14, fontWeight: 600 }}>Project Stylesheet</span>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          CSS applied to every panel in this project, after the theme. Colors, corner
          radius and text size arrive on each control as inline styles, so changing one
          of those from here needs <code>!important</code>.
        </span>
        <div style={{ flex: 1 }} />
        {dirty && (
          <span style={{ fontSize: 11, color: "#f59e0b" }}>Unsaved changes</span>
        )}
        <button
          onClick={handleSave}
          disabled={!dirty}
          style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "4px 12px", borderRadius: "var(--border-radius)",
            border: "none", cursor: dirty ? "pointer" : "default",
            background: dirty ? "var(--accent)" : "var(--bg-hover)",
            color: dirty ? "#10231a" : "var(--text-muted)",
            fontSize: 12, fontWeight: 600,
          }}
        >
          <Save size={13} /> Save
        </button>
        <button
          onClick={handleClose}
          title="Close"
          style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            width: 26, height: 26, borderRadius: "var(--border-radius)",
            background: "transparent", border: "none", cursor: "pointer",
            color: "var(--text-muted)",
          }}
        >
          <X size={16} />
        </button>
      </div>

      {/* Editor + live preview */}
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <div
          style={{
            flex: "1 1 55%",
            minWidth: 0,
            display: "flex",
            flexDirection: "column",
            borderRight: "1px solid var(--border-color)",
          }}
        >
          <div style={{ flex: 1, minHeight: 0 }}>
            <Editor
              height="100%"
              defaultLanguage="css"
              theme="vs-dark"
              value={draft}
              onChange={(value) => setDraft(value ?? "")}
              options={{
                minimap: { enabled: false },
                fontSize: 13,
                lineNumbers: "on",
                scrollBeyondLastLine: false,
                tabSize: 2,
                wordWrap: "on",
                automaticLayout: true,
              }}
            />
          </div>
          {/* What this sheet gives the element panel to offer. Seeing the list
              grow as you type a rule is the quickest way to catch a typo in a
              selector -- a class that never appears here will never appear on
              a control either. */}
          <div
            style={{
              flexShrink: 0,
              borderTop: "1px solid var(--border-color)",
              background: "var(--bg-surface)",
              padding: "8px 12px",
              maxHeight: 96,
              overflowY: "auto",
            }}
          >
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}>
              {classNames.length === 0
                ? "No classes defined yet. A rule like .brand-button { } adds one, and it becomes selectable on any element."
                : `Classes in this stylesheet (${classNames.length}) — pick these on an element under Properties > Style > Custom classes:`}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
              {classNames.map((name) => (
                <span
                  key={name}
                  style={{
                    fontFamily: "var(--font-mono, monospace)",
                    fontSize: 11,
                    padding: "1px 6px",
                    borderRadius: 10,
                    background: "var(--bg-hover)",
                    color: "var(--text-secondary)",
                  }}
                >
                  .{name}
                </span>
              ))}
            </div>
            {draft.trim() === "" && (
              <button
                onClick={() => setDraft(STARTER_CSS)}
                style={{
                  marginTop: 6, padding: "2px 8px", fontSize: 11,
                  borderRadius: "var(--border-radius)", cursor: "pointer",
                  background: "var(--bg-hover)", border: "1px solid var(--border-color)",
                  color: "var(--text-secondary)",
                }}
              >
                Start from an example
              </button>
            )}
          </div>
        </div>

        <div style={{ flex: "1 1 45%", minWidth: 0, display: "flex", flexDirection: "column" }}>
          <div
            style={{
              flexShrink: 0,
              padding: "6px 12px",
              fontSize: 11,
              color: "var(--text-muted)",
              borderBottom: "1px solid var(--border-color)",
              background: "var(--bg-base)",
            }}
          >
            Live preview —{" "}
            {project.ui.pages.find((p) => p.id === previewPageId)?.name || previewPageId || "no page"}
          </div>
          <div style={{ flex: 1, position: "relative", minHeight: 0 }}>
            <PanelPreviewFrame
              project={previewProject}
              pageId={previewPageId}
              panelWidth={panelWidth}
              panelHeight={panelHeight}
              title="Stylesheet preview"
            />
          </div>
        </div>
      </div>

      {showDiscardConfirm && (
        <ConfirmDialog
          title="Discard stylesheet changes?"
          message="Your edits to the project stylesheet have not been saved."
          confirmLabel="Discard"
          destructive
          onCancel={() => setShowDiscardConfirm(false)}
          onConfirm={() => { setShowDiscardConfirm(false); onClose(); }}
        />
      )}
    </Modal>
  );
}
