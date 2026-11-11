/**
 * The Theme Studio, the UI Builder's theme editor, in three columns: the
 * picker, the editor and a live preview. This module holds the theme being
 * edited: the working copy, loaded as the theme is saved (without the
 * project's overrides, which the editor's banner names), and the saved copy
 * that Unsaved, Discard and every "modified" mark compare with. It also holds
 * the file actions and their questions, and what the preview is handed. The
 * editor's sections each read and edit the working copy through one
 * `ThemeDraft` (`themeEdits.ts`), and every file action that waits on the
 * server runs through `statusLine.ts`'s `attempt`, which holds the buttons and
 * says how it went (Discard and Export answer at once, without it).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { X } from "lucide-react";
import {
  getTheme, createTheme, updateTheme, deleteTheme, importTheme,
  ThemeExistsError,
  type ThemeDefinition, type ThemeSummary,
} from "../../api/restClient";
import type { ProjectConfig } from "../../api/types";
import { ConfirmDialog } from "../shared/ConfirmDialog";
import { Modal } from "../shared/Modal";
import { PanelPreviewFrame } from "./PanelPreviewFrame";
import { ThemePickerColumn } from "./ThemePicker";
import { ContrastSection } from "./ContrastSection";
import { ElementSections } from "./ElementSections";
import { editableCopy, variablesOf, type ThemeDraft } from "./themeEdits";
import { QuickAdjustSection } from "./QuickAdjustSection";
import { ThemeInfoSection, ThemeTokensSection } from "./ThemeSections";
import { PageBackgroundSection } from "./PageBackgroundSection";
import { themeFile, savedFile, previewColors, customCopy, duplicateOf, keptBeside } from "./themeFiles";
import { useStatusLine, orStepFailed, importFailed, type StatusLine } from "./statusLine";
import { FileActionBar, FileActionLine, type FileActions } from "./FileActionBar";
import { OverridesBanner } from "./OverridesBanner";
import { galleryPage, GALLERY_PAGE_ID, GALLERY_DEMO_STATE } from "./themeGallery";

// --- Main component ---

export interface ThemeStudioProps {
  open: boolean;
  onClose: () => void;
  themes: ThemeSummary[];
  project: ProjectConfig;
  currentThemeId: string;
  themeOverrides: Record<string, unknown>;
  onChangeTheme: (themeId: string) => void;
  /** Called to clear project-level theme_overrides after save (they're now baked in). */
  onClearOverrides: () => void;
  onRefreshThemes: () => void;
  /** Called after Save Changes so the canvas iframe re-fetches the updated theme. */
  onThemeSaved?: () => void;
  onResetElementStyles?: () => void;
  panelWidth?: number;
  panelHeight?: number;
}

