/**
 * The dialogs the UI Builder opens over the canvas: the Theme Studio, the
 * project stylesheet, the question before every control's styles go back to
 * the theme, Validate's report and Panel Settings, drawn in that order, so a
 * later one sits over an earlier one.
 *
 * Which are open is the page's to hold (`useBuilderDialogs`), because the
 * toolbar's buttons that open three of them are not drawn in preview and a
 * dialog stays open through it. The Theme Studio is always drawn, open or not,
 * so its working copy outlives a close; the others are drawn fresh each time
 * they open. The reset question is asked over the Studio, which stays open
 * after either answer. While the Studio is open the Properties panel offers no
 * theme list of its own (`themeListChange`): choosing there would switch the
 * theme under the Studio's unsaved edits. Both previews are drawn on the screen
 * preset as the toolbar has it chosen (read from the Builder store, as the canvas
 * reads it), not turned for the arrangement behind them: a preview is about
 * colour and type, and should not change shape with the arrangement selected.
 */
import { useCallback, useState } from "react";
import type { ProjectConfig, UIPage } from "../../api/types";
import { SCREEN_PRESETS, type ValidationIssue } from "../../components/ui-builder/uiBuilderHelpers";
import { ThemeStudio } from "../../components/ui-builder/ThemeStudio";
import { StylesheetEditor } from "../../components/ui-builder/StylesheetEditor";
import { ConfirmDialog } from "../../components/shared/ConfirmDialog";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import type { ThemeSummary } from "./builderTheme";
import type { PanelEdits } from "./panelEdits";
import { PanelSettingsDialog } from "./PanelSettingsDialog";
import { ValidationReport } from "./ValidationReport";

export type BuilderDialog = "studio" | "stylesheet" | "settings" | "reset";

export interface BuilderDialogsState {
  open: Record<BuilderDialog, boolean>;
  show: (dialog: BuilderDialog) => void;
  hide: (dialog: BuilderDialog) => void;
}

const NONE_OPEN: Record<BuilderDialog, boolean> = { studio: false, stylesheet: false, settings: false, reset: false };

export function useBuilderDialogs(): BuilderDialogsState {
  const [open, setOpen] = useState(NONE_OPEN);
  // Showing an open dialog, or hiding a closed one, leaves the state as it was.
  const set = useCallback((dialog: BuilderDialog, value: boolean) => {
    setOpen((o) => (o[dialog] === value ? o : { ...o, [dialog]: value }));
  }, []);
  const show = useCallback((dialog: BuilderDialog) => set(dialog, true), [set]);
  const hide = useCallback((dialog: BuilderDialog) => set(dialog, false), [set]);
  return { open, show, hide };
}

/** What the Properties panel's theme list chooses with: nothing while the
 *  Theme Studio is open. */
export function themeListChange(
  dialogs: BuilderDialogsState,
  chooseTheme: (id: string) => void,
): ((id: string) => void) | undefined {
  return dialogs.open.studio ? undefined : chooseTheme;
}

export interface BuilderDialogsProps {
  dialogs: BuilderDialogsState;
  project: ProjectConfig;
  /** The page on screen, which the stylesheet's preview shows. */
  page: UIPage | null;
  theme: { themes: ThemeSummary[]; loadThemes: () => void; refetch: () => void };
  edits: PanelEdits;
  /** Validate's last report, null while none is open. */
  validation: { issues: ValidationIssue[] | null; close: () => void };
}

/** The size both previews are drawn at: the preset's own, else the 7" tablet's. */
function previewSize(preset: { width: number; height: number } | undefined) {
  return { panelWidth: preset?.width ?? 1024, panelHeight: preset?.height ?? 600 };
}

/** The theme the Studio opens on: the project's, else the default, with the
 *  overrides set on it, else none. */
function studioTheme(project: ProjectConfig) {
  const settings = project.ui.settings;
  return { currentThemeId: settings.theme_id || "dark-default", themeOverrides: settings.theme_overrides || {} };
}

const RESET_MESSAGE =
  "This removes per-element style overrides for every property the current theme defines defaults for. " +
  "Elements will inherit their appearance from the theme. This can be undone with Ctrl+Z.";

export function BuilderDialogs({ dialogs, project, page, theme, edits, validation }: BuilderDialogsProps) {
  const { open, show, hide } = dialogs;
  const presetIndex = useUIBuilderStore((s) => s.screenPresetIndex);
  const size = previewSize(SCREEN_PRESETS[presetIndex]);
  return (
    <>
      <ThemeStudio
        open={open.studio}
        onClose={() => hide("studio")}
        themes={theme.themes}
        project={project}
        {...studioTheme(project)}
        onChangeTheme={edits.chooseTheme}
        onClearOverrides={edits.clearOverrides}
        onRefreshThemes={theme.loadThemes}
        onThemeSaved={theme.refetch}
        onResetElementStyles={() => show("reset")}
        {...size}
      />
      {open.stylesheet && (
        <StylesheetEditor
          project={project}
          previewPageId={page?.id ?? null}
          {...size}
          onSave={edits.saveStylesheet}
          onClose={() => hide("stylesheet")}
        />
      )}
      {open.reset && (
        <ConfirmDialog
          title="Reset element styles to theme defaults?"
          message={RESET_MESSAGE}
          confirmLabel="Reset Styles"
          destructive
          onCancel={() => hide("reset")}
          onConfirm={() => {
            hide("reset");
            edits.resetStyles();
          }}
        />
      )}
      {validation.issues !== null && <ValidationReport issues={validation.issues} onClose={validation.close} />}
      {open.settings && (
        <PanelSettingsDialog
          settings={project.ui.settings}
          pages={project.ui.pages}
          onUpdate={edits.saveSettings}
          onClose={() => hide("settings")}
        />
      )}
    </>
  );
}
