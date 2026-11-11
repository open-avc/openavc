import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import type { ThemeDefinition, ThemeSummary } from "../../api/restClient";
import { TYPOGRAPHY } from "./quickAdjust";
import {
  customTheme, builtinTheme, themeById, renderStudio, sectionBody, tokenRow, textIn, SUMMARIES, elementSection,
} from "../../test/themeStudioFixtures";

const { getTheme, updateTheme, createTheme, preview } = vi.hoisted(() => ({
  getTheme: vi.fn(), updateTheme: vi.fn(), createTheme: vi.fn(), preview: vi.fn(),
}));
vi.mock("../../api/restClient", async (original) => ({
  ...await original<typeof import("../../api/restClient")>(),
  getTheme, updateTheme, createTheme,
}));
vi.mock("./PanelPreviewFrame", () => ({
  PanelPreviewFrame: (props: unknown) => { preview(props); return null; },
}));

async function loaded(props: Parameters<typeof renderStudio>[0] = {}) {
  const view = renderStudio(props);
  await screen.findByText("Theme Info");
  return view;
}

const working = () => (preview.mock.lastCall![0] as { inlineTheme: ThemeDefinition }).inlineTheme;
const json = (v: unknown) => JSON.stringify(v);

/** The editor column's bar of file buttons. */
const bar = () => screen.getByRole("button", { name: /Export/ }).parentElement!;
/** The editor column, top to bottom. */
const column = () => bar().parentElement!;
const picker = () => screen.getByText("Themes").parentElement!;
const card = (name: string) =>
  within(picker()).getByText(name, { selector: "div" }).closest("div[style*='position: relative']") as HTMLElement;

function edit(value = "#abcdef") {
  fireEvent.change(textIn(tokenRow("Accent")), { target: { value } });
}

function blankName() {
  fireEvent.change(screen.getByDisplayValue(/Sample|Dark/), { target: { value: "   " } });
}

/** A server call that waits until the test lets it answer. */
function held() {
  let answer!: (v: unknown) => void;
  const promise = new Promise((resolve) => { answer = resolve; });
  return { promise, answer };
}

beforeEach(() => {
  vi.clearAllMocks();
  getTheme.mockImplementation(async (id: string) => themeById(id));
  updateTheme.mockResolvedValue({ status: "ok" });
  createTheme.mockResolvedValue({ status: "ok", theme_id: "x" });
  Element.prototype.scrollIntoView = vi.fn();
});

describe("the editor column's file buttons", () => {
  it.each([
    ["a custom theme, unchanged", "studio-sample", () => {}],
    ["a custom theme, edited", "studio-sample", () => edit()],
    ["a custom theme, edited, with a blank name", "studio-sample", () => { edit(); blankName(); }],
    ["a built-in theme, unchanged", "dark", () => {}],
    ["a built-in theme, edited", "dark", () => edit()],
    ["a built-in theme, edited, with a blank name", "dark", () => { edit(); blankName(); }],
  ])("are drawn as they always have been: %s", async (_state, currentThemeId, act_) => {
    await loaded({ currentThemeId });
    act_();
    expect(bar().outerHTML).toMatchSnapshot();
  });

  it("are drawn as they always have been while a custom theme saves", async () => {
    const save = held();
    updateTheme.mockReturnValue(save.promise);
    await loaded();
    edit();
    fireEvent.click(screen.getByRole("button", { name: /Save Changes/ }));
    expect(bar().outerHTML).toMatchSnapshot();
    await act(async () => { save.answer({ status: "ok" }); });
    await waitFor(() => expect(screen.getByRole("button", { name: /Export/ })).toBeInTheDocument());
  });

  it("are drawn as they always have been while a built-in theme is saved as a custom one", async () => {
    const create = held();
    createTheme.mockReturnValue(create.promise);
    await loaded({ currentThemeId: "dark" });
    edit();
    fireEvent.click(screen.getByRole("button", { name: /Save as Custom/ }));
    expect(bar().outerHTML).toMatchSnapshot();
    await act(async () => { create.answer({ status: "ok", theme_id: "x" }); });
  });
});