export function ThemeStudio({
  open,
  onClose,
  themes,
  project,
  currentThemeId,
  themeOverrides,
  onChangeTheme,
  onClearOverrides,
  onRefreshThemes,
  onThemeSaved,
  onResetElementStyles,
  panelWidth = 1280,
  panelHeight = 800,
}: ThemeStudioProps) {
  // Working copy of the theme being edited. All mutations go here. The
  // preview iframe consumes this directly via postMessage as inlineTheme.
  // The "saved" copy is kept so we can detect dirty state and discard.
  const [working, setWorking] = useState<ThemeDefinition | null>(null);
  const [savedJson, setSavedJson] = useState<string>("");
  const { busy, line: statusMsg, say, attempt } = useStatusLine();
  const [pendingThemeSwitch, setPendingThemeSwitch] = useState<string | null>(null);
  const [pendingClose, setPendingClose] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  // An import whose id collides with an existing custom theme, awaiting the
  // user's Overwrite / Keep both choice.
  const [importCollision, setImportCollision] = useState<{ file: File; id: string; name: string } | null>(null);
  const [previewView, setPreviewView] = useState<string>("gallery");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const gallery = useMemo(() => galleryPage(), []);
  const savedTheme = useMemo<ThemeDefinition | null>(() => {
    if (!savedJson) return null;
    try { return JSON.parse(savedJson) as ThemeDefinition; }
    catch { return null; }
  }, [savedJson]);

  // A theme as loaded is both the working copy and what Unsaved compares with.
  const hold = (theme: ThemeDefinition) => {
    const copy = editableCopy(theme);
    setWorking(copy);
    setSavedJson(JSON.stringify(copy));
  };

  // Load the theme PRISTINE into the working copy. Project-level overrides
  // are a separate layer surfaced by the override banner — merging them in
  // here would make every theme look like it has the override-applied values
  // and silently mislead the user. The studio edits the THEME; overrides are
  // managed via the banner's "Clear overrides" button.
  useEffect(() => {
    if (!open || !currentThemeId) return;
    let cancelled = false;
    say(null);
    getTheme(currentThemeId)
      .then((t) => {
        if (!cancelled) hold(t);
      })
      .catch(() => {
        if (!cancelled) say({ kind: "error", text: `Failed to load "${currentThemeId}"` });
      });
    return () => { cancelled = true; };
  }, [open, currentThemeId]);

  useEffect(() => {
    if (open) setPreviewView("gallery");
  }, [open]);

  const isCustom = working?._source === "custom";
  const isDirty = working ? JSON.stringify(working) !== savedJson : false;

  // Every edit is a rule of themeEdits.ts (or Quick Adjust's surface style)
  // applied to the working copy; each section applies its own through the draft.
  const edit = (change: (theme: ThemeDefinition) => ThemeDefinition) =>
    setWorking((prev) => (prev ? change(prev) : prev));
  const draft: ThemeDraft | null = working && { theme: working, saved: savedTheme, edit };

  // Build the previewProject and inlineTheme that drive the iframe
  const isGallery = previewView === "gallery";
  const previewProject = useMemo<ProjectConfig>(() => {
    if (!working) return project;
    // Settings carry the working theme's id (so applyTheme matches inlineTheme)
    // AND blank out the project-level overrides so the studio's working theme
    // is what's actually rendered. Without these blanks, panel.js's
    // "per-setting overrides take priority" path would clobber the user's
    // accent / font_family edits with the saved project values.
    const settings = {
      ...project.ui.settings,
      theme_id: working.id,
      theme_overrides: {},
      accent_color: "",
      font_family: "",
    };
    if (isGallery) {
      return { ...project, ui: { ...project.ui, pages: [gallery], settings } };
    }
    return { ...project, ui: { ...project.ui, settings } };
  }, [working, project, isGallery, gallery]);
  const previewPageId = isGallery ? GALLERY_PAGE_ID : previewView;
  const previewDemoState = isGallery ? GALLERY_DEMO_STATE : undefined;

  const takenIds = () => new Set(themes.map((t) => t.id));

  const clearOverrides = () => {
    if (Object.keys(themeOverrides).length > 0) onClearOverrides();
  };

  // --- Save Changes (custom only) --- returns true on success
  const handleSaveChanges = async (): Promise<boolean> => {
    if (!working || !isCustom) return false;
    return attempt({
      failure: orStepFailed("Save"),
      work: async () => {
        await updateTheme(working.id, savedFile(working));
        clearOverrides();
        onRefreshThemes();
        return getTheme(working.id);
      },
      done: (reloaded) => {
        hold(reloaded);
        onThemeSaved?.();
        return `Saved "${working.name}"`;
      },
    });
  };

  // --- Save as Custom ---
  // A new name and id, numbered past every theme in the list, so the POST
  // never collides with a built-in or an existing custom theme (a 409).
  const handleSaveAsCustom = async (): Promise<boolean> => {
    if (!working) return false;
    const copy = customCopy(working.name, isCustom, takenIds());
    if (!copy) {
      say({ kind: "error", text: "Enter a theme name" });
      return false;
    }
    const { id, name } = copy;
    return attempt({
      failure: orStepFailed("Save"),
      work: () => createTheme(themeFile(working, { id, name, author: "Custom", preview_colors: previewColors(working.variables) })),
      done: () => {
        clearOverrides();
        onChangeTheme(id);
        onRefreshThemes();
        return `Saved as "${name}"`;
      },
    });
  };

  // --- Discard ---
  const handleDiscard = () => {
    if (!savedJson) return;
    setWorking(JSON.parse(savedJson));
    say({ kind: "info", text: "Reverted to saved values" });
  };

  // --- Delete (custom only) ---
  const handleDeleteTheme = async () => {
    if (!working || !isCustom) return;
    setConfirmDelete(false);
    await attempt({
      keepLine: true,
      failure: orStepFailed("Delete"),
      work: () => deleteTheme(working.id),
      done: () => {
        clearOverrides();
        onChangeTheme("dark-default");
        onRefreshThemes();
        return `Deleted "${working.name}"`;
      },
    });
  };

  // --- Duplicate (from picker hover button) ---
  const handleDuplicate = (sourceId: string, sourceName: string) =>
    attempt({
      failure: orStepFailed("Duplicate"),
      work: async () => {
        const source = await getTheme(sourceId);
        const copy = duplicateOf(sourceId, sourceName, takenIds());
        await createTheme(themeFile(source, { ...copy, author: "Custom" }));
        return copy;
      },
      done: (copy) => {
        onChangeTheme(copy.id);
        onRefreshThemes();
        return `Duplicated as "${copy.name}"`;
      },
    });

  // --- Export / Import ---
  const handleExport = () => {
    if (!working) return;
    const blob = new Blob([JSON.stringify(savedFile(working), null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${working.id}.avctheme`;
    a.click();
    URL.revokeObjectURL(url);
  };

  /** An import the server took: chosen, and said. */
  const chooseImported = (result: { theme_id: string; name: string }) => {
    onChangeTheme(result.theme_id);
    onRefreshThemes();
    return `Imported "${result.name}"`;
  };

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (fileInputRef.current) fileInputRef.current.value = "";
    if (!file) return;
    await attempt({
      failure: importFailed,
      work: async () => {
        try {
          return await importTheme(file, false);
        } catch (err) {
          // Don't silently clobber an edited custom theme — ask first.
          if (err instanceof ThemeExistsError) return err;
          throw err;
        }
      },
      done: (answer) => {
        if (!(answer instanceof ThemeExistsError)) return chooseImported(answer);
        setImportCollision({ file, id: answer.themeId, name: answer.themeName });
        return null;
      },
    });
  };

  // Overwrite the existing custom theme with the imported one.
  const handleOverwriteImport = async () => {
    if (!importCollision) return;
    const { file } = importCollision;
    setImportCollision(null);
    await attempt({ keepLine: true, failure: importFailed, work: () => importTheme(file, true), done: chooseImported });
  };

  // Keep both: re-import the file's theme under a fresh, non-colliding id.
  const handleKeepBothImport = async () => {
    if (!importCollision) return;
    const { file, id } = importCollision;
    setImportCollision(null);
    await attempt({
      keepLine: true,
      failure: importFailed,
      work: async () => {
        const parsed = JSON.parse(await file.text()) as ThemeDefinition;
        const kept = keptBeside(id, parsed.name, takenIds());
        await createTheme(themeFile(parsed, kept));
        return kept;
      },
      done: (kept) => {
        onChangeTheme(kept.id);
        onRefreshThemes();
        return `Imported as "${kept.name}"`;
      },
    });
  };

  // --- Theme switch (confirm if dirty) ---
  const handlePickTheme = (id: string) => {
    if (id === currentThemeId) return;
    if (isDirty) {
      setPendingThemeSwitch(id);
      return;
    }
    onChangeTheme(id);
  };

  const handleConfirmThemeSwitch = () => {
    if (!pendingThemeSwitch) return;
    onChangeTheme(pendingThemeSwitch);
    setPendingThemeSwitch(null);
  };

  // --- Close (confirm if dirty) ---
  const handleClose = () => {
    if (isDirty) {
      setPendingClose(true);
      return;
    }
    onClose();
  };

  if (!open) return null;

  return (
    <Modal
      onClose={handleClose}
      label="Theme Studio"
      overlayStyle={{ background: "rgba(0,0,0,0.75)" }}
      panelStyle={{
        width: "92vw",
        height: "92vh",
        maxWidth: 1800,
        background: "var(--bg-base)",
        border: "1px solid var(--border-color)",
        borderRadius: 8,
        display: "flex",
        flexDirection: "column",
        boxShadow: "0 24px 64px rgba(0,0,0,0.5)",
        overflow: "hidden",
      }}
    >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "10px 16px",
            borderBottom: "1px solid var(--border-color)",
            background: "var(--bg-surface)",
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>
            Theme Studio
          </div>
          {working && (
            <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              Editing <strong style={{ color: "var(--text-primary)" }}>{working.name}</strong>
              <span style={{ marginLeft: 6, color: "var(--text-muted)" }}>
                ({isCustom ? "custom" : "built-in"})
              </span>
              {isDirty && (
                <span
                  title="Unsaved changes"
                  style={{
                    marginLeft: 8,
                    padding: "1px 8px",
                    background: "rgba(255,167,38,0.18)",
                    color: "#ffa726",
                    borderRadius: 10,
                    fontSize: 11,
                    fontWeight: 600,
                  }}
                >
                  Unsaved
                </span>
              )}
            </div>
          )}
          <div style={{ flex: 1 }} />
          <select
            value={previewView}
            onChange={(e) => setPreviewView(e.target.value)}
            title="What the preview pane shows"
            style={{
              padding: "4px 8px",
              background: "var(--bg-surface)",
              color: "var(--text-primary)",
              border: "1px solid var(--border-color)",
              borderRadius: 4,
              fontSize: 12,
            }}
          >
            <option value="gallery">Preview: Element Gallery</option>
            {project.ui.pages.map((p) => (
              <option key={p.id} value={p.id}>
                Preview: {p.name || p.id}
              </option>
            ))}
          </select>
          {onResetElementStyles && (
            <button
              onClick={onResetElementStyles}
              title="Remove per-element style overrides so all elements inherit from the theme"
              style={{
                display: "flex", alignItems: "center", gap: 4,
                padding: "4px 10px", borderRadius: 4, fontSize: 11,
                background: "transparent", border: "1px solid var(--border-color)",
                color: "var(--text-secondary)", cursor: "pointer",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            >
              Reset Element Styles
            </button>
          )}
          <button
            onClick={handleClose}
            title="Close (Esc)"
            style={{
              width: 28,
              height: 28,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: "transparent",
              border: "1px solid var(--border-color)",
              borderRadius: 4,
              cursor: "pointer",
              color: "var(--text-secondary)",
            }}
          >
            <X size={14} />
          </button>
        </div>

        <div style={{ flex: 1, overflow: "hidden" }}>
          <PanelGroup direction="horizontal" autoSaveId="themeStudio">
            <Panel defaultSize={18} minSize={14} maxSize={28}>
              <ThemePickerColumn
                themes={themes}
                currentThemeId={currentThemeId}
                busy={busy}
                isDirty={isDirty}
                onPick={handlePickTheme}
                onDuplicate={handleDuplicate}
                onImport={() => fileInputRef.current?.click()}
              />
            </Panel>
            <PanelResizeHandle
              style={{ width: 4, background: "var(--border-color)", cursor: "col-resize" }}
            />
            <Panel defaultSize={36} minSize={26}>
              <EditorColumn
                draft={draft}
                isCustom={isCustom}
                isDirty={isDirty}
                busy={busy}
                line={statusMsg}
                actions={{
                  saveChanges: handleSaveChanges,
                  saveAsCustom: handleSaveAsCustom,
                  discard: handleDiscard,
                  exportFile: handleExport,
                  askDelete: () => setConfirmDelete(true),
                }}
                themeOverrides={themeOverrides}
                onClearOverrides={onClearOverrides}
              />
            </Panel>
            <PanelResizeHandle
              style={{ width: 4, background: "var(--border-color)", cursor: "col-resize" }}
            />
            <Panel defaultSize={46} minSize={28}>
              <div
                style={{
                  height: "100%",
                  display: "flex",
                  flexDirection: "column",
                  background: "var(--bg-surface)",
                }}
              >
                <div
                  style={{
                    padding: "6px 10px",
                    fontSize: 10,
                    color: "var(--text-muted)",
                    textTransform: "uppercase",
                    letterSpacing: "0.5px",
                    borderBottom: "1px solid var(--border-color)",
                    background: "var(--bg-base)",
                  }}
                >
                  {isGallery
                    ? "Live preview: element gallery (one of every type)"
                    : `Live preview: ${(project.ui.pages.find((p) => p.id === previewView)?.name) || previewView}`}
                </div>
                <div style={{ flex: 1, position: "relative" }}>
                  <PanelPreviewFrame
                    project={previewProject}
                    pageId={previewPageId}
                    inlineTheme={working}
                    demoState={previewDemoState}
                    panelWidth={panelWidth}
                    panelHeight={panelHeight}
                  />
                </div>
              </div>
            </Panel>
          </PanelGroup>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          accept=".avctheme,.json"
          style={{ display: "none" }}
          onChange={handleImport}
        />

        {importCollision && (
          <ConfirmDialog
            title="Theme already exists"
            confirmLabel="Overwrite"
            extraActionLabel="Keep both"
            cancelLabel="Cancel"
            destructive
            onCancel={() => setImportCollision(null)}
            onExtraAction={handleKeepBothImport}
            onConfirm={handleOverwriteImport}
            message={
              <p>
                A custom theme named <strong>"{importCollision.name}"</strong> already exists.
                Overwrite it, or keep both by importing under a new name?
              </p>
            }
          />
        )}

        {pendingThemeSwitch && (
          <ConfirmDialog
            title="Discard unsaved changes?"
            confirmLabel="Discard and switch"
            cancelLabel="Keep editing"
            onCancel={() => setPendingThemeSwitch(null)}
            onConfirm={handleConfirmThemeSwitch}
            message={
              <p>
                You have unsaved changes to <strong>"{working?.name}"</strong>. Switch theme anyway?
                The edits will be lost.
              </p>
            }
          />
        )}

        {pendingClose && (
          <ConfirmDialog
            title="Save changes before closing?"
            confirmLabel={isCustom ? "Save Changes" : "Save as Custom"}
            cancelLabel="Discard"
            onConfirm={async () => {
              setPendingClose(false);
              const ok = isCustom
                ? await handleSaveChanges()
                : await handleSaveAsCustom();
              if (ok) onClose();
              // If save failed, status message is already shown. Studio stays
              // open so the user can retry or discard.
            }}
            onCancel={() => {
              setPendingClose(false);
              onClose();
            }}
            message={
              <>
                <p style={{ marginBottom: 8 }}>
                  You have unsaved edits to <strong>"{working?.name}"</strong>.
                </p>
                <p style={{ color: "var(--text-muted)", fontSize: 12 }}>
                  {isCustom
                    ? "Save Changes overwrites this theme. Discard reloads it."
                    : "Built-in themes can't be modified. Save as Custom creates an editable copy. Discard loses your edits."}
                </p>
              </>
            }
          />
        )}

        {confirmDelete && working && (
          <ConfirmDialog
            title="Delete custom theme?"
            confirmLabel="Delete"
            cancelLabel="Cancel"
            onCancel={() => setConfirmDelete(false)}
            onConfirm={handleDeleteTheme}
            message={
              <>
                <p style={{ marginBottom: 8 }}>
                  Delete <strong>"{working.name}"</strong>? This permanently removes the theme file
                  from this project's <code>themes/</code> directory.
                </p>
                <p style={{ color: "var(--text-muted)", fontSize: 12 }}>
                  Pages still set to this theme will fall back to <strong>dark-default</strong>.
                </p>
              </>
            }
          />
        )}
    </Modal>
  );
}

// --- Editor column ---

interface EditorColumnProps {
  draft: ThemeDraft | null;
  isCustom: boolean;
  isDirty: boolean;
  busy: boolean;
  line: StatusLine | null;
  actions: FileActions;
  themeOverrides: Record<string, unknown>;
  onClearOverrides: () => void;
}

function EditorColumn({
  draft,
  isCustom,
  isDirty,
  busy,
  line,
  actions,
  themeOverrides,
  onClearOverrides,
}: EditorColumnProps) {
  if (!draft) {
    return (
      <div
        style={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--text-muted)",
          padding: 16,
        }}
      >
        Loading theme…
      </div>
    );
  }

  const working = draft.theme;

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <FileActionBar isCustom={isCustom} isDirty={isDirty} busy={busy} name={working.name} actions={actions} />
      {line && <FileActionLine line={line} />}
      <OverridesBanner overrides={themeOverrides} onClear={onClearOverrides} />

      <div style={{ flex: 1, overflow: "auto", padding: 10 }}>
        <ThemeInfoSection draft={draft} />

        <QuickAdjustSection draft={draft} />

        <ThemeTokensSection draft={draft} />

        <PageBackgroundSection draft={draft} />

        <ElementSections draft={draft} />

        <ContrastSection variables={variablesOf(working)} />
      </div>
    </div>
  );
}
