import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// A setup wizard writes the device's settings on the server from a run this
// tab started. The project.reloaded that follows names this tab, so the
// WebSocket handler leaves the refetch to the wizard. If the wizard does not
// re-sync, the tab keeps the version from before the run and its next save is
// refused as another session's.

const { invokeDeviceAction, getProject, saveProject } = vi.hoisted(() => ({
  invokeDeviceAction: vi.fn(),
  getProject: vi.fn(),
  saveProject: vi.fn(),
}));

vi.mock("../../api/restClient", () => ({
  invokeDeviceAction,
  getProject,
  saveProject,
  ConflictError: class ConflictError extends Error {},
}));

const { wsHandlers } = vi.hoisted(() => ({
  wsHandlers: [] as Array<(msg: Record<string, unknown>) => void>,
}));

vi.mock("../../api/wsClient", () => ({
  onMessage: (handler: (msg: Record<string, unknown>) => void) => {
    wsHandlers.push(handler);
    return () => {
      const i = wsHandlers.indexOf(handler);
      if (i >= 0) wsHandlers.splice(i, 1);
    };
  },
}));

import type { DeviceAction, ProjectConfig } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { SetupActionWizard } from "./SetupActionWizard";

const action: DeviceAction = {
  id: "enable_remote",
  kind: "setup",
  label: "Enable Remote Access",
  availability: "offline",
  params: {},
};

function project(port: number, extra: Partial<ProjectConfig> = {}): ProjectConfig {
  return {
    openavc_version: "0.13.0",
    project: { id: "p", name: "P" },
    devices: [
      { id: "dev1", driver: "acme_widget", name: "Widget", config: { port } },
      { id: "dev2", driver: "acme_widget", name: "Other", config: {} },
    ],
    connections: { dev1: { host: "192.0.2.10", port } },
    ...extra,
  } as unknown as ProjectConfig;
}

function progress(status: string, step = "") {
  for (const h of [...wsHandlers]) {
    h({
      type: "action.progress",
      device_id: "dev1",
      action_id: action.id,
      run_id: "r1",
      step,
      pct: null,
      status,
      ...(status === "error" ? { error: step } : {}),
    });
  }
}

async function runWizard() {
  render(<SetupActionWizard deviceId="dev1" action={action} onClose={() => {}} />);
  await userEvent.click(screen.getByRole("button", { name: "Start" }));
  await waitFor(() => expect(invokeDeviceAction).toHaveBeenCalled());
}

beforeEach(() => {
  wsHandlers.length = 0;
  invokeDeviceAction.mockReset().mockResolvedValue({ run_id: "r1", status: "started" });
  getProject.mockReset().mockImplementation(async () => ({
    ...project(22),
    _etag: '"after"',
  }));
  saveProject.mockReset().mockResolvedValue({ etag: '"next"' });
  useProjectStore.setState({
    project: project(23),
    etag: '"before"',
    revision: "before",
    dirty: false,
    saving: false,
    error: null,
    conflictDetected: false,
  });
});

describe("a tab that ran a setup wizard", () => {
  it("takes the server's version when the run finishes, so its next save goes through", async () => {
    await runWizard();
    act(() => progress("done", "Done"));

    await waitFor(() => expect(useProjectStore.getState().etag).toBe('"after"'));
    expect(useProjectStore.getState().project?.connections.dev1.port).toBe(22);

    // The next edit saves against the version the server now holds.
    useProjectStore.getState().update({ variables: [] });
    await useProjectStore.getState().save();
    expect(saveProject).toHaveBeenCalledTimes(1);
    expect(saveProject.mock.calls[0][1]).toBe('"after"');
  });

  it("re-syncs after a failed run too, which may have saved settings first", async () => {
    await runWizard();
    act(() => progress("error", "Lost the connection"));

    await waitFor(() => expect(useProjectStore.getState().etag).toBe('"after"'));
  });

  it("keeps unsaved edits and shows the device's saved settings beside them", async () => {
    useProjectStore.setState({
      project: project(23, { variables: [{ id: "unsaved" }] as never }),
      dirty: true,
    });
    await runWizard();
    act(() => progress("done", "Done"));

    await waitFor(() =>
      expect(useProjectStore.getState().project?.connections.dev1.port).toBe(22),
    );
    const state = useProjectStore.getState();
    expect(state.project?.devices[0].config).toEqual({ port: 22 });
    expect(state.project?.variables).toEqual([{ id: "unsaved" }]);
    expect(state.project?.devices[1].name).toBe("Other");
    // Its version is left alone: the server's conflict check still stands
    // between the unsaved edits and what the server holds.
    expect(state.etag).toBe('"before"');
    expect(state.dirty).toBe(true);
  });
});
