import { describe, it, expect } from "vitest";
import {
  groupUiFiles,
  isEditableUiPath,
  languageForUiPath,
  starterUiContent,
} from "./customUiFiles";

describe("which files the editor opens", () => {
  it("opens the ones a control is written in, in the right language", () => {
    expect(languageForUiPath("room_map/index.html")).toBe("html");
    expect(languageForUiPath("room_map/map.css")).toBe("css");
    expect(languageForUiPath("room_map/map.js")).toBe("javascript");
    expect(languageForUiPath("data.json")).toBe("json");
  });

  it("refuses the ones with nothing to type into", () => {
    // These belong to the control and travel with the project; they are just
    // not text, and opening one in an editor shows bytes.
    expect(isEditableUiPath("room_map/floor.png")).toBe(false);
    expect(isEditableUiPath("fonts/brand.woff2")).toBe(false);
    expect(isEditableUiPath("clip.mp4")).toBe(false);
  });

  it("is case-insensitive, because a camera names a file .PNG", () => {
    expect(isEditableUiPath("shot.PNG")).toBe(false);
    expect(languageForUiPath("PAGE.HTML")).toBe("html");
  });
});

describe("grouping the tree", () => {
  it("puts one control's files together and loose files last", () => {
    const groups = groupUiFiles([
      { path: "readme.md" },
      { path: "lights/index.html" },
      { path: "room_map/map.css" },
      { path: "room_map/index.html" },
    ]);

    expect(groups.map((g) => g.folder)).toEqual(["lights", "room_map", ""]);
    expect(groups[1].files.map((f) => f.path)).toEqual([
      "room_map/index.html",
      "room_map/map.css",
    ]);
  });
});

describe("what a new file starts with", () => {
  it("wires both message directions into a new page", () => {
    const html = starterUiContent("room_map/index.html");

    // An empty HTML file is a blank box with nothing to react to; the bridge
    // is the part worth not retyping, and it has to match what the panel
    // actually sends (docs/custom-controls.md).
    expect(html).toContain("openavc:init");
    expect(html).toContain("openavc:state");
    expect(html).toContain("openavc:action");
    // Every control should report its own errors: nothing outside the frame
    // can see them.
    expect(html).toContain("openavc:error");
  });

  it("leaves a file with no starter empty rather than inventing one", () => {
    expect(starterUiContent("floor.png")).toBe("");
  });
});
