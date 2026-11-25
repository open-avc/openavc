import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";

// The messages that only say something changed on the server move the counter
// the screens that show it follow: a file in the project's ui/ folder redraws
// the custom controls, a saved theme re-reads the Builder's themes, a script's
// failure re-asks the Scripts view's record.

const socket = vi.hoisted(() => ({
  handlers: [] as Array<(msg: Record<string, unknown>) => void>,
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
  onDisconnect: vi.fn(() => () => {}),
}));

vi.mock("../api/serverReady", () => ({ waitForServerReady: vi.fn() }));
vi.mock("../api/startupStatus", () => ({
  askStartupStatus: vi.fn(async () => ({ answered: false, status: null })),
}));

import { useWebSocket } from "./useWebSocket";
import { useUiFilesStore } from "../store/uiFilesStore";
import { useThemeFilesStore } from "../store/themeFilesStore";
import { useScriptErrorsStore } from "../store/scriptErrorsStore";

function receive(msg: Record<string, unknown>) {
  act(() => socket.handlers.forEach((h) => h(msg)));
}

beforeEach(() => {
  socket.handlers.length = 0;
  useUiFilesStore.setState({ version: 0 });
  useThemeFilesStore.setState({ version: 0 });
  useScriptErrorsStore.setState({ heard: 0 });
});

describe("useWebSocket — what changed on the server", () => {
  it("a file in the ui/ folder moves the custom controls' counter alone", () => {
    renderHook(() => useWebSocket());
    receive({ type: "ui.files" });
    expect(useUiFilesStore.getState().version).toBe(1);
    expect(useThemeFilesStore.getState().version).toBe(0);
    expect(useScriptErrorsStore.getState().heard).toBe(0);
  });

  it("a saved theme changing moves the themes' counter alone", () => {
    renderHook(() => useWebSocket());
    receive({ type: "themes.changed", theme_id: "hall-look" });
    receive({ type: "themes.changed" });
    expect(useThemeFilesStore.getState().version).toBe(2);
    expect(useUiFilesStore.getState().version).toBe(0);
    expect(useScriptErrorsStore.getState().heard).toBe(0);
  });

  it("a script's failure moves the Scripts view's counter alone", () => {
    renderHook(() => useWebSocket());
    receive({ type: "script.error", script_id: "room_logic", handler: "on_press" });
    receive({ type: "script.error", script_id: "room_logic", handler: "on_press" });
    expect(useScriptErrorsStore.getState().heard).toBe(2);
    expect(useUiFilesStore.getState().version).toBe(0);
  });

  it("a message of another type moves none of them", () => {
    renderHook(() => useWebSocket());
    receive({ type: "constructor" });
    receive({ type: "trigger.fired", trigger_id: "t1" });
    expect(useUiFilesStore.getState().version).toBe(0);
    expect(useThemeFilesStore.getState().version).toBe(0);
    expect(useScriptErrorsStore.getState().heard).toBe(0);
  });
});
