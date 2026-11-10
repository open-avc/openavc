/**
 * Shared pieces for the Theme Studio's tests: two themes as the server sends
 * them (a custom one and a built-in), the summaries the picker lists, a project
 * with two pages and some theme settings of its own, a render helper that hands
 * back every callback, and finders for the editor's rows. The REST client and
 * the preview frame are mocked in each test file, because `vi.mock` only hoists
 * inside the file that calls it.
 */
import { render, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { ThemeStudio, type ThemeStudioProps } from "../components/ui-builder/ThemeStudio";
import type { ProjectConfig } from "../api/types";
import type { ThemeDefinition, ThemeSummary } from "../api/restClient";

/** A custom theme with a value in most places the Studio edits. */
export function customTheme(): ThemeDefinition {
  return {
    id: "studio-sample",
    name: "Sample",
    version: "1.0",
    author: "Somebody",
    description: "A sample theme",
    preview_colors: [],
    variables: {
      panel_bg: "#000000",
      panel_text: "#ffffff",
      accent: "#3366ff",
      button_bg: "#202020",
      button_text: "#777777",
      surface: "#303030",
      surface_border: "#303030",
      warning: "not-a-colour",
      border_radius: 0.5714,
      font_family: "Inter, system-ui, sans-serif",
    },
    element_defaults: {
      button: { border_radius: 0.5714, border_width: 0.0714, box_shadow: "0 2px 4px rgba(0,0,0,0.3)" },
      label: { text_color: "#ff0000" },
      group: { border_radius: 1 },
      camera_preset: { bg_color: "#111111" },
    },
    page_defaults: { background_color: "#222222" },
    _source: "custom",
  };
}

/** A built-in theme with almost nothing set. */
export function builtinTheme(): ThemeDefinition {
  return {
    id: "dark",
    name: "Dark",
    version: "1.0",
    author: "OpenAVC",
    description: "",
    preview_colors: ["#111111"],
    variables: { accent: "#2196F3" },
    element_defaults: {},
    _source: "builtin",
  };
}

function summary(t: ThemeDefinition, source: string): ThemeSummary {
  return {
    id: t.id, name: t.name, version: t.version, author: t.author, description: t.description,
    preview_colors: t.preview_colors, variables: t.variables, source,
  };
}

export const SUMMARIES: ThemeSummary[] = [summary(builtinTheme(), "builtin"), summary(customTheme(), "custom")];

/** Fetch the fixture theme a test asks for by id. */
export function themeById(id: string): ThemeDefinition {
  if (id === "dark") return builtinTheme();
  if (id === "studio-sample") return customTheme();
  throw new Error(`API 404: {"detail":"Theme '${id}' not found"}`);
}

export function studioProject(): ProjectConfig {
  return {
    ui: {
      settings: {
        theme_id: "studio-sample",
        accent_color: "#ff00ff",
        font_family: "Georgia",
        theme_overrides: { accent: "#00ff00" },
        orientation: "landscape",
      },
      pages: [
        { id: "main", name: "Main Page", elements: [] },
        { id: "unnamed", name: "", elements: [] },
      ],
    },
  } as unknown as ProjectConfig;
}

export function renderStudio(props: Partial<ThemeStudioProps> = {}) {
  const all: ThemeStudioProps = {
    open: true,
    onClose: vi.fn(),
    themes: SUMMARIES,
    project: studioProject(),
    currentThemeId: "studio-sample",
    themeOverrides: {},
    onChangeTheme: vi.fn(),
    onClearOverrides: vi.fn(),
    onRefreshThemes: vi.fn(),
    onThemeSaved: vi.fn(),
    ...props,
  };
  const user = userEvent.setup();
  const view = render(<ThemeStudio {...all} />);
  const rerender = (next: Partial<ThemeStudioProps>) => view.rerender(<ThemeStudio {...all} {...next} />);
  return { ...view, user, props: all, rerender };
}

/** The row a label heads: the label's own flex row. */
export function rowOf(label: HTMLElement): HTMLElement {
  return label.parentElement as HTMLElement;
}

/** A Theme section row, by its label. */
export function tokenRow(label: string): HTMLElement {
  const theme = sectionBody("Theme");
  return rowOf(within(theme).getByText(label, { selector: "label" }));
}

/** The body of an editor section, by its title. */
export function sectionBody(title: string): HTMLElement {
  const heading = [...document.querySelectorAll("div")].find(
    (d) => d.textContent === title && d.childElementCount === 0,
  );
  if (!heading) throw new Error(`no section titled ${title}`);
  return heading.nextElementSibling as HTMLElement;
}

/** An element type's collapsible section, by the name its summary shows. */
export function elementSection(name: string): HTMLDetailsElement {
  const summaryEl = [...document.querySelectorAll("summary")].find((s) => s.textContent === name);
  if (!summaryEl) throw new Error(`no element section ${name}`);
  return summaryEl.parentElement as HTMLDetailsElement;
}

/** One control's row inside an element section. */
export function controlRow(section: string, label: string): HTMLElement {
  return rowOf(within(elementSection(section)).getByText(label, { selector: "label" }));
}

export function textIn(row: HTMLElement): HTMLInputElement {
  return row.querySelector('input[type="text"], input:not([type])') as HTMLInputElement;
}
