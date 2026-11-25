import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
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
    stepErrors: [] as unknown[],
    conditionalResults: [] as unknown[],
    groupResults: [],
    stepSkips: [] as unknown[],
    lastRun: null as unknown,
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
  mocks.logState.stepSkips = [];
  mocks.logState.lastRun = null;
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

describe("a step that did not do its work, in a run that went on", () => {
  const HELP = {
    id: "macro_1",
    name: "Call for Help",
    steps: [{ action: "help.request", message: "Room 2" }],
  } as MacroConfig;
  const HELD_BACK = {
    macroId: "macro_1",
    stepPath: [0],
    action: "help.request",
    code: "help_cooldown",
    message:
      "Help was asked for less than 300 seconds ago, and this step sends at most once in that time, so no new request was sent.",
    at: Date.now(),
  };

  function renderHelp() {
    return render(
      <MacroEditor
        macro={HELP}
        allMacros={[HELP]}
        devices={[]}
        onUpdate={() => {}}
        onConvertToScript={() => {}}
      />,
    );
  }

  it("says so on its step, in the server's words", () => {
    mocks.logState.stepSkips = [HELD_BACK];
    renderHelp();
    expect(screen.getByText(/Help was asked for less than 300 seconds ago/)).toBeTruthy();
    expect(screen.getByText(/Skipped:/)).toBeTruthy();
  });

  it("is not shown on this macro's step for another macro's step", () => {
    mocks.logState.stepSkips = [{ ...HELD_BACK, macroId: "macro_2" }];
    renderHelp();
    expect(screen.queryByText(/Skipped:/)).toBeNull();
  });

  it("is in the last run, which still completed", () => {
    mocks.logState.lastRun = {
      macroId: "macro_1", startedAt: 0, completedAt: Date.now(), duration: 20,
      status: "completed", stepErrors: [], conditionalResults: [], groupResults: [],
      stepSkips: [HELD_BACK],
    };
    renderHelp();
    expect(screen.getByText(/Last run: Completed/)).toBeTruthy();
    expect(screen.getByText(/Step 1/)).toBeTruthy();
    expect(screen.getByText(/Help was asked for less than 300 seconds ago/)).toBeTruthy();
  });
});

describe("a step's result inside a branch or a called macro", () => {
  const OUTER = {
    id: "macro_1",
    name: "System On",
    steps: [
      { action: "delay", seconds: 1 },
      { action: "conditional", condition: { key: "var.x", operator: "eq", value: 1 },
        then_steps: [{ action: "help.request" }], else_steps: [] },
    ],
  } as MacroConfig;
  const INNER = { id: "projector_on", name: "Projector On", steps: [] } as unknown as MacroConfig;
  const HELD_BACK = {
    macroId: "macro_1",
    stepPath: [1, "then", 0],
    action: "help.request",
    code: "help_cooldown",
    message:
      "Help was asked for less than 300 seconds ago, and this step sends at most once in that time, so no new request was sent.",
    at: Date.now(),
  };

  function renderOuter() {
    return render(
      <MacroEditor
        macro={OUTER}
        allMacros={[OUTER, INNER]}
        devices={[]}
        onUpdate={() => {}}
        onConvertToScript={() => {}}
      />,
    );
  }

  it("names the step that holds it in the last run", () => {
    mocks.logState.lastRun = {
      macroId: "macro_1", startedAt: 0, completedAt: Date.now(), duration: 20,
      status: "completed", stepErrors: [], conditionalResults: [], groupResults: [],
      stepSkips: [HELD_BACK],
    };
    renderOuter();
    expect(screen.getByText("Step 2:")).toBeTruthy();
    expect(screen.queryByText("Step 1:")).toBeNull();
  });

  it("names the macro a called macro's step belongs to", () => {
    mocks.logState.lastRun = {
      macroId: "macro_1", startedAt: 0, completedAt: Date.now(), duration: 20,
      status: "completed", stepErrors: [], conditionalResults: [], groupResults: [],
      stepSkips: [{ ...HELD_BACK, macroId: "projector_on", stepPath: [0] }],
    };
    renderOuter();
    expect(screen.getByText("Projector On, step 1:")).toBeTruthy();
  });
});

describe("a failure or a condition's answer inside a branch or a called macro", () => {
  const OUTER = {
    id: "macro_1",
    name: "System On",
    steps: [
      { action: "delay", seconds: 1 },
      { action: "conditional", condition: { key: "var.x", operator: "eq", value: 1 },
        then_steps: [{ action: "conditional", condition: { key: "var.y", operator: "eq", value: 1 },
          then_steps: [], else_steps: [] }], else_steps: [] },
    ],
  } as MacroConfig;
  const INNER = { id: "projector_on", name: "Projector On", steps: [] } as unknown as MacroConfig;
  const FAILED = {
    macroId: "projector_on", stepPath: [0], stepIndex: 0, action: "device.command",
    device: "", group: "", command: "", error: "x", message: "Projector is offline.", description: "",
  };

  function renderOuter() {
    return render(
      <MacroEditor
        macro={OUTER}
        allMacros={[OUTER, INNER]}
        devices={[]}
        onUpdate={() => {}}
        onConvertToScript={() => {}}
      />,
    );
  }

  afterEach(() => {
    mocks.logState.stepErrors = [];
    mocks.logState.conditionalResults = [];
  });

  it("does not put a called macro's failure on this macro's step of the same number", () => {
    mocks.logState.stepErrors = [FAILED];
    renderOuter();
    expect(screen.queryByText("Projector is offline.")).toBeNull();
  });

  it("names the called macro's step in the last run, and a branch's failure by the step that holds it", () => {
    mocks.logState.lastRun = {
      macroId: "macro_1", startedAt: 0, completedAt: Date.now(), duration: 20,
      status: "completed", conditionalResults: [], groupResults: [], stepSkips: [],
      stepErrors: [FAILED, { ...FAILED, macroId: "macro_1", stepPath: [1, "then", 0], message: "Inside the branch." }],
    };
    renderOuter();
    expect(screen.getByText("Projector On, step 1:")).toBeTruthy();
    expect(screen.getByText("Step 2:")).toBeTruthy();
  });

  it("gives a conditional only its own answer, not one from inside its branch", () => {
    // The outer conditional is step 1 and the inner the first of its branch:
    // by index alone, the inner one's answer is step 1's.
    const NESTED = {
      id: "macro_1",
      name: "System On",
      steps: [OUTER.steps[1]],
    } as MacroConfig;
    mocks.logState.conditionalResults = [{
      macroId: "macro_1", stepPath: [0, "then", 0], stepIndex: 0, conditionResult: false,
      branch: "else", conditionKey: "var.y", conditionOperator: "eq", actualValue: 0,
    }];
    render(
      <MacroEditor
        macro={NESTED}
        allMacros={[NESTED]}
        devices={[]}
        onUpdate={() => {}}
        onConvertToScript={() => {}}
      />,
    );
    expect(screen.queryByText("FALSE")).toBeNull();
  });
});
