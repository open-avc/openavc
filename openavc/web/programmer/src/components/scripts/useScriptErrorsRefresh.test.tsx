import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";

// A script that fails while the Scripts view is open marks its row: the view
// re-asks the server's record when the server says a script failed, whatever
// the Log view is doing, and asks once for one failure however it heard of it.

const socket = vi.hoisted(() => ({
  handlers: [] as Array<(msg: Record<string, unknown>) => void>,
}));

vi.mock("../../api/wsClient", () => ({
  connect: vi.fn(),
  disconnect: vi.fn(),
  send: vi.fn(),
  onMessage: vi.fn((h: (msg: Record<string, unknown>) => void) => {
    socket.handlers.push(h);
    return () => {};
  }),
  onConnect: vi.fn(() => () => {}),
  onDisconnect: vi.fn(() => () => {}),
}));

vi.mock("../../api/serverReady", () => ({ waitForServerReady: vi.fn() }));
vi.mock("../../api/startupStatus", () => ({
  askStartupStatus: vi.fn(async () => ({ answered: false, status: null })),
}));

import { useWebSocket } from "../../hooks/useWebSocket";
import { useLogStore } from "../../store/logStore";
import { REASK_AFTER_MS, useScriptErrorsRefresh } from "./useScriptErrorsRefresh";

function receive(msg: Record<string, unknown>) {
  act(() => socket.handlers.forEach((h) => h(msg)));
}

function wait(ms: number) {
  act(() => { vi.advanceTimersByTime(ms); });
}

const failure = {
  type: "script.error",
  script_id: "room_logic",
  handler: "on_press",
  event: "ui.press.btn1",
  error: "'laptop'",
  traceback: "Traceback (most recent call last):\nKeyError: 'laptop'",
};

const failureLogged = {
  type: "log.entry",
  timestamp: 1700000000,
  level: "ERROR",
  source: "openavc.core.script_engine",
  category: "script",
  message: "Error in script 'room_logic' event handler for 'ui.press.btn1'",
};

function openScriptsView(refresh: () => void) {
  return renderHook(() => {
    useWebSocket();
    useScriptErrorsRefresh(refresh);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  socket.handlers.length = 0;
  useLogStore.setState({ logEntries: [], logPaused: false });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the Scripts view re-asks a script's failures", () => {
  it("when the server says a script failed, with the Log view paused", () => {
    // Pause keeps every incoming log line out of the log, and stays on after
    // the Log view is left, so the failure's log line never arrives here.
    useLogStore.setState({ logPaused: true });
    const refresh = vi.fn();
    openScriptsView(refresh);

    receive(failure);
    receive(failureLogged);
    wait(REASK_AFTER_MS);

    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("once for a failure it hears of twice, as a message and as its log line", () => {
    const refresh = vi.fn();
    openScriptsView(refresh);

    receive(failure);
    receive(failureLogged);
    wait(REASK_AFTER_MS);

    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("from a log line alone, for a failure the server sends no message for", () => {
    // An event dropped at the chain limit, or a script that failed to load at
    // another door: logged and recorded, with no script.error.
    const refresh = vi.fn();
    openScriptsView(refresh);

    receive({ ...failureLogged, message: "Script 'room_logic' on_press: Event 'custom.loop' dropped" });
    wait(REASK_AFTER_MS);

    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("at most once a window for a timer failing every tick, and after the last one", () => {
    const refresh = vi.fn();
    openScriptsView(refresh);

    const tick = REASK_AFTER_MS / 5;
    for (let i = 0; i < 20; i++) {
      receive({ ...failure, handler: "poll", event: "every() timer", timer_id: "timer_1" });
      wait(tick);
    }
    const duringTheRun = refresh.mock.calls.length;
    expect(duringTheRun).toBeGreaterThan(0);
    expect(duringTheRun).toBeLessThanOrEqual(Math.ceil((20 * tick) / REASK_AFTER_MS));

    // The last failure is read: one more ask lands after it.
    receive({ ...failure, handler: "poll", event: "every() timer", timer_id: "timer_1" });
    wait(REASK_AFTER_MS);
    expect(refresh.mock.calls.length).toBe(duringTheRun + 1);
  });

  it("nothing on opening for failures heard before the view opened", () => {
    // The view asks for the record itself when it opens.
    const before = vi.fn();
    const first = openScriptsView(before);
    receive(failure);
    receive(failureLogged);
    wait(REASK_AFTER_MS);
    first.unmount();
    socket.handlers.length = 0;

    const refresh = vi.fn();
    openScriptsView(refresh);
    wait(REASK_AFTER_MS);

    expect(refresh).not.toHaveBeenCalled();
  });

  it("nothing once the view has closed", () => {
    const refresh = vi.fn();
    const view = openScriptsView(refresh);

    receive(failure);
    view.unmount();
    wait(REASK_AFTER_MS);

    expect(refresh).not.toHaveBeenCalled();
  });
});
