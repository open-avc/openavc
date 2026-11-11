import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import type { ThemeDefinition } from "../../api/restClient";
import {
  customTheme, builtinTheme, themeById, renderStudio, sectionBody, rowOf, tokenRow,
} from "../../test/themeStudioFixtures";

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
const pageDefaults = () => json(working().page_defaults);

/** Open the Studio on the custom fixture with these page defaults (none: the key left out). */
async function loadedWith(page: Record<string, unknown> | null = { background_color: "#222222" }) {
  const theme = customTheme();
  if (page) theme.page_defaults = page;
  else delete theme.page_defaults;
  getTheme.mockImplementation(async (id: string) => (id === "studio-sample" ? theme : themeById(id)));
  const view = renderStudio();
  await screen.findByText("Theme Info");
  return view;
}

/** The section's title, found by its own text: a "modified" mark may follow it. */
const title = () => [...document.querySelectorAll("div")].find(
  (d) => d.firstChild?.nodeType === Node.TEXT_NODE && d.firstChild.textContent === "Page Background",
)!;
const body = () => title().nextElementSibling as HTMLElement;
const colorMode = () => body().querySelector("select") as HTMLSelectElement;
/** The custom colour's row: the one right after the colour choice. */
const customRow = () => rowOf(colorMode()).nextElementSibling as HTMLElement | null;
const labelled = (label: string) => rowOf(within(body()).getByText(label, { selector: "label" }));
const queryLabelled = (label: string) => within(body()).queryByText(label, { selector: "label" });
const text = (row: HTMLElement) => row.querySelector('input[type="text"]') as HTMLInputElement;
const swatch = (row: HTMLElement) => row.querySelector('input[type="color"]') as HTMLInputElement;
const gradientBox = () => within(body()).getByRole("checkbox") as HTMLInputElement;
const imageField = () => body().querySelector('input[placeholder="assets://bg.jpg or https://…"]') as HTMLInputElement;
const numberIn = (label: string) => labelled(label).querySelector("input") as HTMLInputElement;

function commitNumber(field: HTMLInputElement, value: string) {
  fireEvent.change(field, { target: { value } });
  fireEvent.keyDown(field, { key: "Enter" });
}

beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
});

describe("the page background's colour", () => {
  it("reads a colour the theme sets as a custom colour, with its swatch and its text", async () => {
    await loadedWith();
    expect(colorMode().value).toBe("custom");
    expect([...colorMode().options].map((o) => [o.value, o.textContent])).toEqual([
      ["inherit", "Theme page background (default)"], ["custom", "Custom color"],
    ]);
    expect(swatch(customRow()!).value).toBe("#222222");
    expect(text(customRow()!).value).toBe("#222222");
    expect(text(customRow()!).hasAttribute("placeholder")).toBe(false);
    expect(text(customRow()!).hasAttribute("title")).toBe(false);
    expect(gradientBox().checked).toBe(false);
    expect(queryLabelled("From")).toBeNull();
    expect(imageField().value).toBe("");
    expect(queryLabelled("Fit")).toBeNull();
  });

  it.each([
    ["a token", { background_color: "var(--panel-bg)" }],
    ["a number", { background_color: 5 }],
  ])("reads %s as the theme's own page background and keeps it until a colour is chosen", async (_what, page) => {
    await loadedWith(page);
    expect(colorMode().value).toBe("inherit");
    expect(customRow()).toBeNull();
    expect(pageDefaults()).toBe(json(page));
    fireEvent.change(colorMode(), { target: { value: "custom" } });
    expect(pageDefaults()).toBe(json({ background_color: "#1a1a2e" }));
  });

  it("writes a typed or picked colour, and an emptied one goes back to the theme's", async () => {
    await loadedWith({ background_color: "#222222", background_image: "a" });
    fireEvent.change(text(customRow()!), { target: { value: "#123456" } });
    expect(pageDefaults()).toBe(json({ background_color: "#123456", background_image: "a" }));
    fireEvent.input(swatch(customRow()!), { target: { value: "#654321" } });
    expect(pageDefaults()).toBe(json({ background_color: "#654321", background_image: "a" }));
    fireEvent.change(text(customRow()!), { target: { value: "" } });
    expect(pageDefaults()).toBe(json({ background_image: "a" }));
    expect(colorMode().value).toBe("inherit");
    expect(customRow()).toBeNull();
  });

  it("keeps a typed token but stops showing it as a custom colour", async () => {
    await loadedWith();
    fireEvent.change(text(customRow()!), { target: { value: "var(--accent)" } });
    expect(pageDefaults()).toBe(json({ background_color: "var(--accent)" }));
    expect(colorMode().value).toBe("inherit");
    expect(customRow()).toBeNull();
  });

  it("draws a see-through custom colour as a swatch over a picker seeded with its own colour", async () => {
    await loadedWith({ background_color: "rgba(0,0,0,0)" });
    expect(colorMode().value).toBe("custom");
    expect(customRow()!.querySelector("label")).toHaveAttribute("title", "rgba(0,0,0,0): pick or type a color to change");
    expect(swatch(customRow()!).value).toBe("#000000");
  });
});

