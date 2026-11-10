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
import { isLightColor } from "../../components/ui-builder/colorUtils";
import { showSuccess } from "../../store/toastStore";
import type { ThemeDefaults, ThemeSummary } from "./builderTheme";
import { projectEdit, type EditRun, type ProjectEdit } from "./useEditRun";

/**
 * The light or dark treatment a project falls back on when its theme cannot
 * be found: from the theme's panel background, and only when that cannot be
 * read, from its id. A light theme whose id lacks "light" is still light.
 */
export function themeFallbackMode(themes: ThemeSummary[], id: string): "light" | "dark" {
  const bg = themes.find((t) => t.id === id)?.variables?.panel_bg;
  const light = typeof bg === "string" ? isLightColor(bg) : null;
  if (light !== null) return light ? "light" : "dark";
  return id.includes("light") || id === "minimal" ? "light" : "dark";
}

/** The pages with each control's style overrides removed wherever its type's
 *  theme defaults name the property. A type the theme says nothing about is
 *  left exactly as it was. */
export function withoutThemeStyles(pages: UIPage[], defaults: ThemeDefaults): UIPage[] {
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

export function chooseTheme(project: ProjectConfig, themes: ThemeSummary[], id: string): ProjectEdit {
  const settings = project.ui.settings;
  const theme = themeFallbackMode(themes, id);
  return projectEdit(project, {
    settings: { ...settings, theme_id: id, theme, accent_color: "", font_family: "", theme_overrides: {} },
  }, "Change theme");
}

export function clearThemeOverrides(project: ProjectConfig): ProjectEdit {
  return projectEdit(project, { settings: { ...project.ui.settings, theme_overrides: {} } }, "Edit theme overrides");
}

export function resetStylesToTheme(project: ProjectConfig, defaults: ThemeDefaults): ProjectEdit {
  return projectEdit(project, { pages: withoutThemeStyles(project.ui.pages, defaults) }, "Reset element styles to theme");
}

export function saveStylesheet(project: ProjectConfig, css: string): ProjectEdit {
  return projectEdit(project, { custom_css: css }, "Edit project stylesheet");
}

export function saveSettings(project: ProjectConfig, settings: UISettings): ProjectEdit {
  return projectEdit(project, { settings }, "Edit project settings");
}

export interface PanelEdits {
  chooseTheme: (id: string) => void;
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
  const choose = useCallback((id: string) => {
    if (project) run.commit(chooseTheme(project, themes, id));
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
