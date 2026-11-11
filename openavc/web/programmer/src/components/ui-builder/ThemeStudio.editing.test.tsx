import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import type { ProjectConfig } from "../../api/types";
import type { ThemeDefinition } from "../../api/restClient";
import {
  customTheme, builtinTheme, themeById, renderStudio, tokenRow, controlRow, elementSection,
  sectionBody, textIn, studioProject,
} from "../../test/themeStudioFixtures";

const { getTheme, preview } = vi.hoisted(() => ({ getTheme: vi.fn(), preview: vi.fn() }));
vi.mock("../../api/restClient", async (original) => ({
  ...await original<typeof import("../../api/restClient")>(),
  getTheme,
}));
vi.mock("./PanelPreviewFrame", () => ({
  PanelPreviewFrame: (props: unknown) => { preview(props); return null; },
}));

interface PreviewProps {
  project: ProjectConfig;
  pageId: string;
  inlineTheme: ThemeDefinition | null;
  demoState?: Record<string, unknown>;
  panelWidth: number;
  panelHeight: number;
}
const shown = () => preview.mock.lastCall![0] as PreviewProps;
const working = () => shown().inlineTheme!;
const json = (v: unknown) => JSON.stringify(v);

async function loaded(props: Parameters<typeof renderStudio>[0] = {}) {
  const view = renderStudio(props);
  await screen.findByText("Theme Info");
  return view;
}

