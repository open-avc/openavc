import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeStudio } from "./ThemeStudio";
import type { ProjectConfig } from "../../api/types";

const { getTheme, updateTheme, preview } = vi.hoisted(() => ({
  getTheme: vi.fn(), updateTheme: vi.fn(), preview: vi.fn(),
}));
vi.mock("../../api/restClient", async (original) => ({
  ...await original<typeof import("../../api/restClient")>(),
  getTheme, updateTheme,
}));
vi.mock("./PanelPreviewFrame", () => ({
  PanelPreviewFrame: (props: unknown) => { preview(props); return null; },
}));

const theme = {
  id: "sample", name: "Sample", _source: "custom", description: "",
  variables: { border_radius: 0.5714 },
  element_defaults: { button: { border_radius: 0.5714 }, group: { border_radius: 1 } },
};
const project = { ui: { settings: {}, pages: [] } } as unknown as ProjectConfig;

describe("Theme Studio roundness", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getTheme.mockResolvedValue(theme);
    updateTheme.mockResolvedValue(theme);
  });

  it("reads stored lengths, previews each preset and saves the same units", async () => {
    const user = userEvent.setup();
    render(<ThemeStudio open onClose={vi.fn()} themes={[]} project={project}
      currentThemeId="sample" themeOverrides={{}} onChangeTheme={vi.fn()}
      onClearOverrides={vi.fn()} onRefreshThemes={vi.fn()} />);
    expect(await screen.findByRole("radio", { name: "Standard" })).toBeChecked();

    for (const [name, rem] of [["Round", 1.1429], ["Sharp", 0], ["Standard", 0.5714]] as const) {
      await user.click(screen.getByRole("radio", { name }));
      expect(screen.getByRole("radio", { name })).toBeChecked();
      const shown = preview.mock.lastCall![0].inlineTheme;
      expect(shown.variables.border_radius).toBe(rem);
      expect(shown.element_defaults.button.border_radius).toBe(rem);
      expect(shown.element_defaults.group.border_radius).toBe(1);
    }

    await user.click(screen.getByRole("radio", { name: "Round" }));
    await user.click(screen.getByRole("button", { name: "Save Changes" }));
    await waitFor(() => expect(updateTheme).toHaveBeenCalled());
    expect(updateTheme.mock.calls[0][1].variables.border_radius).toBe(1.1429);
  });
});
