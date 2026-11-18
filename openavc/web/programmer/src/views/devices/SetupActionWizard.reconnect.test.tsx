import { it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// A setup run's final event goes out over the WebSocket. When the socket is
// down at that moment (a dropped network, a server restart), the wizard asks
// the server how its run stands each time the socket comes back, rather than
// showing "Running…" for good.

const { invokeDeviceAction, getSetupRun, syncAfterSetupAction } = vi.hoisted(() => ({
  invokeDeviceAction: vi.fn(),
  getSetupRun: vi.fn(),
  syncAfterSetupAction: vi.fn(),
}));

vi.mock("../../api/restClient", () => ({ invokeDeviceAction, getSetupRun }));
vi.mock("../../store/projectStore", () => ({ syncAfterSetupAction }));
vi.mock("../../api/commandFileClient", () => ({
  uploadCommandFiles: vi.fn().mockResolvedValue({}),
  discardUpload: vi.fn(),
  setupUploadOpener: () => "setup-opener",
}));

const { wsHandlers, connectHandlers } = vi.hoisted(() => ({
  wsHandlers: [] as Array<(msg: Record<string, unknown>) => void>,
  connectHandlers: [] as Array<() => void>,
}));

function subscribe<T>(list: T[], handler: T): () => void {
  list.push(handler);
  return () => {
    const i = list.indexOf(handler);
    if (i >= 0) list.splice(i, 1);
  };
}

vi.mock("../../api/wsClient", () => ({
  onMessage: (handler: (msg: Record<string, unknown>) => void) => subscribe(wsHandlers, handler),
  onConnect: (handler: () => void) => subscribe(connectHandlers, handler),
}));

import type { DeviceAction } from "../../api/types";
import { SetupActionWizard } from "./SetupActionWizard";

const action: DeviceAction = {
  id: "provision",
  kind: "setup",
  label: "Provision",
  availability: "offline",
  params: {},
};

const INTERRUPTED = "The setup was interrupted. Check the device and run it again.";

async function startRun() {
  render(<SetupActionWizard deviceId="dev1" action={action} onClose={() => {}} />);
  await userEvent.click(screen.getByRole("button", { name: "Start" }));
  await waitFor(() => expect(invokeDeviceAction).toHaveBeenCalled());
  await act(async () => {});
}

function reconnect() {
  for (const h of [...connectHandlers]) h();
}

function progress(status: string, step: string, runId = "r1") {
  for (const h of [...wsHandlers]) {
    h({
      type: "action.progress", device_id: "dev1", action_id: action.id, run_id: runId,
      step, pct: null, status, ...(status === "error" ? { error: step } : {}),
    });
  }
}

beforeEach(() => {
  wsHandlers.length = 0;
  connectHandlers.length = 0;
  invokeDeviceAction.mockReset().mockResolvedValue({ run_id: "r1", status: "started" });
  getSetupRun.mockReset();
  syncAfterSetupAction.mockReset().mockResolvedValue(undefined);
});

it("shows a run that finished while the connection was down as done", async () => {
  getSetupRun.mockResolvedValue({
    device_id: "dev1", run_id: "r1", step: "Done", pct: 100, status: "done",
    result: { message: "Found 3 endpoints." },
  });
  await startRun();

  act(() => reconnect());

  expect(await screen.findByText("Found 3 endpoints.")).toBeInTheDocument();
  expect(getSetupRun).toHaveBeenCalledWith("dev1", "r1");
  expect(screen.getByRole("button", { name: "Close" })).toBeEnabled();
  expect(syncAfterSetupAction).toHaveBeenCalledWith("dev1");
});

it("says a run cut short by a restart was interrupted", async () => {
  getSetupRun.mockResolvedValue({
    device_id: "dev1", run_id: "r1", step: INTERRUPTED, pct: null, status: "error",
    error: INTERRUPTED,
  });
  await startRun();

  act(() => reconnect());

  expect(await screen.findByText(INTERRUPTED)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Close" })).toBeEnabled();
});

it("keeps following a run the server says is still going", async () => {
  getSetupRun.mockResolvedValue({
    device_id: "dev1", run_id: "r1", step: "Waiting", pct: 10, status: "running",
  });
  await startRun();

  act(() => reconnect());
  await waitFor(() => expect(getSetupRun).toHaveBeenCalled());
  expect(screen.getByRole("button", { name: "Running…" })).toBeDisabled();

  act(() => progress("done", "Done"));
  expect(await screen.findByRole("button", { name: "Close" })).toBeEnabled();
});

it("shows the server's sentence when it no longer keeps the run", async () => {
  getSetupRun.mockRejectedValue(
    new Error("That setup run has ended and is no longer kept. Check the device."),
  );
  await startRun();

  act(() => reconnect());

  expect(
    await screen.findByText("That setup run has ended and is no longer kept. Check the device."),
  ).toBeInTheDocument();
});

it("asks nothing once the run has ended", async () => {
  await startRun();
  act(() => progress("done", "Done"));
  await screen.findByRole("button", { name: "Close" });

  act(() => reconnect());

  expect(getSetupRun).not.toHaveBeenCalled();
  expect(connectHandlers).toHaveLength(0);
});

it("ignores another run's events once it knows its own run", async () => {
  await startRun();

  act(() => progress("done", "Done", "someone-elses-run"));

  expect(screen.getByRole("button", { name: "Running…" })).toBeDisabled();
  act(() => progress("done", "Done"));
  expect(await screen.findByRole("button", { name: "Close" })).toBeEnabled();
});
