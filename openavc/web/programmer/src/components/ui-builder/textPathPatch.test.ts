import { describe, it, expect } from "vitest";
import { patchForTextPath, emptyCommitDeletes } from "./uiBuilderHelpers";
import type { UIElement } from "../../api/types";

/**
 * The write half of in-place text editing.
 *
 * The panel hands back the authored path that produced the text on screen; this
 * turns it into a patch. The cases below are the real paths panel.js records,
 * one per writer, because a wrong path writes a field the author cannot see.
 */

const el = (extra: Partial<UIElement> = {}): UIElement => ({
  id: "btn1",
  type: "button",
  ...extra,
});

describe("patchForTextPath", () => {
  it("writes a top-level label", () => {
    expect(patchForTextPath(el({ label: "Mute" }), ["label"], "Unmute"))
      .toEqual({ label: "Unmute" });
  });

  it("writes a label element's text", () => {
    expect(patchForTextPath(el({ type: "label", text: "Old" }), ["text"], "New"))
      .toEqual({ text: "New" });
  });

  it("writes a value binding's format, keeping the rest of the binding", () => {
    const element = el({
      type: "label",
      bindings: { show: { value: { key: "device.amp.draw", format: "Old: {value}" } } },
    } as Partial<UIElement>);
    const patch = patchForTextPath(
      element,
      ["bindings", "show", "value", "format"],
      "Amp draw: {value} A",
    );
    expect(patch).toEqual({
      bindings: {
        show: { value: { key: "device.amp.draw", format: "Amp draw: {value} A" } },
      },
    });
  });

  it("writes whichever half of a conditional is showing", () => {
    const element = el({
      type: "label",
      bindings: { show: { value: { key: "d.k", condition: { equals: "1" }, text_true: "On", text_false: "Off" } } },
    } as Partial<UIElement>);
    const patch = patchForTextPath(
      element,
      ["bindings", "show", "value", "text_false"],
      "Standby",
    );
    const v = (patch as Record<string, Record<string, Record<string, Record<string, string>>>>)
      .bindings.show.value;
    expect(v.text_false).toBe("Standby");
    // The other half, and the condition, are untouched.
    expect(v.text_true).toBe("On");
    expect(v.condition).toEqual({ equals: "1" });
  });

  it("writes a look state's label without disturbing its siblings", () => {
    const element = el({
      bindings: {
        show: {
          look: {
            key: "device.amp.mute",
            states: { muted: { label: "MUTED", bg_color: "#f00" }, clear: { label: "Mute" } },
          },
        },
      },
    } as Partial<UIElement>);
    const patch = patchForTextPath(
      element,
      ["bindings", "show", "look", "states", "muted", "label"],
      "Muted",
    );
    const look = (patch as Record<string, Record<string, Record<string, Record<string, Record<string, Record<string, string>>>>>>)
      .bindings.show.look;
    expect(look.states.muted).toEqual({ label: "Muted", bg_color: "#f00" });
    expect(look.states.clear).toEqual({ label: "Mute" });
    expect(look.key).toBe("device.amp.mute");
  });

  it("handles a state key containing a dot, which a dotted path would lose", () => {
    const element = el({
      bindings: { show: { look: { key: "d.level", states: { "2.5": { label: "Low" } } } } },
    } as Partial<UIElement>);
    const patch = patchForTextPath(
      element,
      ["bindings", "show", "look", "states", "2.5", "label"],
      "Quiet",
    );
    const look = (patch as Record<string, Record<string, Record<string, Record<string, Record<string, Record<string, string>>>>>>)
      .bindings.show.look;
    expect(look.states["2.5"].label).toBe("Quiet");
  });

  it("writes a toggle word through the press action array", () => {
    const element = el({
      bindings: { do: { press: [{ action: "device", mode: "toggle", on_label: "ON", off_label: "OFF" }] } },
    } as Partial<UIElement>);
    const patch = patchForTextPath(
      element,
      ["bindings", "do", "press", 0, "on_label"],
      "Running",
    );
    const press = (patch as Record<string, Record<string, Record<string, string>[]>>)
      .bindings.do.press;
    expect(Array.isArray(press)).toBe(true);
    expect(press[0].on_label).toBe("Running");
    expect(press[0].off_label).toBe("OFF");
    expect(press[0].action).toBe("device");
  });

  it("creates the structure when the author has written none of it yet", () => {
    const patch = patchForTextPath(
      el({ type: "label" }),
      ["bindings", "show", "value", "format"],
      "Draw: {value}",
    );
    expect(patch).toEqual({
      bindings: { show: { value: { format: "Draw: {value}" } } },
    });
  });

  it("does not mutate the element it was given", () => {
    const element = el({
      bindings: { show: { look: { states: { muted: { label: "MUTED" } } } } },
    } as Partial<UIElement>);
    const before = JSON.stringify(element);
    patchForTextPath(element, ["bindings", "show", "look", "states", "muted", "label"], "x");
    expect(JSON.stringify(element)).toBe(before);
  });

  it("returns null for an empty path", () => {
    expect(patchForTextPath(el(), [], "x")).toBeNull();
  });
});

describe("an empty commit", () => {
  it("blanks a slot the renderer reads by !== undefined", () => {
    const element = el({
      bindings: { show: { look: { states: { muted: { label: "MUTED" } } } } },
    } as Partial<UIElement>);
    const patch = patchForTextPath(
      element,
      ["bindings", "show", "look", "states", "muted", "label"],
      "",
    );
    const states = (patch as Record<string, Record<string, Record<string, Record<string, Record<string, string>>>>>)
      .bindings.show.look.states;
    expect(states.muted).toEqual({ label: "" });
  });

  it("deletes a slot the renderer reads by truthiness, because '' there reads as another word", () => {
    // evaluateFeedback's binary branch tests `isActive && binding.label_active`
    // and falls through to the element's own label, so storing "" would put a
    // different word on screen rather than clearing it.
    const element = el({
      label: "Mute",
      bindings: { show: { look: { label_active: "MUTED", label_inactive: "Mute" } } },
    } as Partial<UIElement>);
    const patch = patchForTextPath(
      element,
      ["bindings", "show", "look", "label_active"],
      "",
    );
    const look = (patch as Record<string, Record<string, Record<string, Record<string, string>>>>)
      .bindings.show.look;
    expect("label_active" in look).toBe(false);
    expect(look.label_inactive).toBe("Mute");
  });

  it("names the four truthiness-read slots and nothing else", () => {
    expect(emptyCommitDeletes(["bindings", "show", "look", "label_active"])).toBe(true);
    expect(emptyCommitDeletes(["bindings", "show", "look", "label_inactive"])).toBe(true);
    expect(emptyCommitDeletes(["bindings", "do", "press", 0, "on_label"])).toBe(true);
    expect(emptyCommitDeletes(["bindings", "do", "press", 0, "off_label"])).toBe(true);
    expect(emptyCommitDeletes(["label"])).toBe(false);
    expect(emptyCommitDeletes(["text"])).toBe(false);
    expect(emptyCommitDeletes(["bindings", "show", "value", "format"])).toBe(false);
    expect(emptyCommitDeletes(["bindings", "show", "look", "states", "muted", "label"])).toBe(false);
  });
});
