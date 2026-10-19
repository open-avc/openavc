import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// A setup wizard writes the device's settings on the server from a run this
// tab started. The project.reloaded that follows names this tab, so the
// WebSocket handler leaves the refetch to the wizard. If the wizard does not
// re-sync, the tab keeps the version from before the run and its next save is
// refused as another session's.

const { getProject, saveProject } = vi.hoisted(() => ({
  getProject: vi.fn(),
  saveProject: vi.fn(),
}));

vi.mock("../../api/restClient", () => ({
  getProject,
  saveProject,
  ConflictError: class ConflictError extends Error {},
}));

const { prepareSetup, startSetup, setupStatus, subscribeSetup, unsubscribeSetup, retrieveSetup, acknowledgeSetup, discardSetupTransfer } = vi.hoisted(() => ({
  prepareSetup: vi.fn(), startSetup: vi.fn(), setupStatus: vi.fn(), subscribeSetup: vi.fn(),
  unsubscribeSetup: vi.fn(), retrieveSetup: vi.fn(), acknowledgeSetup: vi.fn(), discardSetupTransfer: vi.fn(),
}));
vi.mock("../../api/setupClient", () => ({ prepareSetup, startSetup, setupStatus, subscribeSetup, unsubscribeSetup, retrieveSetup, acknowledgeSetup, discardSetupTransfer }));

const { wsHandlers, disconnectHandlers } = vi.hoisted(() => ({
  disconnectHandlers: [] as Array<() => void>,
  wsHandlers: [] as Array<(msg: Record<string, unknown>) => void>,
}));

vi.mock("../../api/wsClient", () => ({
  getConnectionId: () => "connection-1",
  onDisconnect: (handler: () => void) => {
    disconnectHandlers.push(handler);
    return () => { const i = disconnectHandlers.indexOf(handler); if (i >= 0) disconnectHandlers.splice(i, 1); };
  },
  onMessage: (handler: (msg: Record<string, unknown>) => void) => {
    wsHandlers.push(handler);
    return () => {
      const i = wsHandlers.indexOf(handler);
      if (i >= 0) wsHandlers.splice(i, 1);
    };
  },
}));

import type { SetupStatus } from "../../api/setupClient";
import { ApiError } from "../../api/errors";
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

function snapshot(status: SetupStatus["status"] = "running", extra: Partial<SetupStatus> = {}): SetupStatus {
  return { run_id: "r1", status, progress: null, result: null, secret: { state: "none", generation: 0 }, ...extra };
}
function completed(outcome: "succeeded" | "failed" = "succeeded") {
  return snapshot("terminal", { result: { outcome, summary: "The device answered.", findings: [], config_save: "reported_complete", reconnect: "reported_complete" } });
}
function emit(msg: Record<string, unknown>) { for (const h of [...wsHandlers]) h(msg); }
function progress(status: string) {
  emit({ type: "action.progress", device_id: "dev1", action_id: action.id, ...completed(status === "error" ? "failed" : "succeeded") });
}

async function runWizard() {
  render(<SetupActionWizard deviceId="dev1" action={action} onClose={() => {}} />);
  await userEvent.click(screen.getByRole("button", { name: "Start" }));
  await waitFor(() => expect(startSetup).toHaveBeenCalled());
}

