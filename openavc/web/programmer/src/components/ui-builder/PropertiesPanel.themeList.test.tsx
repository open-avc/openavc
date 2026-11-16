import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ProjectConfig } from "../../api/types";
import { PropertiesPanel } from "./PropertiesPanel";

// With nothing selected, the Properties panel opens on the theme list, which
// marks the theme the project draws: the one the Builder resolved for it
// (builderTheme.ts projectThemeId), so a light project with no theme chosen is
// marked Light Modern, as its canvas draws it.

const THEMES = [
  { id: "dark-default", name: "Dark Default", preview_colors: [], source: "builtin" },
  { id: "light-modern", name: "Light Modern", preview_colors: [], source: "builtin" },
];

const LIGHT_NO_ID = {
  ui: { settings: { theme: "light", theme_id: "" }, pages: [], master_elements: [] },
} as unknown as ProjectConfig;

function showList(currentThemeId: string, onThemeChange = vi.fn()) {
  render(
    <PropertiesPanel
      element={null}
      page={null}
      project={LIGHT_NO_ID}
      themes={THEMES}
      currentThemeId={currentThemeId}
      onThemeChange={onThemeChange}
      onChange={vi.fn()}
    />,
  );
  return onThemeChange;
}

const weight = (name: string) => screen.getByText(name).style.fontWeight;

describe("the theme list", () => {
  it("marks the theme the project draws", () => {
    showList("light-modern");
    expect(weight("Light Modern")).toBe("600");
    expect(weight("Dark Default")).toBe("400");
  });

  it("switches to the theme pressed", () => {
    const onThemeChange = showList("light-modern");
    fireEvent.click(screen.getByTitle('Switch to "Dark Default"'));
    expect(onThemeChange).toHaveBeenCalledExactlyOnceWith("dark-default");
  });
});
