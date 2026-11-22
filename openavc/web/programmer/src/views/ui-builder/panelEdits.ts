/**
 * The UI Builder's edits of the panel as a whole, where elementEdits.ts has
 * those of one control or master: choosing a theme, clearing the overrides
 * tuned on it, putting every control's styles back to it, the project
 * stylesheet and Panel Settings. Each is one undo entry (useEditRun.ts).
 *
 * Choosing a theme clears the accent, the font and the overrides set on the
 * project, which were tuned for the theme before it (a blue accent carried onto
 * a gold theme looks wrong), and sets the light or dark treatment the panel
 * falls back on when the theme cannot be found. The Properties panel's theme
 * list and the Theme Studio both choose through it. Clearing the overrides joins
 * the run in progress (the Theme Studio clears them as it saves, switches to or
 * deletes a theme), where every other edit here is an entry of its own.
 */
import { useCallback } from "react";
import type { ProjectConfig, UIPage, UISettings } from "../../api/types";
import { PROJECT_VALUES } from "../../api/projectValues.gen";
import type { ThemeMode } from "../../api/systemClient";
import { showSuccess } from "../../store/toastStore";
import type { ThemeDefaults, ThemeSummary } from "./builderTheme";
import { projectEdit, type EditRun, type ProjectEdit } from "./useEditRun";

/**
 * The light or dark treatment a project falls back on when its theme cannot
 * be found: the one the server's theme rule gave the theme, as the server's
 * answer to the save that made it (`given`) or in the list (its `mode`), and
 * the project schema's default for a theme the list lacks.
 */
function themeFallbackMode(themes: ThemeSummary[], id: string, given?: ThemeMode): string {
  return given ?? themes.find((t) => t.id === id)?.mode ?? PROJECT_VALUES.UISettings.theme.default;
}

/** The pages with each control's style overrides removed wherever its type's
 *  theme defaults name the property. A type the theme says nothing about is
 *  left exactly as it was. */
function withoutThemeStyles(pages: UIPage[], defaults: ThemeDefaults): UIPage[] {
  return pages.map((page) => ({
    ...page,
    elements: page.elements.map((el) => {
      const keysToReset = new Set(Object.keys(defaults[el.type] || {}));
      if (keysToReset.size === 0) return el;
      const cleaned: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(el.style || {})) {
        if (!keysToReset.has(k)) cleaned[k] = v;
      }
      return { ...el, style: cleaned };
    }),
  }));
}

function chooseTheme(project: ProjectConfig, themes: ThemeSummary[], id: string, mode?: ThemeMode): ProjectEdit {
  const settings = project.ui.settings;
  const theme = themeFallbackMode(themes, id, mode);
  return projectEdit(project, {
    settings: { ...settings, theme_id: id, theme, accent_color: "", font_family: "", theme_overrides: {} },
  }, "Change theme");
}

function clearThemeOverrides(project: ProjectConfig): ProjectEdit {
  return projectEdit(project, { settings: { ...project.ui.settings, theme_overrides: {} } }, "Edit theme overrides");
}

function resetStylesToTheme(project: ProjectConfig, defaults: ThemeDefaults): ProjectEdit {
  return projectEdit(project, { pages: withoutThemeStyles(project.ui.pages, defaults) }, "Reset element styles to theme");
}

function saveStylesheet(project: ProjectConfig, css: string): ProjectEdit {
  return projectEdit(project, { custom_css: css }, "Edit project stylesheet");
}

function saveSettings(project: ProjectConfig, settings: UISettings): ProjectEdit {
  return projectEdit(project, { settings }, "Edit project settings");
}

export interface PanelEdits {
  /** Choose a theme; `mode` is the server's answer for one a save just made, which the list may not hold yet. */
  chooseTheme: (id: string, mode?: ThemeMode) => void;
  clearOverrides: () => void;
  resetStyles: () => void;
  saveStylesheet: (css: string) => void;
  saveSettings: (settings: UISettings) => void;
}

/** The theme these edits read: its list (for the fallback treatment) and its
 *  control defaults (for the reset). */
interface ThemeRead {
  themes: ThemeSummary[];
  defaults: ThemeDefaults;
}

/** The edits bound to the project as last drawn. Choosing a theme keeps its
 *  identity until the project, the theme list or the run changes, as the
 *  page's handler did; nothing reads the others' identity. */
export function usePanelEdits(project: ProjectConfig | null, run: EditRun, theme: ThemeRead): PanelEdits {
  const { themes, defaults } = theme;
  const choose = useCallback((id: string, mode?: ThemeMode) => {
    if (project) run.commit(chooseTheme(project, themes, id, mode));
  }, [project, themes, run]);
  const clearOverrides = useCallback(() => {
    if (project) run.join(clearThemeOverrides(project));
  }, [project, run]);
  return {
    chooseTheme: choose,
    clearOverrides,
    resetStyles: () => {
      if (!project) return;
      run.commit(resetStylesToTheme(project, defaults));
      showSuccess("Element styles reset to theme defaults");
    },
    saveStylesheet: (css) => {
      if (!project) return;
      run.commit(saveStylesheet(project, css));
      showSuccess("Project stylesheet saved");
    },
    saveSettings: (settings) => {
      if (project) run.commit(saveSettings(project, settings));
    },
  };
}
