import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";

// Why a trigger's fire or a macro's run did not go ahead, from the server's
// `trigger.skipped` and `macro.skipped`. Nothing in the Programmer read either,
// so a trigger that never ran looked the same as one nobody had set off.

const socket = vi.hoisted(() => ({
  handlers: [] as Array<(msg: Record<string, unknown>) => void>,
  disconnects: [] as Array<() => void>,
}));

vi.mock("../api/wsClient", () => ({
  connect: vi.fn(),
  disconnect: vi.fn(),
  send: vi.fn(),
  onMessage: vi.fn((h: (msg: Record<string, unknown>) => void) => {
    socket.handlers.push(h);
    return () => {};
  }),
  onConnect: vi.fn(() => () => {}),
  onDisconnect: vi.fn((h: () => void) => {
    socket.disconnects.push(h);
    return () => {};
  }),
}));

vi.mock("../api/serverReady", () => ({ waitForServerReady: vi.fn() }));
vi.mock("../api/startupStatus", () => ({
  askStartupStatus: vi.fn(async () => ({ answered: false, status: null })),
}));

import { useWebSocket } from "./useWebSocket";
import { useLogStore } from "../store/logStore";

function receive(msg: Record<string, unknown>) {
  act(() => socket.handlers.forEach((h) => h(msg)));
}

// The frames as the server pushes them: the bus payload under its type. On a
// trigger's `reason` is the code; on a macro's it is the sentence, so neither
// is what a reader keys on.
const TRIGGER_SKIPPED = {
  type: "trigger.skipped",
  trigger_id: "trg_dusk",
  reason: "cooldown",
  code: "cooldown",
  message: "The trigger's Cooldown had not passed since it last fired.",
};
const MACRO_SKIPPED = {
  type: "macro.skipped",
  macro_id: "lights_on",
  name: "Lights On",
  reason: "The macro was already running, and the macro's Overlap is set to Skip.",
  code: "macro_overlap",
  message: "The macro was already running, and the macro's Overlap is set to Skip.",
};

beforeEach(() => {
  socket.handlers.length = 0;
  socket.disconnects.length = 0;
  useLogStore.setState({
    triggerSkips: {},
    macroSkips: {},
    triggerPending: {},
    runningMacros: {},
    stepSkips: [],
    lastRun: null,
  });
});

describe("useWebSocket: a fire or a run that did not go ahead", () => {
  it("keeps the trigger's reason, by its code and its sentence", () => {
    renderHook(() => useWebSocket());
    receive(TRIGGER_SKIPPED);
    expect(useLogStore.getState().triggerSkips.trg_dusk).toMatchObject({
      code: "cooldown",
      message: "The trigger's Cooldown had not passed since it last fired.",
    });
  });

  it("ends the trigger's wait: a queued fire that was dropped is no longer queued", () => {
    renderHook(() => useWebSocket());
    receive({ type: "trigger.queued", trigger_id: "trg_dusk", queue_position: 1 });
    expect(useLogStore.getState().triggerPending.trg_dusk).toBeTruthy();
    receive({
      ...TRIGGER_SKIPPED,
      reason: "queue_timeout",
      code: "queue_timeout",
      message: "The macro was still running after 300 seconds, so the trigger's queued fire was dropped.",
    });
    expect(useLogStore.getState().triggerPending.trg_dusk).toBeUndefined();
  });

  it("drops the trigger's reason when it next fires", () => {
    renderHook(() => useWebSocket());
    receive(TRIGGER_SKIPPED);
    receive({ type: "trigger.fired", trigger_id: "trg_dusk", trigger_type: "schedule" });
    expect(useLogStore.getState().triggerSkips.trg_dusk).toBeUndefined();
  });

  it("keeps it across a Fire now, which the trigger's own record does not take", () => {
    renderHook(() => useWebSocket());
    receive(TRIGGER_SKIPPED);
    receive({ type: "trigger.fired", trigger_id: "trg_dusk", trigger_type: "test" });
    expect(useLogStore.getState().triggerSkips.trg_dusk?.code).toBe("cooldown");
  });

  it("keeps the macro's reason, by its code and its sentence", () => {
    renderHook(() => useWebSocket());
    receive(MACRO_SKIPPED);
    expect(useLogStore.getState().macroSkips.lights_on).toMatchObject({
      code: "macro_overlap",
      message: MACRO_SKIPPED.message,
    });
  });

  it("leaves the run it was refused beside alone", () => {
    // Overlap Skip: the refused start comes while the first run is going. It
    // is not a run, so it neither ends that one nor becomes the last run.
    renderHook(() => useWebSocket());
    receive({ type: "macro.started", macro_id: "lights_on", total_steps: 2 });
    receive(MACRO_SKIPPED);
    const store = useLogStore.getState();
    expect(store.runningMacros.lights_on).toBe(1);
    expect(store.lastRun).toBeNull();
  });

  it("drops the macro's reason when it next starts", () => {
    renderHook(() => useWebSocket());
    receive(MACRO_SKIPPED);
    receive({ type: "macro.started", macro_id: "lights_on", total_steps: 2 });
    expect(useLogStore.getState().macroSkips.lights_on).toBeUndefined();
  });

  it("forgets both on a dropped connection, which may have missed a fire", () => {
    renderHook(() => useWebSocket());
    receive(TRIGGER_SKIPPED);
    receive(MACRO_SKIPPED);
    act(() => socket.disconnects.forEach((h) => h()));
    expect(useLogStore.getState().triggerSkips).toEqual({});
    expect(useLogStore.getState().macroSkips).toEqual({});
  });
});