describe("the page background's gradient", () => {
  it.each([
    ["the custom colour", { background_color: "#222222" }, "#222222"],
    ["the default colour under a token", { background_color: "var(--accent)" }, "#1a1a2e"],
  ])("turns on from %s and off again", async (_from, page, from) => {
    await loadedWith(page);
    fireEvent.click(gradientBox());
    expect(pageDefaults()).toBe(json({ ...page, background_gradient: { from, to: "#000000", angle: 180 } }));
    expect(text(labelled("From")).value).toBe(from);
    expect(text(labelled("To")).value).toBe("#000000");
    expect(numberIn("Angle (deg)").value).toBe("180");
    fireEvent.click(gradientBox());
    expect(pageDefaults()).toBe(json(page));
    expect(queryLabelled("From")).toBeNull();
  });

  it("turns on for a theme with no page background, which gains one at the end", async () => {
    await loadedWith(null);
    fireEvent.click(gradientBox());
    expect(json(working().page_defaults)).toBe(json({ background_gradient: { from: "#1a1a2e", to: "#000000", angle: 180 } }));
    expect(Object.keys(working()).at(-1)).toBe("page_defaults");
  });

  it("reads a gradient that is not an object as none", async () => {
    await loadedWith({ background_gradient: "linear" });
    expect(gradientBox().checked).toBe(false);
    fireEvent.click(gradientBox());
    expect(pageDefaults()).toBe(json({ background_gradient: { from: "#1a1a2e", to: "#000000", angle: 180 } }));
  });

  it("writes its colours and angle in one order, filling in what the theme's gradient leaves out", async () => {
    await loadedWith({ background_gradient: { to: "#ffffff", extra: 1 } });
    expect(gradientBox().checked).toBe(true);
    expect(text(labelled("From")).value).toBe("");
    expect(swatch(labelled("From")).value).toBe("#1a1a2e");
    expect(numberIn("Angle (deg)").value).toBe("180");
    fireEvent.change(text(labelled("From")), { target: { value: "#111111" } });
    expect(pageDefaults()).toBe(json({ background_gradient: { from: "#111111", to: "#ffffff", angle: 180 } }));
    fireEvent.input(swatch(labelled("To")), { target: { value: "#333333" } });
    expect(pageDefaults()).toBe(json({ background_gradient: { from: "#111111", to: "#333333", angle: 180 } }));
    fireEvent.input(swatch(labelled("From")), { target: { value: "#444444" } });
    expect(pageDefaults()).toBe(json({ background_gradient: { from: "#444444", to: "#333333", angle: 180 } }));
    fireEvent.change(text(labelled("To")), { target: { value: "#555555" } });
    expect(pageDefaults()).toBe(json({ background_gradient: { from: "#444444", to: "#555555", angle: 180 } }));
    commitNumber(numberIn("Angle (deg)"), "90");
    expect(pageDefaults()).toBe(json({ background_gradient: { from: "#444444", to: "#555555", angle: 90 } }));
    commitNumber(numberIn("Angle (deg)"), "400");
    expect(working().page_defaults!.background_gradient).toEqual({ from: "#444444", to: "#555555", angle: 360 });
  });

  it("ends a gradient saved without an end colour on black, shown empty", async () => {
    await loadedWith({ background_gradient: { from: "#111111" } });
    expect(text(labelled("To")).value).toBe("");
    expect(swatch(labelled("To")).value).toBe("#000000");
    commitNumber(numberIn("Angle (deg)"), "45");
    expect(pageDefaults()).toBe(json({ background_gradient: { from: "#111111", to: "#000000", angle: 45 } }));
  });

  it("keeps an emptied gradient colour inside the gradient, with its swatch on the default", async () => {
    await loadedWith({ background_gradient: { from: "#111111", to: "#222222", angle: 45 } });
    fireEvent.change(text(labelled("From")), { target: { value: "" } });
    fireEvent.change(text(labelled("To")), { target: { value: "" } });
    expect(pageDefaults()).toBe(json({ background_gradient: { from: "", to: "", angle: 45 } }));
    expect(swatch(labelled("From")).value).toBe("#1a1a2e");
    expect(swatch(labelled("To")).value).toBe("#000000");
  });
});

