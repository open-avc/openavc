import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import type { ThemeDefinition } from "../../api/restClient";
import { customTheme, renderStudio, sectionBody, tokenRow, textIn } from "../../test/themeStudioFixtures";

const { getTheme, preview } = vi.hoisted(() => ({ getTheme: vi.fn(), preview: vi.fn() }));
vi.mock("../../api/restClient", async (original) => ({
  ...await original<typeof import("../../api/restClient")>(),
  getTheme,
}));
vi.mock("./PanelPreviewFrame", () => ({
  PanelPreviewFrame: (props: unknown) => { preview(props); return null; },
}));

const working = () => (preview.mock.lastCall![0] as { inlineTheme: ThemeDefinition }).inlineTheme;
const json = (v: unknown) => JSON.stringify(v);

/** Open the Studio on the custom theme with its variables and element
 *  defaults replaced (undefined leaves the fixture's). */
async function openOn(variables?: Record<string, unknown>, element_defaults?: ThemeDefinition["element_defaults"]) {
  const theme = customTheme();
  if (variables) theme.variables = variables;
  if (element_defaults) theme.element_defaults = element_defaults;
  getTheme.mockResolvedValue(theme);
  const view = renderStudio();
  await screen.findByText("Quick Adjust");
  return view;
}

const quick = () => sectionBody("Quick Adjust");
const group = (name: string) => within(quick()).getByRole("radiogroup", { name });
const checked = (name: string) =>
  within(group(name)).queryAllByRole("radio").filter((r) => r.getAttribute("aria-checked") === "true")
    .map((r) => r.textContent);
const pick = (groupName: string, option: string) =>
  fireEvent.click(within(group(groupName)).getByRole("radio", { name: option }));
const modified = () =>
  [...quick().querySelectorAll("span")].filter((s) => s.textContent === "modified")
    .map((s) => s.parentElement!.firstChild!.textContent);

beforeEach(() => vi.clearAllMocks());

describe("Brand Accent", () => {
  it("shows the accent in the swatch and the text box, and writes either", async () => {
    await openOn();
    const swatch = within(quick()).getByLabelText("Brand accent color") as HTMLInputElement;
    expect(swatch.value).toBe("#3366ff");
    fireEvent.input(swatch, { target: { value: "#00aa00" } });
    expect(working().variables.accent).toBe("#00aa00");
    fireEvent.change(within(quick()).getByLabelText("Brand accent hex value"), { target: { value: "red" } });
    expect(working().variables.accent).toBe("red");
    expect((within(quick()).getByLabelText("Brand accent color") as HTMLInputElement).value).toBe("#2196f3");
    expect(modified()).toEqual(["Brand Accent"]);
  });

  it("shows the default accent when the theme has none", async () => {
    await openOn({});
    expect((within(quick()).getByLabelText("Brand accent hex value") as HTMLInputElement).value).toBe("#2196F3");
  });
});

