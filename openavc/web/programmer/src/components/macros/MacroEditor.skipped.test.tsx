import { beforeEach, describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * A run of this macro that did not start.
 *
 * The Test button said "it's already running, or it's still in its cooldown"
 * whatever the server's reason was, and a start refused anywhere else (a
 * panel press, a trigger, a script) showed nothing in the editor at all.
 */

const mocks = vi.hoisted(() => ({
  projectState: {
    project: { devices: [] }, dirty: false, saving: false,
    savePending: false, conflictDetected: false, error: null as string | null,
    save: vi.fn(async () => {}),
  },
  logState: {
    macroProgress: { macroId: null, status: null, activeStepPath: [] },
    runningMacros: {},
    stepErrors: [],
    conditionalResults: [],
    groupResults: [],
    lastRun: null,
    recentlyFired: {},
    triggerPending: {},
    triggerRuns: {},
    triggerSkips: {},
    macroSkips: {} as Record<string, { code: string; message: string; at: number }>,
  },
  showError: vi.fn(),
  executeMacro: vi.fn(async () => ({ status: "completed" })),
}));

vi.mock("../../api/restClient", () => ({
  executeMacro: mocks.executeMacro,
  cancelMacro: vi.fn(async () => ({})),
  listPlugins: vi.fn(async () => []),
  getDevice: vi.fn(async () => ({ id: "x", commands: {} })),
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
  const state = mocks.projectState;
  return {
    useProjectStore: Object.assign(
      (selector: (s: unknown) => unknown) => selector(state),
      { getState: () => state },
    ),
  };
});

vi.mock("../../store/toastStore", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../store/toastStore")>(),
  showError: mocks.showError,
}));

vi.mock("../../store/logStore", () => ({
  useLogStore: Object.assign(
    (selector: (s: unknown) => unknown) => selector(mocks.logState),
    { getState: () => mocks.logState },
  ),
}));

import { MacroEditor } from "./MacroEditor";
import type { MacroConfig } from "../../api/types";

const MACRO = {
  id: "macro_1",
  name: "Lights On",
  steps: [{ action: "delay", seconds: 1 }],
} as MacroConfig;

function renderEditor() {
  return render(
    <MacroEditor
      macro={MACRO}
      allMacros={[MACRO]}
      devices={[]}
      onUpdate={() => {}}
      onConvertToScript={() => {}}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.logState.macroSkips = {};
});

describe("a run of this macro that did not start", () => {
  it("is shown with the server's reason, wherever it was refused", () => {
    mocks.logState.macroSkips = {
      macro_1: {
        code: "macro_overlap",
        message: "The macro was already running, and the macro's Overlap is set to Skip.",
        at: Date.now(),
      },
    };
    renderEditor();
    expect(
      screen.getByText(/The macro was already running, and the macro's Overlap is set to Skip\./),
    ).toBeTruthy();
    expect(screen.getByText(/Skipped/)).toBeTruthy();
  });

  it("is not shown for another macro's refused run", () => {
    mocks.logState.macroSkips = {
      macro_2: { code: "macro_cooldown", message: "The macro's Cooldown (5 seconds) had not passed since it last started.", at: Date.now() },
    };
    renderEditor();
    expect(screen.queryByText(/Skipped/)).toBeNull();
  });

  it("is told to the person who pressed Test, in the server's words", async () => {
    mocks.executeMacro.mockResolvedValueOnce({
      status: "skipped",
      code: "stopping",
      message: "The system is stopping.",
    } as never);
    renderEditor();
    await userEvent.setup().click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() =>
      expect(mocks.showError).toHaveBeenCalledWith("The macro didn't start. The system is stopping."),
    );
  });
});