// A step its own setting held back (an Ask for Help inside its Send at most
// every): the run goes on, and the step says it sent nothing, on the run's
// progress, belonging to the step last named as running.
const HELP_HELD_BACK = {
  type: "macro.progress",
  macro_id: "call_help",
  action: "help.request",
  status: "skipped",
  code: "help_cooldown",
  message:
    "Help was asked for less than 300 seconds ago, and this step sends at most once in that time, so no new request was sent.",
};

describe("useWebSocket: a step that did not do its work", () => {
  it("is kept against the step that was running, and the run's last-run record keeps it", () => {
    renderHook(() => useWebSocket());
    receive({ type: "macro.started", macro_id: "call_help", total_steps: 2 });
    receive({
      type: "macro.progress", macro_id: "call_help", step_index: 1, total_steps: 2,
      step_path: [1], action: "help.request", status: "running", description: "Asking for help",
    });
    receive(HELP_HELD_BACK);
    const said = {
      macroId: "call_help", stepPath: [1], action: "help.request",
      code: "help_cooldown", message: HELP_HELD_BACK.message,
    };
    expect(useLogStore.getState().stepSkips).toMatchObject([said]);

    receive({ type: "macro.completed", macro_id: "call_help", outcome: "completed", failed_steps: 0 });
    const lastRun = useLogStore.getState().lastRun;
    expect(lastRun?.status).toBe("completed");
    expect(lastRun?.stepSkips).toMatchObject([said]);
  });

  it("inside a conditional's branch, belongs to the step's place in the tree", () => {
    renderHook(() => useWebSocket());
    receive({ type: "macro.started", macro_id: "call_help", total_steps: 3 });
    receive({
      type: "macro.progress", macro_id: "call_help", step_index: 2, total_steps: 3,
      step_path: [2], action: "conditional", status: "running", description: "If",
    });
    receive({
      type: "macro.progress", macro_id: "call_help", step_index: 0, total_steps: 1,
      step_path: [2, "then", 0], action: "help.request", status: "running", description: "Asking for help",
    });
    receive(HELP_HELD_BACK);
    expect(useLogStore.getState().stepSkips).toMatchObject([
      { macroId: "call_help", stepPath: [2, "then", 0] },
    ]);
  });

  it("is forgotten when the next run starts", () => {
    renderHook(() => useWebSocket());
    receive({ type: "macro.started", macro_id: "call_help", total_steps: 1 });
    receive(HELP_HELD_BACK);
    receive({ type: "macro.completed", macro_id: "call_help", outcome: "completed", failed_steps: 0 });
    receive({ type: "macro.started", macro_id: "call_help", total_steps: 1 });
    expect(useLogStore.getState().stepSkips).toEqual([]);
  });
});