beforeEach(() => {
  wsHandlers.length = 0;
  disconnectHandlers.length = 0;
  prepareSetup.mockReset().mockResolvedValue({ run_id: "r1", status: "prepared", capability: "private-capability" });
  startSetup.mockReset().mockResolvedValue(snapshot());
  setupStatus.mockReset().mockResolvedValue(snapshot());
  subscribeSetup.mockReset().mockResolvedValue(snapshot("prepared"));
  unsubscribeSetup.mockReset().mockResolvedValue({ unsubscribed: true });
  retrieveSetup.mockReset().mockResolvedValue({ generation: 1, values: { app_key: "invented-secret" } });
  acknowledgeSetup.mockReset().mockResolvedValue({ acknowledged: 1 });
  discardSetupTransfer.mockReset().mockResolvedValue({ discarded: "r1" });
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
    act(() => progress("done"));

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
    act(() => progress("error"));

    await waitFor(() => expect(useProjectStore.getState().etag).toBe('"after"'));
  });

  it("keeps unsaved edits and shows the device's saved settings beside them", async () => {
    useProjectStore.setState({
      project: project(23, { variables: [{ id: "unsaved" }] as never }),
      dirty: true,
    });
    await runWizard();
    act(() => progress("done"));

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

describe("prepared setup attempt ownership", () => {
  it("shows reported values without turning zero or false into missing results", async () => {
    const status = completed();
    status.result!.outcome = "unverified";
    status.result!.findings = [{ code: "check.summary", outcome: "unverified", detail: "Limited device report", fields: [
      { label: "Checks", value: 0 }, { label: "Authenticated", value: false },
      { label: "Firmware", value: null }, { label: "Device name", value: "<script>marker</script>" },
    ] }];
    startSetup.mockResolvedValueOnce(status);
    await runWizard();
    expect(await screen.findByText("Outcome not confirmed")).toBeInTheDocument();
    for (const text of ["0", "No", "Not reported", "<script>marker</script>"])
      expect(screen.getByText(text)).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
  });

  it("keeps one reservation through double clicks and a lost start response", async () => {
    let prepared!: (value: unknown) => void;
    prepareSetup.mockImplementationOnce(() => new Promise((resolve) => { prepared = resolve; }));
    startSetup.mockRejectedValueOnce(new TypeError("Response lost"));
    setupStatus.mockResolvedValueOnce(completed());
    render(<SetupActionWizard deviceId="dev1" action={action} onClose={() => {}} />);
    const start = screen.getByRole("button", { name: "Start" });
    act(() => { start.click(); start.click(); });
    expect(prepareSetup).toHaveBeenCalledTimes(1);
    await act(async () => prepared({ run_id: "r1", status: "prepared", capability: "private-capability" }));
    await screen.findByText("Completed");
    expect(startSetup).toHaveBeenCalledTimes(1);
    expect(startSetup).toHaveBeenCalledWith("dev1", action.id, "private-capability", {});
    expect(setupStatus).toHaveBeenCalledWith("private-capability");
    expect(prepareSetup).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/private-capability/)).toBeNull();
  });

  it("keeps terminal status ahead of a delayed start reply and ignores another run", async () => {
    let started!: (value: unknown) => void;
    startSetup.mockImplementationOnce(() => new Promise((resolve) => { started = resolve; }));
    await runWizard();
    act(() => emit({ type: "action.progress", device_id: "dev1", action_id: action.id,
      ...completed(), run_id: "another-run" }));
    expect(screen.queryByText("Completed")).toBeNull();
    act(() => progress("done"));
    await screen.findByText("Completed");
    await act(async () => started(snapshot("claimed")));
    expect(screen.getByText("Completed")).toBeTruthy();
    expect(getProject).toHaveBeenCalledTimes(1);
  });

  it("uses run and sequence while 100 percent remains an active run", async () => {
    await runWizard();
    const message = (sequence: number, percent: number, detail: string) => ({ type: "action.progress",
      device_id: "dev1", action_id: action.id, ...snapshot("running", {
        progress: { code: "pair.progress", sequence, percent, detail },
      }) });
    act(() => { emit(message(2, 100, "Paired")); emit(message(3, 90, "Saving")); emit(message(1, 20, "Stale")); });
    expect(screen.getByText("100%")).toBeTruthy();
    expect(screen.getByText("90%")).toBeTruthy();
    expect(screen.queryByText("Stale")).toBeNull();
    expect(screen.queryByText("Completed")).toBeNull();
    expect(getProject).not.toHaveBeenCalled();
  });

  it("subscribes the new socket and recovers a terminal result without another start", async () => {
    await runWizard();
    subscribeSetup.mockResolvedValueOnce(completed("failed"));
    act(() => { for (const h of disconnectHandlers) h(); emit({ type: "connection.ready", connection_id: "connection-2" }); });
    await screen.findByText("Failed");
    expect(subscribeSetup).toHaveBeenLastCalledWith("private-capability", "connection-2");
    expect(startSetup).toHaveBeenCalledTimes(1);
    expect(prepareSetup).toHaveBeenCalledTimes(1);
    expect(getProject).toHaveBeenCalledTimes(1);
  });

  it("retains an unsaved key until explicit reveal and acknowledgement, with a close warning", async () => {
    startSetup.mockResolvedValueOnce({ ...completed("failed"), secret: { state: "available", generation: 1 } });
    const onClose = vi.fn();
    render(<SetupActionWizard deviceId="dev1" action={action} onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    await screen.findByRole("button", { name: "Reveal credentials" });
    expect(retrieveSetup).not.toHaveBeenCalled();
    expect(screen.queryByDisplayValue("invented-secret")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Keep open" }));
    await userEvent.click(screen.getByRole("button", { name: "Reveal credentials" }));
    expect(await screen.findByDisplayValue("invented-secret")).toBeTruthy();
    expect(acknowledgeSetup).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "I've saved these credentials" }));
    await waitFor(() => expect(screen.queryByDisplayValue("invented-secret")).toBeNull());
    expect(acknowledgeSetup).toHaveBeenCalledWith("private-capability", 1);
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(unsubscribeSetup).toHaveBeenCalledWith("private-capability", "connection-1");
  });

  it("never revives a denied result from a delayed progress frame", async () => {
    startSetup.mockResolvedValueOnce({ ...completed(), secret: { state: "available", generation: 1 } });
    await runWizard();
    setupStatus.mockRejectedValueOnce(new ApiError(410, '{"detail":"unavailable"}'));
    await userEvent.click(screen.getByRole("button", { name: "Check status" }));
    await screen.findByRole("alert");
    act(() => emit({ type: "action.progress", device_id: "dev1", action_id: action.id,
      ...completed(), secret: { state: "available", generation: 1 } }));
    expect(screen.queryByRole("button", { name: "Reveal credentials" })).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("unavailable");
  });

  it("ignores a secret response superseded by a saved generation", async () => {
    startSetup.mockResolvedValueOnce({ ...completed(), secret: { state: "available", generation: 1 } });
    let retrieved!: (value: unknown) => void;
    retrieveSetup.mockImplementationOnce(() => new Promise((resolve) => { retrieved = resolve; }));
    await runWizard();
    await userEvent.click(screen.getByRole("button", { name: "Reveal credentials" }));
    act(() => emit({ type: "action.progress", device_id: "dev1", action_id: action.id,
      ...completed(), secret: { state: "reported_saved", generation: 1 } }));
    await act(async () => retrieved({ generation: 1, values: { app_key: "invented-secret" } }));
    expect(screen.queryByDisplayValue("invented-secret")).toBeNull();
    expect(screen.queryByRole("button", { name: "Reveal credentials" })).toBeNull();
  });

  it.each(["replacement", "saved", "revoked"])("reconciles a refused secret read against the same attempt: %s", async (change) => {
    startSetup.mockResolvedValueOnce({ ...completed(), secret: { state: "available", generation: 1 } });
    let refused!: (error: unknown) => void;
    retrieveSetup.mockImplementationOnce(() => new Promise((_resolve, reject) => { refused = reject; }));
    await runWizard();
    await userEvent.click(screen.getByRole("button", { name: "Reveal credentials" }));
    if (change === "revoked") setupStatus.mockRejectedValueOnce(new ApiError(410, "unavailable"));
    else setupStatus.mockResolvedValueOnce({ ...completed(), secret: {
      state: change === "replacement" ? "available" : "reported_saved",
      generation: change === "replacement" ? 2 : 1,
    } });
    await act(async () => refused(new ApiError(410, "unavailable")));
    expect(setupStatus).toHaveBeenCalledWith("private-capability");
    expect(screen.queryByDisplayValue("invented-secret")).toBeNull();
    if (change === "replacement") {
      retrieveSetup.mockResolvedValueOnce({ generation: 2, values: { app_key: "replacement-secret" } });
      await userEvent.click(await screen.findByRole("button", { name: "Reveal credentials" }));
      expect(await screen.findByDisplayValue("replacement-secret")).toBeTruthy();
      await userEvent.click(screen.getByRole("button", { name: "I've saved these credentials" }));
      expect(acknowledgeSetup).toHaveBeenCalledWith("private-capability", 2);
    } else {
      expect(screen.queryByRole("button", { name: "Reveal credentials" })).toBeNull();
      expect(!!screen.queryByRole("button", { name: "Check status" })).toBe(change === "saved");
    }
    expect(prepareSetup).toHaveBeenCalledTimes(1);
    expect(startSetup).toHaveBeenCalledTimes(1);
  });
});