describe("the editor column", () => {
  it("lays out the bar, the line, the overrides and the sections in this order", async () => {
    await loaded({ themeOverrides: { accent: "#00ff00" } });
    edit();
    fireEvent.click(screen.getByRole("button", { name: /Discard/ }));
    const parts = [...column().children].map((c) => `${c.tagName} ${c.textContent!.slice(0, 40)}`);
    expect(parts).toMatchSnapshot();
    const scroll = column().lastElementChild!;
    expect([...scroll.children].map((c) => c.firstElementChild!.textContent!.slice(0, 30))).toMatchSnapshot();
  });

  it("draws the line and the overrides as they always have been", async () => {
    await loaded({ themeOverrides: { accent: "#00ff00", font_family: "x" } });
    edit();
    fireEvent.click(screen.getByRole("button", { name: /Discard/ }));
    expect(bar().nextElementSibling!.outerHTML).toMatchSnapshot();
    expect(bar().nextElementSibling!.nextElementSibling!.outerHTML).toMatchSnapshot();
  });

  it.each([["a custom theme", "studio-sample"], ["a built-in theme with almost nothing set", "dark"]])(
    "draws Theme Info and the Theme section as they always have been: %s",
    async (_state, currentThemeId) => {
      await loaded({ currentThemeId });
      expect(sectionBody("Theme Info").parentElement!.outerHTML).toMatchSnapshot();
      expect(sectionBody("Theme").parentElement!.outerHTML).toMatchSnapshot();
    },
  );

  it("offers, in the Theme section's font list, every stack Quick Adjust's Typography writes", async () => {
    await loaded();
    const offered = [...tokenRow("Font Family").querySelectorAll("option")].map((o) => o.value);
    for (const preset of TYPOGRAPHY) expect(offered).toContain(preset.font);
  });

  it("keeps a name and description exactly as typed", async () => {
    await loaded();
    const [name, description] = within(sectionBody("Theme Info")).getAllByRole("textbox");
    fireEvent.change(name, { target: { value: "  Spaced  name " } });
    fireEvent.change(description, { target: { value: " A  b " } });
    expect(working().name).toBe("  Spaced  name ");
    expect(working().description).toBe(" A  b ");
  });

  it("keeps a half-typed radius with the theme it was typed for when another theme is chosen", async () => {
    const view = await loaded();
    const radius = tokenRow("Border Radius (px)").querySelector("input")!;
    radius.focus();
    fireEvent.change(radius, { target: { value: "20" } });
    view.rerender({ currentThemeId: "dark" });
    await waitFor(() => expect(working().id).toBe("dark"));
    expect(tokenRow("Border Radius (px)").querySelector("input")).toBe(radius);
    expect(radius.value).toBe("0");
    fireEvent.blur(radius);
    expect(json(working().variables)).toBe(json(builtinTheme().variables));
  });

  it("draws the Theme section's edited, reset-able rows as they always have been", async () => {
    await loaded();
    edit();
    fireEvent.change(tokenRow("Font Family").querySelector("select")!, { target: { value: "monospace" } });
    expect(sectionBody("Theme").parentElement!.outerHTML).toMatchSnapshot();
  });
});

describe("the contrast checks' section", () => {
  const contrast = () => screen.getByText("Contrast (WCAG)").parentElement!;

  it.each([
    ["the custom theme", "studio-sample"],
    ["a theme with almost nothing set, read as the panel's defaults", "dark"],
  ])("is drawn as it always has been: %s", async (_state, currentThemeId) => {
    await loaded({ currentThemeId });
    expect(contrast().outerHTML).toMatchSnapshot();
  });

  it("reads a colour set empty as the panel's default, and rates every level", async () => {
    getTheme.mockImplementation(async (id: string): Promise<ThemeDefinition> => (id === "studio-sample" ? {
      ...customTheme(),
      variables: { panel_text: "", panel_bg: "", accent: "#767676", button_text: "#000000", button_bg: "#ffffff",
        danger: "transparent", success: "#000000", warning: "#595959" },
    } : themeById(id)));
    await loaded();
    expect(contrast().outerHTML).toMatchSnapshot();
  });
});

