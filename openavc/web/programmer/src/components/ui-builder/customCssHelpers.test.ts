import { describe, it, expect } from "vitest";
import {
  stylesheetClassNames,
  stylesheetClassProperties,
  feedbackClassConflicts,
  cssClassList,
  cssClassValue,
  toggleCssClass,
  invalidCssClassNames,
} from "./customCssHelpers";

describe("stylesheetClassNames", () => {
  it("lists the classes a sheet defines, first mention first", () => {
    const css = `
      .brand-button { background: #123456; }
      .brand-fader .track { background: #222; }
      .brand-button { color: white; }
    `;
    expect(stylesheetClassNames(css)).toEqual(["brand-button", "brand-fader", "track"]);
  });

  it("reads selectors only, so a decimal in a declaration is not a class", () => {
    // .5rem in a declaration would read as a class called "5rem" to anything
    // that just regexes the whole document.
    const css = `.card { border-radius: 0.5rem; padding: .25rem; }`;
    expect(stylesheetClassNames(css)).toEqual(["card"]);
  });

  it("skips an at-rule prelude but reads the rules inside it", () => {
    const css = `@media (min-width: 37.5rem) { .wide-only { display: block; } }`;
    expect(stylesheetClassNames(css)).toEqual(["wide-only"]);
  });

  it("ignores commented-out rules", () => {
    const css = `/* .old-name { color: red; } */ .new-name { color: blue; }`;
    expect(stylesheetClassNames(css)).toEqual(["new-name"]);
  });

  it("ignores dots inside strings and attribute selectors", () => {
    const css = `.tag[data-kind=".hidden"]::after { content: ".done"; }`;
    expect(stylesheetClassNames(css)).toEqual(["tag"]);
  });

  it("is empty for nothing, blank, or a non-string", () => {
    expect(stylesheetClassNames("")).toEqual([]);
    expect(stylesheetClassNames(null)).toEqual([]);
    expect(stylesheetClassNames(undefined)).toEqual([]);
  });
});

describe("css_class round trip", () => {
  it("splits and rejoins on whitespace", () => {
    expect(cssClassList("  brand-button   big  ")).toEqual(["brand-button", "big"]);
    expect(cssClassValue(["brand-button", "big"])).toBe("brand-button big");
  });

  it("stores nothing rather than an empty string when the last class is removed", () => {
    expect(cssClassValue([])).toBeUndefined();
    expect(toggleCssClass("solo", "solo")).toBeUndefined();
  });

  it("toggles a class on and off, leaving the others in place", () => {
    expect(toggleCssClass("a b", "c")).toBe("a b c");
    expect(toggleCssClass("a b c", "b")).toBe("a c");
    expect(toggleCssClass(undefined, "a")).toBe("a");
  });
});

describe("invalidCssClassNames", () => {
  it("names what the panel would refuse", () => {
    // classList.add throws on these; the panel catches and warns, so the class
    // simply never appears on the glass.
    expect(invalidCssClassNames("2cool ok-name -3bad has space")).toEqual([
      "2cool",
      "-3bad",
    ]);
  });

  it("accepts the shapes CSS allows", () => {
    expect(invalidCssClassNames("brand_button -kebab a1 _x")).toEqual([]);
  });
});

describe("stylesheetClassProperties", () => {
  it("collects the properties a class sets, across every rule that names it", () => {
    const css = `.brand { color: white; } .brand:hover { opacity: 0.8; }`;
    expect([...(stylesheetClassProperties(css).get("brand") ?? [])].sort()).toEqual([
      "color",
      "opacity",
    ]);
  });

  it("expands the shorthands that collide with feedback", () => {
    const props = stylesheetClassProperties(`.brand { background: red; border: 1px solid #000; }`);
    const set = props.get("brand")!;
    expect(set.has("background-color")).toBe(true);
    expect(set.has("border-color")).toBe(true);
    expect(set.has("border-width")).toBe(true);
  });

  it("reads rules inside a media query", () => {
    const props = stylesheetClassProperties(`@media (min-width: 30rem) { .brand { color: red; } }`);
    expect([...(props.get("brand") ?? [])]).toEqual(["color"]);
  });
});

describe("feedbackClassConflicts", () => {
  // A button that goes green when the projector is on, in both binding shapes.
  const statesBinding = {
    show: { look: { key: "device.p1.power", states: { on: { bg_color: "#4CAF50" } } } },
  };
  const legacyBinding = {
    show: {
      look: {
        key: "var.system_power",
        condition: { equals: "on" },
        style_active: { bg_color: "#4CAF50" },
        style_inactive: { bg_color: "#424242" },
      },
    },
  };
  const css = `.brand { background: #8AB493; font-weight: 700; }
               .rounded { border-radius: 2rem; }`;

  it("names the class and what it takes over", () => {
    expect(feedbackClassConflicts("brand", css, statesBinding)).toEqual([
      { className: "brand", labels: ["background"] },
    ]);
    expect(feedbackClassConflicts("brand", css, legacyBinding)).toEqual([
      { className: "brand", labels: ["background"] },
    ]);
  });

  it("stays quiet when the class touches nothing the feedback draws", () => {
    expect(feedbackClassConflicts("rounded", css, statesBinding)).toEqual([]);
  });

  it("stays quiet when the control has no look binding at all", () => {
    expect(feedbackClassConflicts("brand", css, { do: { press: [] } })).toEqual([]);
    expect(feedbackClassConflicts("brand", css, undefined)).toEqual([]);
  });

  it("checks every class on the element, not just the first", () => {
    const both = feedbackClassConflicts("rounded brand", css, statesBinding);
    expect(both).toEqual([{ className: "brand", labels: ["background"] }]);
  });
});