describe("retained inputs after an older page refresh", () => {
  const resumedAction: DeviceAction = { ...action, params: {
    note: { type: "string", label: "Note", required: true },
    password: { type: "password", label: "Device password", required: true },
    save: { type: "boolean", label: "Save settings" },
  } };
  const retained = () => ({ run_id: "r1", status: "prepared" as const,
    capability: "private-capability", device_id: "dev1", action: resumedAction,
    params: { note: "retained-note", password: "invented-input", save: false } });

  it("shows retained inputs without starting and uses the same reservation on Start", async () => {
    render(<SetupActionWizard deviceId="dev1" action={resumedAction} retained={retained()} onClose={() => {}} />);
    expect(screen.getByDisplayValue("retained-note")).toBeInTheDocument();
    expect(screen.getByDisplayValue("invented-input")).toHaveAttribute("type", "password");
    expect(screen.getByRole("combobox")).toHaveValue("false");
    expect(prepareSetup).not.toHaveBeenCalled();
    expect(startSetup).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Start", exact: true }));
    await waitFor(() => expect(startSetup).toHaveBeenCalledWith("dev1", action.id,
      "private-capability", retained().params));
    expect(prepareSetup).not.toHaveBeenCalled();
    expect(discardSetupTransfer).not.toHaveBeenCalled();
  });

  it("discards an unstarted reservation before preparing edited inputs", async () => {
    let discard!: () => void;
    discardSetupTransfer.mockImplementationOnce(() => new Promise<void>((resolve) => { discard = resolve; }));
    render(<SetupActionWizard deviceId="dev1" action={resumedAction} retained={retained()} onClose={() => {}} />);
    await userEvent.type(screen.getByDisplayValue("retained-note"), " changed");
    await userEvent.click(screen.getByRole("button", { name: "Start", exact: true }));
    expect(discardSetupTransfer).toHaveBeenCalledWith("r1");
    expect(prepareSetup).not.toHaveBeenCalled();
    expect(startSetup).not.toHaveBeenCalled();
    await act(async () => discard());
    await waitFor(() => expect(startSetup).toHaveBeenCalled());
    expect(prepareSetup).toHaveBeenCalledWith("dev1", action.id, { ...retained().params, note: "retained-note changed" });
  });

  it("never prepares a replacement when another tab consumed the retained attempt", async () => {
    discardSetupTransfer.mockRejectedValueOnce(new ApiError(410, "unavailable"));
    render(<SetupActionWizard deviceId="dev1" action={resumedAction} retained={retained()} onClose={() => {}} />);
    await userEvent.type(screen.getByDisplayValue("retained-note"), " changed");
    await userEvent.click(screen.getByRole("button", { name: "Start", exact: true }));
    await screen.findByRole("alert");
    expect(prepareSetup).not.toHaveBeenCalled();
    expect(startSetup).not.toHaveBeenCalled();
    expect(screen.getByDisplayValue("retained-note changed")).toBeInTheDocument();
  });

  it.each([false, true])("explicit close forgets the draft even if it expired: %s", async (expired) => {
    if (expired) discardSetupTransfer.mockRejectedValueOnce(new ApiError(410, "unavailable"));
    const close = vi.fn();
    render(<SetupActionWizard deviceId="dev1" action={resumedAction} retained={retained()} onClose={close} />);
    await userEvent.click(screen.getByRole("button", { name: "Cancel", exact: true }));
    expect(close).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Close and discard access" }));
    await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
    expect(discardSetupTransfer).toHaveBeenCalledWith("r1");
    expect(startSetup).not.toHaveBeenCalled();
  });
});
