import { it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// A restart on a system with a Programmer password signs the tab out, and
// the wizard goes with the page. The tab keeps the open run, and after the
// next sign-in the wizard opens on it and says how it ended.

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

const { wsHandlers } = vi.hoisted(() => ({
  wsHandlers: [] as Array<(msg: Record<string, unknown>) => void>,
}));

vi.mock("../../api/wsClient", () => ({
  onMessage: (handler: (msg: Record<string, unknown>) => void) => {
    wsHandlers.push(handler);
    return () => wsHandlers.splice(wsHandlers.indexOf(handler), 1);
  },
  onConnect: () => () => {},
}));

import type { DeviceAction } from "../../api/types";
import { SetupActionWizard } from "./SetupActionWizard";

const action: DeviceAction = {
  id: "provision", kind: "setup", label: "Provision", availability: "offline", params: {},
};
const KEY = "openavc.setup.open_run";
const INTERRUPTED = "The setup was interrupted. Check the device and run it again.";

beforeEach(() => {
  sessionStorage.clear();
  wsHandlers.length = 0;
  invokeDeviceAction.mockReset().mockResolvedValue({ run_id: "r1", status: "started" });
  getSetupRun.mockReset();
  syncAfterSetupAction.mockReset().mockResolvedValue(undefined);
});

it("keeps the open run in the tab while it goes, and forgets it once it ends", async () => {
  render(<SetupActionWizard deviceId="dev1" action={action} onClose={() => {}} />);
  await userEvent.click(screen.getByRole("button", { name: "Start" }));
  await waitFor(() => expect(sessionStorage.getItem(KEY)).not.toBeNull());
  expect(JSON.parse(sessionStorage.getItem(KEY)!)).toEqual({
    deviceId: "dev1", actionId: "provision", runId: "r1",
  });

  await act(async () => {
    for (const h of [...wsHandlers]) {
      h({ type: "action.progress", device_id: "dev1", action_id: "provision", run_id: "r1",
        step: "Done", pct: 100, status: "done" });
    }
  });
  expect(sessionStorage.getItem(KEY)).toBeNull();
});

it("opened on a run, asks the server at once and says how it ended", async () => {
  getSetupRun.mockResolvedValue({ status: "error", step: INTERRUPTED, error: INTERRUPTED });
  render(<SetupActionWizard deviceId="dev1" action={action} runId="r1" onClose={() => {}} />);

  expect(await screen.findByText(INTERRUPTED)).toBeTruthy();
  expect(getSetupRun).toHaveBeenCalledWith("dev1", "r1");
  expect(invokeDeviceAction).not.toHaveBeenCalled();
});
