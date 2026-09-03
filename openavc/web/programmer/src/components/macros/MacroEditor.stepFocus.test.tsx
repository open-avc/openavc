import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * Typing into a macro step field.
 *
 * Every step row is keyed by an id minted per step OBJECT, and editing a step
 * replaces that object -- so the key changed on every keystroke, React tore the
 * row down and built a new one, and the focused input was destroyed under the
 * caret. Keystroke two went to the page body. Typing "1000" into a required
 * numeric left "1" behind: a valid-looking, silently truncated value on its way
 * to a device.
 *
 * These tests type more than one character and check what actually landed, so
 * the row can be re-keyed per edit again only by turning them red.
 */

const mocks = vi.hoisted(() => ({
  executeMacro: vi.fn(async () => ({})),
  cancelMacro: vi.fn(async () => ({})),
  listPlugins: vi.fn(async () => []),
  getDevice: vi.fn(async () => ({
    id: "tone_1",
    commands: {
      set_tone: {
        params: { frequency: { type: "integer", required: true, min: 20, max: 20000 } },
      },
    },
  })),
}));

vi.mock("../../api/restClient", () => ({
  executeMacro: mocks.executeMacro,
  cancelMacro: mocks.cancelMacro,
  listPlugins: mocks.listPlugins,
  getDevice: mocks.getDevice,
}));

vi.mock("../../store/connectionStore", () => {
  const state = { liveState: {}, connected: true };
  return {
    useConnectionStore: Object.assign(
      (selector: (s: unknown) => unknown) => selector(state),
      { getState: () => state },
    ),
  };
});

vi.mock("../../store/projectStore", () => {
  const state = { project: { devices: [] } };
  return {
    useProjectStore: Object.assign(
      (selector: (s: unknown) => unknown) => selector(state),
      { getState: () => state },
    ),
  };
});

vi.mock("../../store/logStore", () => {
  const state = {
    macroProgress: { macroId: null, status: null, activeStepPath: [] },
    stepErrors: [],
    conditionalResults: [],
    groupResults: [],
    lastRun: null,
    recentlyFired: {},
    triggerPending: {},
  };
  return {
    useLogStore: Object.assign(
      (selector: (s: unknown) => unknown) => selector(state),
      { getState: () => state },
    ),
  };
});

import { MacroEditor } from "./MacroEditor";
import type { MacroConfig, MacroStep } from "../../api/types";

/**
 * Drives the editor the way MacroView does: the parent owns the macro and every
 * edit comes back as a whole new macro carrying a whole new step object. That
 * round trip is the thing under test -- an editor that only survived mutation
 * in place would pass here and still lose the caret in the IDE.
 */
function hostFor(steps: MacroStep[]) {
  const seen = { macro: { id: "macro_1", name: "Test Macro", steps } as MacroConfig };
  function Host() {
    const [macro, setMacro] = useState(seen.macro);
    seen.macro = macro;
    return (
      <MacroEditor
        macro={macro}
        allMacros={[macro]}
        devices={[]}
        onUpdate={setMacro}
        onConvertToScript={() => {}}
      />
    );
  }
  return { Host, seen };
}

describe("macro step editing keeps the field you are typing in", () => {
  it("takes every character of a description and never drops focus", async () => {
    const user = userEvent.setup();
    const { Host, seen } = hostFor([{ action: "delay", seconds: 1 }]);
    render(<Host />);

    await user.click(screen.getByText("Delay"));
    const field = screen.getByPlaceholderText(
      /shown in panel progress/i,
    ) as HTMLInputElement;
    await user.click(field);
    await user.type(field, "ABCDEFGH");

    // The node the caret started in is the node still on screen.
    expect(document.body.contains(field)).toBe(true);
    expect(document.activeElement).toBe(field);
    expect(field.value).toBe("ABCDEFGH");
    expect(seen.macro.steps[0].description).toBe("ABCDEFGH");
  });

  it("takes every digit of a numeric field, so 1000 does not become 1", async () => {
    const user = userEvent.setup();
    const { Host, seen } = hostFor([{ action: "delay" }]);
    render(<Host />);

    await user.click(screen.getByText("Delay"));
    const field = screen.getByPlaceholderText("0") as HTMLInputElement;
    await user.click(field);
    await user.type(field, "1000");

    expect(document.activeElement).toBe(field);
    expect(seen.macro.steps[0].seconds).toBe(1000);
  });

  it("keeps a device-command parameter whole, so a 1000 Hz tone is not sent at 1 Hz", async () => {
    const user = userEvent.setup();
    const { Host, seen } = hostFor([
      { action: "device.command", device: "tone_1", command: "set_tone", params: {} },
    ]);
    render(<Host />);

    await user.click(screen.getByText("Device Command"));
    // ParamInput labels a bounded numeric with its declared range.
    const field = await screen.findByPlaceholderText("20-20000");
    await user.click(field);
    await user.type(field, "1000");

    expect(document.activeElement).toBe(field);
    expect((seen.macro.steps[0].params as Record<string, unknown>).frequency).toBe("1000");
    // The device was looked up once, not once per keystroke.
    expect(mocks.getDevice).toHaveBeenCalledTimes(1);
  });

  it("edits the step it is pointed at when two steps sit side by side", async () => {
    const user = userEvent.setup();
    const { Host, seen } = hostFor([
      { action: "delay", seconds: 1 },
      { action: "delay", seconds: 2 },
    ]);
    render(<Host />);

    // Expand the second row; the two rows are identical apart from position.
    await user.click(screen.getAllByText("Delay")[1]);
    const field = screen.getByPlaceholderText(
      /shown in panel progress/i,
    ) as HTMLInputElement;
    await user.click(field);
    await user.type(field, "second");

    expect(document.activeElement).toBe(field);
    expect(seen.macro.steps[0].description).toBeUndefined();
    expect(seen.macro.steps[1].description).toBe("second");
  });
});