describe("the page background's image", () => {
  it("shows its fit, position and opacity once there is one, and drops all four when it is cleared", async () => {
    await loadedWith();
    fireEvent.change(imageField(), { target: { value: "assets://bg.jpg" } });
    const fit = labelled("Fit").querySelector("select") as HTMLSelectElement;
    expect(fit.value).toBe("cover");
    expect([...fit.options].map((o) => [o.value, o.textContent])).toEqual([
      ["cover", "Cover"], ["contain", "Contain"], ["auto", "Actual size"], ["100% 100%", "Stretch"],
    ]);
    const position = text(labelled("Position"));
    expect(position.value).toBe("center");
    expect(position).toHaveAttribute("placeholder", "center");
    expect(numberIn("Opacity").value).toBe("1");
    fireEvent.change(fit, { target: { value: "contain" } });
    fireEvent.change(position, { target: { value: "top" } });
    commitNumber(numberIn("Opacity"), "0.5");
    expect(pageDefaults()).toBe(json({
      background_color: "#222222", background_image: "assets://bg.jpg", background_image_size: "contain",
      background_image_position: "top", background_image_opacity: 0.5,
    }));
    commitNumber(numberIn("Opacity"), "3");
    expect(working().page_defaults!.background_image_opacity).toBe(1);
    fireEvent.change(text(labelled("Position")), { target: { value: "" } });
    expect(pageDefaults()).toBe(json({
      background_color: "#222222", background_image: "assets://bg.jpg", background_image_size: "contain",
      background_image_opacity: 1,
    }));
    fireEvent.change(imageField(), { target: { value: "" } });
    expect(pageDefaults()).toBe(json({ background_color: "#222222" }));
    expect(queryLabelled("Fit")).toBeNull();
  });

  it("reads settings that are not text as their defaults, and any change sweeps out an empty one", async () => {
    await loadedWith({
      background_image: "a", background_image_size: 3, background_image_position: null,
      background_image_opacity: "0.25", background_color: "",
    });
    expect(colorMode().value).toBe("inherit");
    expect((labelled("Fit").querySelector("select") as HTMLSelectElement).value).toBe("cover");
    expect(text(labelled("Position")).value).toBe("center");
    expect(numberIn("Opacity").value).toBe("0.25");
    fireEvent.change(labelled("Fit").querySelector("select")!, { target: { value: "auto" } });
    expect(pageDefaults()).toBe(json({ background_image: "a", background_image_size: "auto", background_image_opacity: "0.25" }));
  });

  it("reads no opacity as fully opaque", async () => {
    await loadedWith({ background_image: "a", background_image_opacity: null });
    expect(numberIn("Opacity").value).toBe("1");
  });
});

describe("the page background's title", () => {
  it("is laid out as a row and marks a change from the saved page background", async () => {
    await loadedWith();
    expect(title()).toHaveTextContent(/^Page Background$/);
    expect(title()).toHaveStyle({ display: "flex", alignItems: "center", color: "var(--text-secondary)" });
    fireEvent.change(text(customRow()!), { target: { value: "#333333" } });
    expect(title()).toHaveTextContent(/^Page Backgroundmodified$/);
    expect(within(title()).getByText("modified")).toHaveStyle({ color: "var(--accent)", marginLeft: "6px", fontSize: "9px" });
    fireEvent.change(text(customRow()!), { target: { value: "#222222" } });
    expect(title()).toHaveTextContent(/^Page Background$/);
  });

  it("is the only section title laid out as a row", async () => {
    await loadedWith();
    for (const name of ["Theme Info", "Quick Adjust", "Theme", "Elements"]) {
      expect(sectionBody(name).previousElementSibling).toHaveAttribute("style", expect.not.stringContaining("display"));
    }
    const contrast = screen.getByText(/^Contrast \(WCAG\)/).closest("div") as HTMLElement;
    expect(contrast.getAttribute("style")).not.toContain("display");
  });

  it("marks the same values in another order as changed", async () => {
    await loadedWith({ background_color: "#222222", background_image: "a" });
    fireEvent.change(colorMode(), { target: { value: "inherit" } });
    fireEvent.change(colorMode(), { target: { value: "custom" } });
    fireEvent.change(text(customRow()!), { target: { value: "#222222" } });
    expect(pageDefaults()).toBe(json({ background_image: "a", background_color: "#222222" }));
    expect(title()).toHaveTextContent(/^Page Backgroundmodified$/);
  });

  it("does not mark a theme saved with empty page defaults once they are gone", async () => {
    await loadedWith({ background_image: "a" });
    fireEvent.change(imageField(), { target: { value: "" } });
    expect("page_defaults" in working()).toBe(false);
    expect(title()).toHaveTextContent(/^Page Backgroundmodified$/);
    fireEvent.change(imageField(), { target: { value: "a" } });
    expect(title()).toHaveTextContent(/^Page Background$/);
  });
});

