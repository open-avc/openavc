import { describe, it, expect } from "vitest";
import {
  stylesheetClassNames,
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
