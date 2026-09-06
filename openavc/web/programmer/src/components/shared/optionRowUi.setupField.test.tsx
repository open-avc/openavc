import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// SetupField offers a driver config field where the unusable row is, so the
// setting does not have to be hunted for. Two things went wrong with that:
//
// 1. The field's TYPE comes from the driver's config_schema, which arrives on
//    its own fetch. Until it lands every field looks like a string, so a port
//    typed and saved in that window was stored as "8554" rather than 8554.
// 2. It re-synced by calling projectStore.load(), which is a no-op while the
//    store is dirty. PUT /devices/{id} bumps the project revision, so a save
//    made from inside the UI Builder left the Builder holding a stale ETag and
//    409ing its own next autosave. syncDeviceConfig is the helper for exactly
//    that, and the other three inline editors already used it.

const mocks = vi.hoisted(() => ({
  state: { project: { devices: [] as Array<Record<string, unknown>> } },
  load: vi.fn(async () => true),
  syncDeviceConfig: vi.fn(async () => {}),
  updateDevice: vi.fn(async () => ({})),
  listDrivers: vi.fn(),
  showError: vi.fn(),
  showSuccess: vi.fn(),
}));

vi.mock("../../store/projectStore", () => ({
  useProjectStore: Object.assign(
    (selector: (s: unknown) => unknown) => selector(mocks.state),
    { getState: () => ({ ...mocks.state, load: mocks.load }) },
  ),
  syncDeviceConfig: mocks.syncDeviceConfig,
}));

vi.mock("../../store/toastStore", () => ({
  showError: (m: string) => mocks.showError(m),
  showSuccess: (m: string) => mocks.showSuccess(m),
}));

vi.mock("../../api/restClient", () => ({
  updateDevice: mocks.updateDevice,
  listDrivers: mocks.listDrivers,
}));

import { SetupField } from "./optionRowUi";

const DEVICE = "cam1";

/** One driver whose `port` field is declared an integer. */
const DRIVERS = [
  {
    id: "acme_widget",
    name: "Acme Widget",
    config_schema: {
      port: { label: "Stream port", type: "integer" },
    },
  },
];

/** Never resolves, so the component stays in its pre-schema window. */
function driversPending() {
  mocks.listDrivers.mockImplementation(() => new Promise(() => {}));
}

function driversResolved() {
  mocks.listDrivers.mockImplementation(async () => DRIVERS);
}

beforeEach(() => {
  mocks.state.project = {
    devices: [{ id: DEVICE, driver: "acme_widget", config: { host: "10.0.0.5" } }],
  };
  mocks.load.mockClear();
  mocks.syncDeviceConfig.mockClear();
  mocks.updateDevice.mockClear();
  mocks.showError.mockClear();
  mocks.showSuccess.mockClear();
  driversResolved();
});

describe("SetupField — saving before the driver schema arrives", () => {
  it("does not offer Save until the field's type is known", async () => {
    driversPending();
    render(<SetupField device={DEVICE} field="port" />);

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    // And the Enter key does not get in around the disabled button.
    await userEvent.type(screen.getByRole("textbox"), "8554{Enter}");
    expect(mocks.updateDevice).not.toHaveBeenCalled();
  });

  it("saves a declared integer field as a number once it can", async () => {
    render(<SetupField device={DEVICE} field="port" />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save" })).toBeEnabled(),
    );

    await userEvent.clear(screen.getByRole("spinbutton"));
    await userEvent.type(screen.getByRole("spinbutton"), "8554");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mocks.updateDevice).toHaveBeenCalled());
    const [, body] = mocks.updateDevice.mock.calls[0];
    expect(body.config.port).toBe(8554);
    // The rest of the driver's config survives: the endpoint replaces the whole
    // protocol config with what it is sent.
    expect(body.config.host).toBe("10.0.0.5");
  });
});

describe("SetupField — re-syncing the project after the save", () => {
  it("hands the saved config to syncDeviceConfig rather than reloading", async () => {
    render(<SetupField device={DEVICE} field="port" />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save" })).toBeEnabled(),
    );

    await userEvent.clear(screen.getByRole("spinbutton"));
    await userEvent.type(screen.getByRole("spinbutton"), "8554");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mocks.syncDeviceConfig).toHaveBeenCalled());
    // load() is the one that does nothing while the store is dirty, which is
    // how the Builder ended up with a stale ETag.
    expect(mocks.load).not.toHaveBeenCalled();
    const [deviceId, config] = mocks.syncDeviceConfig.mock.calls[0];
    expect(deviceId).toBe(DEVICE);
    expect(config.port).toBe(8554);
  });
});