beforeEach(() => {
  vi.clearAllMocks();
  getTheme.mockImplementation(async (id: string) => themeById(id));
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => vi.useRealTimers());

describe("opening the Studio", () => {
  it("draws nothing and loads nothing while closed", () => {
    renderStudio({ open: false });
    expect(screen.queryByText("Theme Studio")).toBeNull();
    expect(getTheme).not.toHaveBeenCalled();
    expect(preview).not.toHaveBeenCalled();
  });

  it("loads the theme as saved, without the project's overrides laid over it", async () => {
    await loaded({ themeOverrides: { accent: "#00ff00" } });
    expect(getTheme).toHaveBeenCalledExactlyOnceWith("studio-sample");
    expect(json(working())).toBe(json(customTheme()));
    expect(screen.getByText("Sample", { selector: "strong" }).parentElement).toHaveTextContent("Editing Sample(custom)");
    expect(screen.queryByText("Unsaved")).toBeNull();
    expect(screen.getByRole("button", { name: "Save Changes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save as Copy" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Delete" })).toBeEnabled();
  });

  it("says a built-in theme is read-only and offers Save as Custom", async () => {
    await loaded({ currentThemeId: "dark" });
    expect(screen.getByText("Dark", { selector: "strong" }).parentElement).toHaveTextContent("Editing Dark(built-in)");
    expect(screen.getByText("Built-in (read-only)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save Changes" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
    expect(screen.getByRole("button", { name: "Save as Custom" })).toBeEnabled();
  });

  it("keeps waiting, and says nothing, when the first theme fails to load", async () => {
    getTheme.mockRejectedValue(new Error("boom"));
    renderStudio();
    await waitFor(() => expect(getTheme).toHaveBeenCalledOnce());
    await act(async () => {});
    expect(screen.getByText("Loading theme…")).toBeInTheDocument();
    expect(screen.queryByText(/Failed to load/)).toBeNull();
    expect(shown().inlineTheme).toBeNull();
  });

  it("names a theme that failed to load over the one already open, and keeps that error", async () => {
    const { rerender } = await loaded();
    rerender({ currentThemeId: "missing" });
    expect(await screen.findByText('Failed to load "missing"')).toBeInTheDocument();
    expect(working().id).toBe("studio-sample");
    vi.useFakeTimers();
    act(() => vi.advanceTimersByTime(10000));
    expect(screen.getByText('Failed to load "missing"')).toBeInTheDocument();
  });

  it("hands the preview the project as given until a theme loads", () => {
    getTheme.mockReturnValue(new Promise(() => {}));
    const { props } = renderStudio();
    expect(shown().project).toBe(props.project);
    expect(shown().pageId).toBe("_studio_gallery");
  });

  it("loads again on reopening, back on the gallery", async () => {
    const { rerender } = await loaded();
    fireEvent.change(screen.getByTitle("What the preview pane shows"), { target: { value: "main" } });
    expect(shown().pageId).toBe("main");
    rerender({ open: false });
    rerender({ open: true });
    await waitFor(() => expect(getTheme).toHaveBeenCalledTimes(2));
    expect(shown().pageId).toBe("_studio_gallery");
  });

  it("drops a load that answers after another theme was chosen", async () => {
    let answerFirst: (t: ThemeDefinition) => void = () => {};
    getTheme.mockImplementationOnce(() => new Promise((resolve) => { answerFirst = resolve; }));
    const { rerender } = renderStudio();
    rerender({ currentThemeId: "dark" });
    await screen.findByText("Dark", { selector: "strong" });
    await act(async () => answerFirst(customTheme()));
    expect(working().id).toBe("dark");
  });
});

describe("a theme sent without variables or element defaults", () => {
  it("gets empty ones, after its other fields, and edits like any other", async () => {
    getTheme.mockResolvedValue({ id: "bare", name: "Bare", _source: "custom", description: "" });
    await loaded({ currentThemeId: "bare" });
    expect(json(working())).toBe(json({ id: "bare", name: "Bare", _source: "custom", description: "", variables: {}, element_defaults: {} }));
    expect(screen.queryByText("Unsaved")).toBeNull();
    expect((tokenRow("Border Radius (px)").querySelector("input") as HTMLInputElement).value).toBe("0");
    expect(within(sectionBody("Quick Adjust")).getByRole("radio", { name: "Standard" })).toBeChecked();
    fireEvent.change(textIn(controlRow("Label", "Text Color")), { target: { value: "#0f0f0f" } });
    fireEvent.change(textIn(tokenRow("Accent")), { target: { value: "#f0f0f0" } });
    expect(json(working())).toBe(json({
      id: "bare", name: "Bare", _source: "custom", description: "",
      variables: { accent: "#f0f0f0" }, element_defaults: { label: { text_color: "#0f0f0f" } },
    }));
  });
});

describe("the Theme section", () => {
  it("writes a typed colour, marks it, and resets it to the saved value", async () => {
    await loaded();
    fireEvent.change(textIn(tokenRow("Accent")), { target: { value: "#abcdef" } });
    expect(working().variables.accent).toBe("#abcdef");
    const label = within(tokenRow("Accent")).getByText("Accent");
    expect(label).toHaveStyle({ color: "var(--accent)", fontWeight: "600" });
    const reset = within(tokenRow("Accent")).getByTitle("Reset to saved: #3366ff");
    fireEvent.click(reset);
    expect(json(working())).toBe(json(customTheme()));
    expect(within(tokenRow("Accent")).queryByTitle(/Reset to saved/)).toBeNull();
  });

  it("deletes a cleared colour and shows the value it inherits", async () => {
    await loaded();
    const text = textIn(tokenRow("Accent"));
    fireEvent.change(text, { target: { value: "" } });
    expect("accent" in working().variables).toBe(false);
    expect(text).toHaveAttribute("placeholder", "#2196F3 (inherited)");
    expect(text).toHaveAttribute("title", "Inherits #2196F3");
    expect((tokenRow("Accent").querySelector('input[type="color"]') as HTMLInputElement).value).toBe("#2196f3");
  });

  it("marks a field saved empty as unchanged once it is cleared again", async () => {
    getTheme.mockResolvedValue({ ...customTheme(), variables: { ...customTheme().variables, danger: "" } });
    await loaded();
    expect(within(tokenRow("Danger")).queryByTitle(/Reset to saved/)).toBeNull();
    fireEvent.change(textIn(tokenRow("Danger")), { target: { value: "#ff0000" } });
    expect(within(tokenRow("Danger")).getByTitle(/^Reset to saved:\s*$/)).toBeInTheDocument();
    fireEvent.change(textIn(tokenRow("Danger")), { target: { value: "" } });
    expect("danger" in working().variables).toBe(false);
    expect(within(tokenRow("Danger")).queryByTitle(/Reset to saved/)).toBeNull();
    expect(within(tokenRow("Danger")).getByText("Danger")).toHaveStyle({ fontWeight: "400" });
  });

  it("puts a colour set again after clearing it at the end, which reads as unsaved", async () => {
    await loaded();
    fireEvent.change(textIn(tokenRow("Accent")), { target: { value: "" } });
    fireEvent.change(textIn(tokenRow("Accent")), { target: { value: "#3366ff" } });
    expect(Object.keys(working().variables).at(-1)).toBe("accent");
    expect(screen.getByText("Unsaved")).toBeInTheDocument();
  });

  it("writes a colour picked in the swatch", async () => {
    await loaded();
    fireEvent.input(tokenRow("Surface").querySelector('input[type="color"]')!, { target: { value: "#123456" } });
    expect(working().variables.surface).toBe("#123456");
  });

  it("shows the radius in pixels and drags only the controls that followed the old one", async () => {
    await loaded();
    const field = tokenRow("Border Radius (px)").querySelector("input") as HTMLInputElement;
    expect(field.value).toBe("8");
    fireEvent.change(field, { target: { value: "16" } });
    fireEvent.keyDown(field, { key: "Enter" });
    expect(working().variables.border_radius).toBe(1.1429);
    expect(working().element_defaults.button.border_radius).toBe(1.1429);
    expect(working().element_defaults.group.border_radius).toBe(1);
    expect(Object.keys(working().element_defaults)).toEqual(["button", "label", "group", "camera_preset"]);
  });

  it("keeps the radius when its field is emptied", async () => {
    await loaded();
    const field = tokenRow("Border Radius (px)").querySelector("input") as HTMLInputElement;
    fireEvent.change(field, { target: { value: "" } });
    fireEvent.blur(field);
    expect(working().variables.border_radius).toBe(0.5714);
  });

  it("chooses a font from the list, and shows Inter when none is set", async () => {
    await loaded();
    const select = tokenRow("Font Family").querySelector("select") as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual(
      ["Inter", "System UI", "Roboto", "Segoe UI", "Serif", "Monospace"],
    );
    fireEvent.change(select, { target: { value: "monospace" } });
    expect(working().variables.font_family).toBe("monospace");
    fireEvent.click(within(tokenRow("Font Family")).getByTitle("Reset to saved: Inter, system-ui, sans-serif"));
    expect(working().variables.font_family).toBe("Inter, system-ui, sans-serif");
  });

  it("lists every token with its hint", async () => {
    await loaded();
    const labels = [...sectionBody("Theme").querySelectorAll("label")].map((l) => [l.textContent, l.title]);
    expect(labels).toMatchSnapshot();
  });
});

describe("the Elements section", () => {
  it("lists the element types in order, each with its controls and hints", async () => {
    await loaded();
    const sections = [...sectionBody("Elements").querySelectorAll("details")].map((d) => ({
      name: d.querySelector("summary")!.textContent,
      controls: [...d.querySelectorAll("label")].map((l) => [l.textContent, l.title]),
    }));
    expect(sections).toMatchSnapshot();
  });

  it("writes a button colour to the theme's variables", async () => {
    await loaded();
    fireEvent.change(textIn(controlRow("Button", "Background")), { target: { value: "#010203" } });
    expect(working().variables.button_bg).toBe("#010203");
    fireEvent.click(within(controlRow("Button", "Background")).getByTitle("Reset to saved: #202020"));
    expect(json(working())).toBe(json(customTheme()));
  });

  it("shows what an unset colour inherits: the variable's value, or the theme default", async () => {
    await loaded();
    const border = textIn(controlRow("Button", "Border Color"));
    expect(border).toHaveAttribute("placeholder", "#555555 (inherited)");
    expect(border).toHaveAttribute("title", "Inherits #555555");
    const sliderBg = textIn(controlRow("Slider", "Wrapper Background"));
    expect(sliderBg).toHaveAttribute("placeholder", "inherited");
    expect(sliderBg).toHaveAttribute("title", "Inherits the theme default");
    const set = textIn(controlRow("Label", "Text Color"));
    expect(set).toHaveAttribute("placeholder", "");
    expect(set).not.toHaveAttribute("title");
  });

  it("drops a cleared element default, and the element's entry once it is empty", async () => {
    await loaded();
    fireEvent.change(textIn(controlRow("Label", "Text Color")), { target: { value: "" } });
    expect(Object.keys(working().element_defaults)).toEqual(["button", "group", "camera_preset"]);
    fireEvent.change(textIn(controlRow("Slider", "Wrapper Background")), { target: { value: "#0a0a0a" } });
    expect(working().element_defaults.slider).toEqual({ bg_color: "#0a0a0a" });
    expect(Object.keys(working().element_defaults).at(-1)).toBe("slider");
    fireEvent.click(within(controlRow("Slider", "Wrapper Background")).getByTitle("Reset to saved: undefined"));
    expect("slider" in working().element_defaults).toBe(false);
  });

  it("resets an element default to the saved one", async () => {
    await loaded();
    fireEvent.change(textIn(controlRow("Camera Preset", "Background")), { target: { value: "#999999" } });
    expect(working().element_defaults.camera_preset.bg_color).toBe("#999999");
    fireEvent.click(within(controlRow("Camera Preset", "Background")).getByTitle("Reset to saved: #111111"));
    expect(json(working())).toBe(json(customTheme()));
  });

  it("reads and writes a length in pixels, and removes it when emptied", async () => {
    await loaded();
    const width = controlRow("Button", "Border Width (px)").querySelector('input[type="number"]') as HTMLInputElement;
    expect(width.value).toBe("1");
    fireEvent.change(width, { target: { value: "2" } });
    expect(working().element_defaults.button.border_width).toBe(0.1429);
    fireEvent.change(width, { target: { value: "" } });
    expect("border_width" in working().element_defaults.button).toBe(false);
    const thresholds = controlRow("Level Meter", "Green Threshold (dB)").querySelector("input") as HTMLInputElement;
    expect(thresholds.value).toBe("");
    fireEvent.change(thresholds, { target: { value: "-10" } });
    expect(working().element_defaults.level_meter).toEqual({ green_to: -10 });
  });

  it("edits a box shadow in a text box", async () => {
    await loaded();
    const box = controlRow("Button", "Box Shadow").querySelector("textarea") as HTMLTextAreaElement;
    expect(box.value).toBe("0 2px 4px rgba(0,0,0,0.3)");
    expect(box).toHaveAttribute("title", "0 2px 4px rgba(0,0,0,0.3)");
    expect(box).toHaveAttribute("placeholder", "none");
    fireEvent.change(box, { target: { value: "none" } });
    expect(working().element_defaults.button.box_shadow).toBe("none");
    expect(controlRow("Page Nav", "Box Shadow").querySelector("textarea")!.value).toBe("");
  });

  it("draws a clear or see-through colour as a swatch that says what it is", async () => {
    await loaded();
    const field = textIn(controlRow("Label", "Background"));
    const swatch = () => controlRow("Label", "Background").querySelector("label[title]") as HTMLLabelElement;
    expect(swatch()).toHaveAttribute("title", "Inherits the theme default: pick or type a color to set one");
    fireEvent.change(field, { target: { value: "transparent" } });
    expect(swatch()).toHaveAttribute("title", "Transparent: pick or type a color to change");
    expect(swatch().style.backgroundSize).toBe("8px 8px");
    fireEvent.change(field, { target: { value: "rgba(255, 0, 0, 0.5)" } });
    expect(swatch()).toHaveAttribute("title", "rgba(255, 0, 0, 0.5): pick or type a color to change");
    expect(swatch().style.backgroundSize).toBe("");
    expect((swatch().querySelector("input") as HTMLInputElement).value).toBe("#ff0000");
    fireEvent.input(swatch().querySelector("input")!, { target: { value: "#00ff00" } });
    expect(working().element_defaults.label.bg_color).toBe("#00ff00");
    expect(controlRow("Label", "Background").querySelector("label[title]")).toBeNull();
  });

  it("writes a picked element colour", async () => {
    await loaded();
    const pick = controlRow("Gauge", "Gauge Color").querySelector('input[type="color"]') as HTMLInputElement;
    fireEvent.input(pick, { target: { value: "#00ff00" } });
    expect(working().element_defaults.gauge).toEqual({ gauge_color: "#00ff00" });
  });
});

describe("the page background", () => {
  it("drops the theme's page defaults once nothing is left in them, and writes them back at the end", async () => {
    await loaded();
    const color = [...sectionBody("Page Background").querySelectorAll("select")][0] as HTMLSelectElement;
    expect(color.value).toBe("custom");
    fireEvent.change(color, { target: { value: "inherit" } });
    expect("page_defaults" in working()).toBe(false);
    fireEvent.change(color, { target: { value: "custom" } });
    expect(working().page_defaults).toEqual({ background_color: "#1a1a2e" });
    expect(Object.keys(working()).at(-1)).toBe("page_defaults");
  });

  it("writes a patch over what is there, without the keys it clears", async () => {
    await loaded();
    const image = sectionBody("Page Background").querySelector('input[placeholder="assets://bg.jpg or https://…"]')!;
    fireEvent.change(image, { target: { value: "assets://bg.jpg" } });
    expect(json(working().page_defaults)).toBe(json({ background_color: "#222222", background_image: "assets://bg.jpg" }));
    fireEvent.change(image, { target: { value: "" } });
    expect(json(working().page_defaults)).toBe(json({ background_color: "#222222" }));
  });
});

describe("a field being typed in", () => {
  it("stays the same field, keeping the focus, while its value changes and clears", async () => {
    await loaded();
    const cases: [() => HTMLElement, string[]][] = [
      [() => textIn(tokenRow("Accent")), ["#1", "#12", "", "#123456"]],
      [() => textIn(controlRow("Label", "Text Color")), ["#a", "", "#abcdef"]],
      [() => controlRow("Button", "Border Width (px)").querySelector<HTMLElement>('input[type="number"]')!, ["3", "", "4"]],
      [() => controlRow("Button", "Box Shadow").querySelector("textarea")!, ["none", "", "0 1px 2px red"]],
      [() => within(sectionBody("Quick Adjust")).getByLabelText("Brand accent hex value"), ["#", "#00ff00", ""]],
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

  it("keeps an opaque colour's swatch while the colour changes, typed or picked", async () => {
    await loaded();
    const swatch = () => controlRow("Camera Preset", "Background").querySelector('input[type="color"]');
    const node = swatch();
    fireEvent.change(textIn(controlRow("Camera Preset", "Background")), { target: { value: "#222222" } });
    expect(swatch()).toBe(node);
    fireEvent.input(node!, { target: { value: "#333333" } });
    expect(swatch()).toBe(node);
    expect(working().element_defaults.camera_preset.bg_color).toBe("#333333");
  });
});

describe("unsaved edits", () => {
  it("are marked in the header and the picker, and Discard puts the saved theme back", async () => {
    await loaded();
    fireEvent.change(textIn(tokenRow("Accent")), { target: { value: "#abcdef" } });
    expect(screen.getByText("Unsaved")).toBeInTheDocument();
    expect(screen.getAllByTitle("Unsaved changes")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Save Changes" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(json(working())).toBe(json(customTheme()));
    expect(screen.getByText("Reverted to saved values")).toBeInTheDocument();
    expect(screen.queryByText("Unsaved")).toBeNull();
    expect(screen.getByRole("button", { name: "Discard" })).toBeDisabled();
  });

  it("include the name and description", async () => {
    await loaded();
    const info = sectionBody("Theme Info");
    const [name, description] = [...info.querySelectorAll("input")];
    fireEvent.change(name, { target: { value: "Renamed" } });
    fireEvent.change(description, { target: { value: "Changed" } });
    expect(working().name).toBe("Renamed");
    expect(working().description).toBe("Changed");
    expect(screen.getByText("Renamed", { selector: "strong" })).toBeInTheDocument();
  });

  it("clear an information line after two and a half seconds, and keep an error", async () => {
    await loaded();
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.queryByText("Reverted to saved values")).toBeNull();
    fireEvent.change(textIn(tokenRow("Accent")), { target: { value: "#abcdef" } });
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    act(() => vi.advanceTimersByTime(2499));
    expect(screen.getByText("Reverted to saved values")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByText("Reverted to saved values")).toBeNull();
  });

});

describe("the project's own theme overrides", () => {
  it("are not mentioned when there are none", async () => {
    await loaded();
    expect(screen.queryByRole("button", { name: "Clear overrides" })).toBeNull();
  });

  it.each([
    [{ accent: "#00ff00" }, "1 project override", "accent"],
    [{ accent: "#00ff00", font_family: "x" }, "2 project overrides", "accent, font_family"],
  ])("are counted and named, and Clear overrides asks for them gone (%j)", async (overrides, count, keys) => {
    const { props } = await loaded({ themeOverrides: overrides });
    expect(screen.getByText(count)).toBeInTheDocument();
    expect(screen.getByText(keys, { selector: "code" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear overrides" }));
    expect(props.onClearOverrides).toHaveBeenCalledOnce();
  });
});

describe("the preview", () => {
  it("shows the gallery with the working theme and none of the project's theme settings", async () => {
    await loaded();
    const p = shown();
    expect(p.pageId).toBe("_studio_gallery");
    expect(p.project.ui.pages.map((pg) => pg.id)).toEqual(["_studio_gallery"]);
    expect(json(p.project.ui.settings)).toBe(json({
      theme_id: "studio-sample", accent_color: "", font_family: "", theme_overrides: {}, orientation: "landscape",
    }));
    expect(p.inlineTheme).toBe(working());
    expect([p.panelWidth, p.panelHeight]).toEqual([1280, 800]);
    expect(screen.getByText("Live preview: element gallery (one of every type)")).toBeInTheDocument();
  });

  it("draws the gallery page with its demo values", async () => {
    await loaded();
    expect(shown().project.ui.pages[0]).toMatchSnapshot();
    expect(shown().demoState).toMatchSnapshot();
  });

  it("can show one of the project's pages instead, by name or id", async () => {
    await loaded({ panelWidth: 1024, panelHeight: 600 });
    const select = screen.getByTitle("What the preview pane shows") as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual(
      ["Preview: Element Gallery", "Preview: Main Page", "Preview: unnamed"],
    );
    fireEvent.change(select, { target: { value: "main" } });
    const p = shown();
    expect(p.pageId).toBe("main");
    expect(p.project.ui.pages).toEqual(studioProject().ui.pages);
    expect(p.demoState).toBeUndefined();
    expect(p.project.ui.settings.theme_id).toBe("studio-sample");
    expect([p.panelWidth, p.panelHeight]).toEqual([1024, 600]);
    expect(screen.getByText("Live preview: Main Page")).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "unnamed" } });
    expect(screen.getByText("Live preview: unnamed")).toBeInTheDocument();
  });
});

describe("a control clicked in the preview", () => {
  const post = (data: unknown, origin = window.location.origin) =>
    act(() => { window.dispatchEvent(new MessageEvent("message", { data, origin })); });

  it("opens its type's section alone, scrolls to it and outlines it for a moment", async () => {
    await loaded();
    vi.useFakeTimers();
    elementSection("Label").open = true;
    post({ type: "openavc:theme-element-click", elementType: "camera_preset" });
    const open = [...sectionBody("Elements").querySelectorAll("details")].filter((d) => d.open);
    expect(open).toEqual([elementSection("Camera Preset")]);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "nearest" });
    expect(elementSection("Camera Preset").style.outline).toBe("2px solid var(--accent)");
    act(() => vi.advanceTimersByTime(1200));
    expect(elementSection("Camera Preset").style.outline).toBe("");
  });

  it("opens the section named for each element type", async () => {
    await loaded();
    const types: [string, string][] = [
      ["button", "Button"], ["page_nav", "Page Nav"], ["camera_preset", "Camera Preset"], ["keypad", "Keypad"],
      ["label", "Label"], ["slider", "Slider"], ["fader", "Fader"], ["select", "Select"], ["text_input", "Text Input"],
      ["status_led", "Status LED"], ["gauge", "Gauge"], ["level_meter", "Level Meter"], ["list", "List"],
      ["matrix", "Matrix"], ["group", "Group"], ["image", "Image"], ["clock", "Clock"],
    ];
    for (const [type, name] of types) {
      post({ type: "openavc:theme-element-click", elementType: type });
      expect([...sectionBody("Elements").querySelectorAll("details")].filter((d) => d.open))
        .toEqual([elementSection(name)]);
    }
  });

  it("can open the same section again after it was closed", async () => {
    await loaded();
    post({ type: "openavc:theme-element-click", elementType: "gauge" });
    elementSection("Gauge").open = false;
    post({ type: "openavc:theme-element-click", elementType: "gauge" });
    expect(elementSection("Gauge").open).toBe(true);
  });

  it("moves the outline when another control is clicked before it fades", async () => {
    await loaded();
    vi.useFakeTimers();
    post({ type: "openavc:theme-element-click", elementType: "gauge" });
    post({ type: "openavc:theme-element-click", elementType: "slider" });
    expect(elementSection("Gauge").style.outline).toBe("");
    expect(elementSection("Slider").style.outline).toBe("2px solid var(--accent)");
  });

  it.each([
    ["another origin", { type: "openavc:theme-element-click", elementType: "gauge" }, "https://elsewhere.test"],
    ["another message", { type: "openavc:something", elementType: "gauge" }, undefined],
    ["a type with no section", { type: "openavc:theme-element-click", elementType: "custom" }, undefined],
  ])("is ignored from %s", async (_why, data, origin) => {
    await loaded();
    elementSection("Label").open = true;
    post(data, origin);
    expect([...sectionBody("Elements").querySelectorAll("details")].filter((d) => d.open))
      .toEqual([elementSection("Label")]);
  });
});

describe("the arrow keys on a section's name", () => {
  it("open the next or previous section alone and stop at either end", async () => {
    await loaded();
    const summary = (name: string) => elementSection(name).querySelector("summary")!;
    elementSection("Label").open = true;
    summary("Button").focus();
    fireEvent.keyDown(summary("Button"), { key: "ArrowDown" });
    expect(document.activeElement).toBe(summary("Page Nav"));
    expect([...sectionBody("Elements").querySelectorAll("details")].filter((d) => d.open))
      .toEqual([elementSection("Page Nav")]);
    fireEvent.keyDown(summary("Page Nav"), { key: "ArrowUp" });
    expect(document.activeElement).toBe(summary("Button"));
    fireEvent.keyDown(summary("Button"), { key: "ArrowUp" });
    expect(document.activeElement).toBe(summary("Button"));
    expect(elementSection("Button").open).toBe(true);
    summary("Clock").focus();
    fireEvent.keyDown(summary("Clock"), { key: "ArrowDown" });
    expect(document.activeElement).toBe(summary("Clock"));
  });

  it("leave the sections alone from a field, or for another key", async () => {
    await loaded();
    const field = textIn(controlRow("Label", "Text Color"));
    field.focus();
    const down = fireEvent.keyDown(field, { key: "ArrowDown" });
    expect(down).toBe(true);
    const summaryEl = elementSection("Label").querySelector("summary")!;
    summaryEl.focus();
    fireEvent.keyDown(summaryEl, { key: "ArrowRight" });
    expect([...sectionBody("Elements").querySelectorAll("details")].some((d) => d.open)).toBe(false);
  });
});

describe("the contrast checks", () => {
  it("rate each pair, with an unset colour read as its default and an unreadable one as n/a", async () => {
    await loaded();
    const body = screen.getByText("WCAG AA requires 4.5:1 for normal text. AAA requires 7:1.").parentElement!;
    const lines = [...body.children].slice(0, -1).map((r) => r.textContent);
    expect(lines).toMatchSnapshot();
    const pair = [...body.children][0].children[2];
    expect(pair.children[0]).toHaveStyle({ background: "#ffffff" });
    expect(pair.children[1]).toHaveStyle({ background: "#000000" });
    expect(lines.filter((l) => l!.endsWith("fail"))).toHaveLength(3);
  });

  it("count the failing pairs in the title", async () => {
    await loaded();
    expect(screen.getByText("3 failing")).toBeInTheDocument();
    fireEvent.change(textIn(tokenRow("Text Color")), { target: { value: "#000000" } });
    expect(screen.getByText("4 failing")).toBeInTheDocument();
    fireEvent.change(textIn(tokenRow("Accent")), { target: { value: "#ffffff" } });
    fireEvent.change(textIn(tokenRow("Text Color")), { target: { value: "#ffffff" } });
    fireEvent.change(textIn(controlRow("Button", "Background")), { target: { value: "#000000" } });
    fireEvent.change(textIn(controlRow("Button", "Text")), { target: { value: "#ffffff" } });
    expect(screen.getByText("1 failing")).toBeInTheDocument();
    fireEvent.change(textIn(tokenRow("Accent")), { target: { value: "#767676" } });
    expect(screen.queryByText(/failing$/)).toBeNull();
  });
});

describe("the header", () => {
  it("offers Reset Element Styles only when the Builder gives it one", async () => {
    await loaded();
    expect(screen.queryByRole("button", { name: "Reset Element Styles" })).toBeNull();
  });

  it("asks the Builder to reset element styles", async () => {
    const onResetElementStyles = vi.fn();
    await loaded({ onResetElementStyles });
    fireEvent.click(screen.getByRole("button", { name: "Reset Element Styles" }));
    expect(onResetElementStyles).toHaveBeenCalledOnce();
  });
});

describe("a built-in theme", () => {
  it("is edited like any other until it is saved as a copy", async () => {
    await loaded({ currentThemeId: "dark" });
    fireEvent.change(textIn(tokenRow("Accent")), { target: { value: "#00ff00" } });
    expect(working().variables).toEqual({ accent: "#00ff00" });
    expect(json(builtinTheme().variables)).not.toBe(json(working().variables));
  });
});