describe("a number typed into the page background", () => {
  it("stays with the theme it was typed for when another theme is chosen", async () => {
    const dark: ThemeDefinition = {
      ...builtinTheme(),
      page_defaults: { background_image: "b", background_gradient: { from: "#000001", to: "#000002", angle: 45 } },
    };
    getTheme.mockImplementation(async (id: string) => (id === "dark" ? dark : {
      ...customTheme(),
      page_defaults: { background_image: "a", background_gradient: { from: "#111111", to: "#222222", angle: 90 } },
    }));
    const view = renderStudio();
    await screen.findByText("Theme Info");
    const angle = numberIn("Angle (deg)");
    const opacity = numberIn("Opacity");
    angle.focus();
    fireEvent.change(angle, { target: { value: "500" } });
    fireEvent.change(opacity, { target: { value: "5" } });
    view.rerender({ currentThemeId: "dark" });
    await waitFor(() => expect(working().id).toBe("dark"));
    expect(numberIn("Angle (deg)")).toBe(angle);
    expect(angle.value).toBe("45");
    expect(opacity.value).toBe("1");
    fireEvent.blur(angle);
    fireEvent.blur(opacity);
    expect(json(working().page_defaults)).toBe(json(dark.page_defaults));
  });
});

describe("a page background or theme field being typed in", () => {
  it("stays the same field, keeping the focus, while its value changes", async () => {
    await loadedWith({ background_color: "#222222", background_image: "a", background_gradient: { from: "#111111", to: "#222222", angle: 10 } });
    const select = () => tokenRow("Font Family").querySelector("select") as HTMLSelectElement;
    const cases: [() => HTMLElement, string[]][] = [
      [() => text(customRow()!), ["#1", "#12", "#123456"]],
      [() => text(labelled("From")), ["#a", "", "#abcdef"]],
      [() => text(labelled("To")), ["#b", "", "#bbbbbb"]],
      [() => numberIn("Angle (deg)"), ["1", "", "20"]],
      [imageField, ["b", "bg.jpg"]],
      [() => text(labelled("Position")), ["top", "", "left"]],
      [() => numberIn("Opacity"), ["0.3", "", "0.4"]],
      [() => sectionBody("Theme Info").querySelectorAll("input")[0], ["N", "", "Named"]],
      [() => sectionBody("Theme Info").querySelectorAll("input")[1], ["D", "", "Described"]],
      [() => tokenRow("Border Radius (px)").querySelector("input")!, ["1", "", "12"]],
      [select, ["monospace", "system-ui, sans-serif"]],
    ];
    for (const [field, values] of cases) {
      const node = field();
      node.focus();
      for (const value of values) {
        fireEvent.change(node, { target: { value } });
        expect(field()).toBe(node);
        expect(document.activeElement).toBe(node);
      }
    }
  });
});

describe("the page background's markup", () => {
  it.each([
    ["as loaded", { background_color: "#222222" }],
    ["under a token, with a gradient and an image", {
      background_color: "var(--accent)", background_gradient: { from: "#111111", to: "rgba(0,0,0,0.5)", angle: 30 },
      background_image: "assets://a.png", background_image_size: "contain", background_image_position: "top",
      background_image_opacity: 0.5,
    }],
  ])("is drawn as it always has been (%s)", async (_state, page) => {
    await loadedWith(page);
    expect(title().parentElement!.outerHTML).toMatchSnapshot();
  });
});
