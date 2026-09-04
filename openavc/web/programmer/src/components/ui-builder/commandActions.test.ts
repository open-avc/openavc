import { describe, it, expect } from "vitest";
import {
  elementCommandActions,
  projectCommandActions,
  validateProject,
} from "./uiBuilderHelpers";
import type { ProjectConfig, UIElement } from "../../api/types";

// Validate is the check an integrator runs before handing a room over, and it
// answered "No Issues" for a fader whose command was refused on every press --
// because this file holds the project and not the drivers, so it could not ask.
// It asks now, over `POST /api/ui/validate-actions`, and looks the answers back
// up by the action OBJECT rather than by a path spelled twice. That is what
// these pin: the walk finds every action the engine would run, and what comes
// back lands on the right control.

const SET_FADER = {
  action: "device.command",
  device: "amp",
  command: "set_fader",
  params: {},
};

function project(elements: UIElement[]): ProjectConfig {
  return {
    project: { id: "p", name: "P", description: "" },
    devices: [{ id: "amp", name: "Amp", driver: "acme_amp" }],
    macros: [],
    ui: {
      settings: {},
      pages: [{ id: "main", name: "Main", elements, layouts: [] }],
      master_elements: [],
    },
  } as unknown as ProjectConfig;
}

function fader(bindings: Record<string, unknown>): UIElement {
  return { id: "fader_1", type: "fader", bindings } as unknown as UIElement;
}

describe("elementCommandActions", () => {
  it("finds a command in any interaction slot", () => {
    const el = fader({ do: { change: [SET_FADER], press: [SET_FADER] } });
    expect(elementCommandActions(el)).toEqual([SET_FADER, SET_FADER]);
  });

  it("finds one written as a bare object, the way the runtime runs it", () => {
    expect(elementCommandActions(fader({ do: { change: SET_FADER } }))).toEqual([
      SET_FADER,
    ]);
  });

  it("descends into a value_map, because the engine runs each branch", () => {
    const el = fader({
      do: { select: [{ action: "value_map", map: { "1": SET_FADER } }] },
    });
    expect(elementCommandActions(el)).toEqual([SET_FADER]);
  });

  it("leaves out an action with no command chosen yet", () => {
    // Nothing to ask about, and the Incomplete badge already says so.
    const el = fader({ do: { change: [{ action: "device.command", device: "amp" }] } });
    expect(elementCommandActions(el)).toEqual([]);
  });

  it("returns the project's own objects, so answers can be keyed by identity", () => {
    const el = fader({ do: { change: [SET_FADER] } });
    expect(elementCommandActions(el)[0]).toBe(SET_FADER);
  });

  it("covers master elements as well as pages", () => {
    const p = project([]);
    p.ui.master_elements = [fader({ do: { press: [SET_FADER] } })] as never;
    expect(projectCommandActions(p)).toEqual([SET_FADER]);
  });

  it("covers macro steps, which Validate already reports on", () => {
    // A gate that names a step aimed at a deleted device and stays quiet about
    // one the device would refuse is the same half-answer, one level down.
    const step = { action: "device.command", device: "amp", command: "set_fader", params: {} };
    const nested = { action: "conditional", then_steps: [step] };
    const p = project([]);
    p.macros = [{ id: "m", name: "M", steps: [nested] }] as never;
    expect(projectCommandActions(p)).toEqual([step]);
  });
});

describe("validateProject with what the platform answered", () => {
  it("reports the refusal against the control that causes it", () => {
    const el = fader({ do: { change: [SET_FADER] } });
    const answers = new Map([[SET_FADER, ["'set_fader': 'channel' is required"]]]);
    const issues = validateProject(project([el]), answers as never);
    expect(issues).toContainEqual({
      severity: "error",
      message: "'set_fader': 'channel' is required",
      location: "Main > fader_1 > change",
      pageId: "main",
      elementId: "fader_1",
    });
  });

  it("reports a macro step the same way", () => {
    const step = { action: "device.command", device: "amp", command: "set_fader", params: {} };
    const p = project([]);
    p.macros = [{ id: "m", name: "Startup", steps: [step] }] as never;
    const answers = new Map([[step, ["'set_fader': 'channel' is required"]]]);
    const issues = validateProject(p, answers as never);
    expect(issues).toContainEqual({
      severity: "error",
      message: "'set_fader': 'channel' is required",
      location: 'Macro "Startup" > device.command',
    });
  });

  it("claims nothing when it was not asked", () => {
    // The old behaviour, and still the right one when the request could not be
    // made: silence, not a guess. Every other finding must still come through.
    const el = fader({ do: { change: [SET_FADER] } });
    const issues = validateProject(project([el]));
    expect(issues.filter((i) => i.message.includes("is required"))).toEqual([]);
  });
});