describe("the picker's cards", () => {
  const bare: ThemeSummary = { ...SUMMARIES[0], id: "bare", name: "Bare", description: "", variables: undefined as never };
  const blank: ThemeSummary = {
    ...SUMMARIES[0], id: "blank", name: "Blank", description: "",
    variables: { panel_bg: "", panel_text: "", accent: "", button_bg: "", button_text: "", button_border: "",
      surface: "", surface_border: "", font_family: "", border_radius: "" },
  };
  const rounded: ThemeSummary = {
    ...SUMMARIES[1], id: "rounded", name: "Rounded", description: "Big corners",
    variables: { border_radius: 12, button_border: "#123456", surface_border: "#654321" },
  };

  it("draw each theme's colours, font and corners, or the panel's defaults, as they always have", async () => {
    await loaded({ themes: [...SUMMARIES, bare, blank, rounded] });
    expect(picker().outerHTML).toMatchSnapshot();
  });

  it("are not drawn, and nothing is said about custom themes, while the list is empty", async () => {
    await loaded({ themes: [] });
    expect(picker().textContent).toBe("Themes Import .avctheme");
  });

  it("list a theme from anywhere but the built-in set with the custom ones", async () => {
    await loaded({ themes: [...SUMMARIES, { ...SUMMARIES[1], id: "other", name: "Other", source: "project" }] });
    const heading = within(picker()).getByText("Custom", { selector: "div" });
    expect(heading.compareDocumentPosition(card("Other")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(card("Sample").compareDocumentPosition(card("Other")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("mark the open theme while it has unsaved edits", async () => {
    await loaded();
    edit();
    expect(card("Sample").outerHTML).toMatchSnapshot();
  });

  it("keep a card hovered when the pointer leaves another one", async () => {
    await loaded();
    const dark = card("Dark");
    const sample = card("Sample");
    fireEvent.mouseEnter(dark);
    fireEvent.mouseEnter(sample);
    fireEvent.mouseLeave(dark);
    expect(screen.queryByTitle('Duplicate "Dark" as a custom theme')).toBeNull();
    expect(screen.getByTitle('Duplicate "Sample" as a custom theme')).toBeInTheDocument();
    expect(sample.outerHTML).toMatchSnapshot();
    fireEvent.mouseLeave(sample);
    expect(screen.queryByTitle('Duplicate "Sample" as a custom theme')).toBeNull();
  });

  it("draw a hovered card while a file action holds the buttons", async () => {
    const save = held();
    updateTheme.mockReturnValue(save.promise);
    await loaded();
    edit();
    fireEvent.click(screen.getByRole("button", { name: /Save Changes/ }));
    fireEvent.mouseEnter(card("Dark"));
    expect(card("Dark").outerHTML).toMatchSnapshot();
    await act(async () => { save.answer({ status: "ok" }); });
  });
});

describe("switching theme", () => {
  it("keeps every part of the column and the picker in place, the same elements", async () => {
    const { rerender } = await loaded();
    const nodes = () => [
      sectionBody("Theme Info"),
      textIn(tokenRow("Accent")),
      tokenRow("Font Family").querySelector("select"),
      screen.getByText("Contrast (WCAG)").parentElement,
      elementSection("Button"),
      bar(),
      card("Dark"),
    ];
    const before = nodes();
    const name = within(sectionBody("Theme Info")).getAllByRole("textbox")[0];
    rerender({ currentThemeId: "dark" });
    await waitFor(() => expect(screen.getByDisplayValue("Dark")).toBeInTheDocument());
    const after = nodes();
    after.forEach((node, i) => expect(node).toBe(before[i]));
    expect(within(sectionBody("Theme Info")).getAllByRole("textbox")[0]).toBe(name);
  });
});
