import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Settings queued for a device that is not connected yet are saved into the
// project on the server, which owns the queue: the device page reads what is
// waiting from the server. The dialog brings a clean tab to the server's
// project and revision, or its next save is refused as another session's, and
// copies no queue into a tab with unsaved edits, which keeps them.

const mocks = vi.hoisted(() => ({
  setDeviceSetting: vi.fn(),
  storePendingSettings: vi.fn(),
  getProject: vi.fn(),
}));

vi.mock("../../api/restClient", () => ({
  setDeviceSetting: mocks.setDeviceSetting,
  storePendingSettings: mocks.storePendingSettings,
  getProject: mocks.getProject,
}));

import { ApiError } from "../../api/errors";
import type { DriverInfo, ProjectConfig } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { DeviceSettingsSetupDialog } from "./DeviceSettingsSetupDialog";

const DEVICE_ID = "acme_matrix_1";

const driverInfo = {
  id: "acme_matrix",
  name: "Acme Matrix",
  device_settings: {
    high_density: {
      type: "boolean",
      label: "High Density",
      help: "Packs more outputs onto each card.",
      setup: true,
      default: false,
    },
  },
} as unknown as DriverInfo;

function project(pending: Record<string, unknown>, macros: unknown[] = []): ProjectConfig {
  return {
    project: { id: "p", name: "P" },
    devices: [{ id: DEVICE_ID, driver: "acme_matrix", name: "Acme Matrix", config: {}, pending_settings: pending }],
    connections: {},
    macros,
  } as unknown as ProjectConfig;
}

function seedStore(over: Record<string, unknown> = {}) {
  useProjectStore.setState({
    project: project({}),
    dirty: false,
    saving: false,
    revision: "r1",
    etag: '"r1"',
    ...over,
  });
}

async function applyHighDensityYes() {
  const user = userEvent.setup();
  render(
    <DeviceSettingsSetupDialog
      deviceId={DEVICE_ID}
      driverInfo={driverInfo}
      existingDeviceIds={[DEVICE_ID]}
      onClose={vi.fn()}
    />,
  );
  await user.selectOptions(screen.getByRole("combobox"), "true");
  await user.click(screen.getByRole("button", { name: "Apply Settings" }));
  await waitFor(() => expect(mocks.storePendingSettings).toHaveBeenCalled());
}

beforeEach(() => {
  mocks.setDeviceSetting.mockReset().mockRejectedValue(
    new ApiError(503, JSON.stringify({ detail: "Device 'acme_matrix_1' is not connected" })),
  );
  mocks.storePendingSettings.mockReset().mockResolvedValue({
    status: "pending", device_id: DEVICE_ID, settings: { high_density: true },
  });
  mocks.getProject.mockReset().mockResolvedValue({
    ...project({ high_density: true }),
    _etag: '"r2"',
  });
});

describe("DeviceSettingsSetupDialog: settings queued for a device not connected yet", () => {
  it("queues them on the server and brings a clean tab to the server's revision", async () => {
    seedStore();
    await applyHighDensityYes();
    expect(mocks.storePendingSettings).toHaveBeenCalledWith(DEVICE_ID, { high_density: true });
    await waitFor(() => expect(useProjectStore.getState().revision).toBe("r2"));
    expect(useProjectStore.getState().dirty).toBe(false);
  });

  it("copies no queue into a tab with unsaved edits, which keeps them", async () => {
    const unsaved = { id: "m1", name: "New Macro", steps: [] };
    seedStore({ project: project({}, [unsaved]), dirty: true });
    await applyHighDensityYes();
    await screen.findByText(/Queued/);
    await waitFor(() => expect(mocks.getProject).toHaveBeenCalled());
    const device = useProjectStore.getState().project?.devices.find((d) => d.id === DEVICE_ID);
    expect(device?.pending_settings).toEqual({});
    expect(useProjectStore.getState().project?.macros).toEqual([unsaved]);
    expect(useProjectStore.getState().revision).toBe("r1");
  });
});