describe("Roundness", () => {
  it.each([
    [{ border_radius: 0 }, ["Sharp"]],
    [{ border_radius: 0.5714 }, ["Standard"]],
    [{ border_radius: 1.1429 }, ["Round"]],
    [{}, ["Standard"]],
  ])("reads %j as %j", async (variables, preset) => {
    await openOn(variables);
    expect(checked("Roundness")).toEqual(preset);
    expect(within(quick()).queryByText(/^Custom \(/)).toBeNull();
  });

  it("names a radius no preset matches", async () => {
    await openOn({ border_radius: 0.2857 });
    expect(checked("Roundness")).toEqual([]);
    expect(within(quick()).getByText("Custom (4px), adjust in the Theme section below")).toBeInTheDocument();
  });

  it("writes each preset in stored units and marks it modified", async () => {
    await openOn();
    pick("Roundness", "Sharp");
    expect(working().variables.border_radius).toBe(0);
    expect(working().element_defaults.button.border_radius).toBe(0);
    expect(modified()).toEqual(["Roundness"]);
    pick("Roundness", "Standard");
    expect(working().variables.border_radius).toBe(0.5714);
    expect(modified()).toEqual([]);
  });
});

describe("Surface Style", () => {
  it.each([
    [{ button: { border_width: 0 } }, "Flat"],
    [{ button: { border_width: 0, box_shadow: "none" } }, "Flat"],
    [{ button: { box_shadow: "0 1px 2px black" } }, "Layered"],
    [{ button: { border_width: 0, box_shadow: "0 1px 2px black" } }, "Layered"],
    [{ button: { box_shadow: "none" } }, "Outlined"],
    [{ button: { border_width: 0.0714, box_shadow: "" } }, "Outlined"],
    [{}, "Outlined"],
  ])("reads %j as %s", async (defaults, style) => {
    await openOn(undefined, defaults);
    expect(checked("Surface style")).toEqual([style]);
  });

  it("Flat takes every border and shadow away and lines the surfaces with their own colour", async () => {
    await openOn(undefined, { camera_preset: { bg_color: "#111111" }, keypad: { text_color: "#222222" } });
    pick("Surface style", "Flat");
    expect(json(working().element_defaults)).toMatchSnapshot();
    expect(working().variables.surface_border).toBe("#303030");
    expect(checked("Surface style")).toEqual(["Flat"]);
    expect(modified()).toEqual(["Surface Style"]);
  });

  it("Flat leaves the surface border alone when there is no surface colour", async () => {
    await openOn({ surface_border: "#123456" }, {});
    pick("Surface style", "Flat");
    expect(working().variables).toEqual({ surface_border: "#123456" });
  });

  it("Layered gives every element a hairline and the raised ones a shadow", async () => {
    await openOn({ surface: "#303030", surface_border: "#303030" }, { camera_preset: { bg_color: "#111111" } });
    pick("Surface style", "Layered");
    expect(json(working().element_defaults)).toMatchSnapshot();
    expect(working().variables.surface_border).toMatchInlineSnapshot(`"#595959"`);
    expect(checked("Surface style")).toEqual(["Layered"]);
  });

  it("Layered and Flat keep what the elements already had", async () => {
    await openOn({ surface: "#303030", surface_border: "#000000" }, {
      slider: { bg_color: "#010101" }, button: { text_color: "#020202", box_shadow: "none" }, group: { border_width: 0.5 },
    });
    pick("Surface style", "Layered");
    expect(json(working().element_defaults)).toMatchSnapshot();
    pick("Surface style", "Flat");
    expect(json(working().element_defaults)).toMatchSnapshot();
  });

  it("Layered shadows a page nav that has defaults of its own", async () => {
    await openOn({}, { page_nav: { text_color: "#ffffff" } });
    pick("Surface style", "Layered");
    expect(working().element_defaults.page_nav).toEqual({ text_color: "#ffffff", box_shadow: "0 1px 3px rgba(0,0,0,0.2)" });
    expect(working().element_defaults.camera_preset).toBeUndefined();
  });

  it("Layered and Outlined keep a surface border that differs from the surface", async () => {
    await openOn({ surface: "#303030", surface_border: "#ffffff" }, {});
    pick("Surface style", "Layered");
    expect(working().variables.surface_border).toBe("#ffffff");
    pick("Surface style", "Outlined");
    expect(working().variables.surface_border).toBe("#ffffff");
  });

  it("Outlined draws a hairline everywhere and takes the shadows away", async () => {
    await openOn({ surface: "#eeeeee", surface_border: "#eeeeee" }, { button: { box_shadow: "0 1px 2px black" } });
    pick("Surface style", "Outlined");
    expect(json(working().element_defaults)).toMatchSnapshot();
    expect(working().variables.surface_border).toMatchInlineSnapshot(`"#cacaca"`);
    expect(checked("Surface style")).toEqual(["Outlined"]);
  });
});

describe("Typography", () => {
  it.each([
    ["Inter, system-ui, sans-serif", "Sans"],
    ["system-ui, sans-serif", "Sans"],
    ["Georgia, 'Times New Roman', serif", "Serif"],
    ["'Times New Roman', serif", "Serif"],
    ["monospace", "Mono"],
    ["'Fira Code', monospace", "Mono"],
    ["Roboto Mono", "Mono"],
    ["Interstate Mono", "Sans"],
  ])("reads %s as %s", async (font, preset) => {
    await openOn({ font_family: font });
    expect(checked("Typography")).toEqual([preset]);
    expect(within(quick()).queryByText(/^Custom: /)).toBeNull();
  });

  it.each([["sans-serif"], ["'Segoe UI', sans-serif"], ["Arial"]])("names %s as a custom font", async (font) => {
    await openOn({ font_family: font });
    expect(checked("Typography")).toEqual([]);
    expect(within(quick()).getByText(`Custom: ${font}`)).toBeInTheDocument();
  });

  it("reads no font as Sans", async () => {
    await openOn({});
    expect(checked("Typography")).toEqual(["Sans"]);
  });

  it("writes each preset's font", async () => {
    await openOn();
    pick("Typography", "Serif");
    expect(working().variables.font_family).toBe("Georgia, 'Times New Roman', serif");
    expect(modified()).toEqual(["Typography"]);
    pick("Typography", "Mono");
    expect(working().variables.font_family).toBe("monospace");
    pick("Typography", "Sans");
    expect(working().variables.font_family).toBe("Inter, system-ui, sans-serif");
    expect(modified()).toEqual([]);
    expect((tokenRow("Font Family").querySelector("select") as HTMLSelectElement).value).toBe("Inter, system-ui, sans-serif");
  });
});

describe("the modified marks", () => {
  it("follow the Theme section's edits too", async () => {
    await openOn();
    fireEvent.change(textIn(tokenRow("Accent")), { target: { value: "#000001" } });
    const radius = tokenRow("Border Radius (px)").querySelector("input") as HTMLInputElement;
    fireEvent.change(radius, { target: { value: "3" } });
    fireEvent.keyDown(radius, { key: "Enter" });
    expect(modified()).toEqual(["Brand Accent", "Roundness"]);
  });
});
