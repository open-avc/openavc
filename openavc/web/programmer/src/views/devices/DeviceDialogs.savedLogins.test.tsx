import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// A login saved on this system reaches the Programmer only as the project's
// marker. The device dialogs show it as "Saved on this system" with Change,
// never as a value in a box: a box holding the marker read as a password 21
// characters long, and text typed after it was saved as part of the password.
// Change opens an empty box; what is typed there replaces the saved login, and
// a box left empty keeps it. Another driver, or a copy of the device, has no
// saved login of its own and asks for one.

const mocks = vi.hoisted(() => ({
  syncDeviceFromServer: vi.fn(async () => {}),
}));

vi.mock("../../api/restClient", () => ({
  listDrivers: vi.fn(),
  getDevice: vi.fn(),
  updateDevice: vi.fn(() => Promise.resolve({ status: "updated", device_id: "x" })),
}));
vi.mock("../../api/systemClient", () => ({
  getSerialPorts: vi.fn(() => Promise.resolve({ ports: [] })),
}));
vi.mock("../../store/projectStore", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../store/projectStore")>()),
  syncDeviceFromServer: mocks.syncDeviceFromServer,
}));

import * as api from "../../api/restClient";
import type { DeviceConfig, DriverInfo } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { AddDeviceDialog, carryOnDriverChange, EditDeviceDialog } from "./DeviceDialogs";

const MARKER = "<saved on this system>";

const driver = (over: Record<string, unknown>) =>
  ({ manufacturer: "Acme", category: "projector", commands: {}, ...over }) as unknown as DriverInfo;

const widget = driver({
  id: "acme_widget",
  name: "Acme Widget",
  transport: "tcp",
  default_config: { port: 4352 },
  config_schema: {
    host: { type: "string", required: true, label: "IP Address" },
    port: { type: "integer", label: "Port" },
    password: { type: "string", secret: true, label: "Password" },
    api_key: { type: "string", secret: true, label: "API Key" },
    mode: { type: "string", label: "Mode" },
  },
});
const gadget = driver({
  id: "acme_gadget",
  name: "Acme Gadget",
  transport: "tcp",
  default_config: { port: 23 },
  config_schema: {
    host: { type: "string", required: true, label: "IP Address" },
    port: { type: "integer", label: "Port" },
    password: { type: "string", secret: true, label: "Password" },
  },
});

const device = {
  id: "proj1",
  driver: "acme_widget",
  name: "Hall Projector",
  config: { api_key: MARKER, mode: "eco" },
} as DeviceConfig;
const connection = { host: "192.0.2.10", port: 4352, password: MARKER };

function seed(devices: DeviceConfig[], connections: Record<string, Record<string, unknown>>) {
  useProjectStore.setState({
    project: { devices, connections } as never,
    load: vi.fn(() => Promise.resolve(true)),
    update: vi.fn(),
    save: vi.fn(() => Promise.resolve()),
  });
}

function openEdit(d: DeviceConfig = device, c: Record<string, unknown> = connection) {
  seed([d], { [d.id]: c });
  render(<EditDeviceDialog device={d} onClose={() => {}} onSaved={() => {}} />);
}

async function pickDriver(name: string) {
  await userEvent.click(screen.getByPlaceholderText("Search drivers..."));
  await userEvent.click(await screen.findByText(name));
}

function sentConfig() {
  return vi.mocked(api.updateDevice).mock.calls[0][1].config as Record<string, unknown>;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listDrivers).mockResolvedValue([widget, gadget]);
  vi.mocked(api.getDevice).mockResolvedValue({ needs_login: [] } as never);
});

describe("Edit Device with saved logins", () => {
  it("shows each saved login as Saved on this system, never as a value", async () => {
    openEdit();
    expect(await screen.findByRole("button", { name: "Change Password" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change API Key" })).toBeInTheDocument();
    expect(screen.getAllByText("Saved on this system")).toHaveLength(2);
    expect(screen.queryByDisplayValue(MARKER)).toBeNull();
  });

  it("keeps both saved logins on a save that changes something else", async () => {
    openEdit();
    await screen.findByRole("button", { name: "Change Password" });
    await userEvent.clear(screen.getByDisplayValue("eco"));
    await userEvent.type(screen.getByLabelText("Mode"), "bright");
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(sentConfig()).toMatchObject({ password: MARKER, api_key: MARKER, mode: "bright" });
  });

  it("sends a login typed after Change, and keeps the other one", async () => {
    openEdit();
    await userEvent.click(await screen.findByRole("button", { name: "Change Password" }));
    const box = screen.getByLabelText("Password");
    expect(box).toHaveValue("");
    expect(box).toHaveAttribute("type", "password");
    expect(box).toHaveFocus();
    await userEvent.type(box, "Hunter2-Acme");
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(sentConfig()).toMatchObject({ password: "Hunter2-Acme", api_key: MARKER });
  });

  it("keeps the saved login when Change is left empty", async () => {
    openEdit();
    await userEvent.click(await screen.findByRole("button", { name: "Change API Key" }));
    expect(screen.getByLabelText("API Key")).toHaveAttribute(
      "placeholder", "Leave blank to keep the saved one",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(sentConfig()).toMatchObject({ password: MARKER, api_key: MARKER });
  });

  it("asks another driver for its own login, and sends none until one is typed", async () => {
    openEdit();
    await screen.findByRole("button", { name: "Change Password" });
    await pickDriver("Acme Gadget");

    expect(screen.queryByText("Saved on this system")).toBeNull();
    expect(screen.getByLabelText("Password")).toHaveValue("");
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    const sent = vi.mocked(api.updateDevice).mock.calls[0][1];
    expect(sent.driver).toBe("acme_gadget");
    expect(sent.config).not.toHaveProperty("api_key");
    // The connection's password is removed rather than left as the marker,
    // which the new driver has nothing saved for.
    expect(sent.config).toMatchObject({ host: "192.0.2.10", password: null });
  });

  it("brings the saved logins back when the device's own driver is picked again", async () => {
    openEdit();
    await screen.findByRole("button", { name: "Change Password" });
    await pickDriver("Acme Gadget");
    await pickDriver("Acme Widget");

    expect(screen.getAllByText("Saved on this system")).toHaveLength(2);
  });

  it("shows a saved login as saved when the device's driver is not installed", async () => {
    const orphan = { ...device, driver: "acme_missing" } as DeviceConfig;
    openEdit(orphan, { host: "192.0.2.10", password: MARKER });
    expect(await screen.findByRole("button", { name: "Change password" })).toBeInTheDocument();
    expect(screen.queryByDisplayValue(MARKER)).toBeNull();
  });

  it("masks a password typed for a device whose driver is not installed", async () => {
    const orphan = { ...device, driver: "acme_missing", config: {} } as DeviceConfig;
    openEdit(orphan, { host: "192.0.2.10", password: "Hunter2-Acme" });
    await waitFor(() => expect(screen.getByDisplayValue("Hunter2-Acme")).toBeInTheDocument());
    expect(screen.getByDisplayValue("Hunter2-Acme")).toHaveAttribute("type", "password");
  });
});

describe("Edit Device on a device that waits for its login", () => {
  // The project marks the password saved, and this system has none for it
  // (a replacement restored from a copy without its logins): the device page
  // says Needs login, and Edit must not say the opposite.
  beforeEach(() => {
    vi.mocked(api.getDevice).mockResolvedValue({ needs_login: ["password"] } as never);
  });

  /** Open Edit once both the driver list and the device's waiting logins are
   *  in: the API Key's row is drawn, and the password has a box of its own. */
  async function openWaiting() {
    openEdit();
    await screen.findByRole("button", { name: "Change API Key" });
    await waitFor(() => expect(screen.getByLabelText("Password")).toBeInTheDocument());
  }

  it("asks for the login in an empty box rather than calling it saved", async () => {
    await openWaiting();
    const box = screen.getByLabelText("Password");
    expect(box).toHaveValue("");
    expect(box).toHaveAttribute("type", "password");
    expect(box).not.toHaveAttribute("placeholder", "Leave blank to keep the saved one");
    // The API Key is saved here, and still says so.
    expect(screen.getByRole("button", { name: "Change API Key" })).toBeInTheDocument();
    expect(screen.getAllByText("Saved on this system")).toHaveLength(1);
    expect(api.getDevice).toHaveBeenCalledWith("proj1");
  });

  it("saves a login typed there", async () => {
    await openWaiting();
    await userEvent.type(screen.getByLabelText("Password"), "Hunter2-Acme");
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(sentConfig()).toMatchObject({ password: "Hunter2-Acme", api_key: MARKER });
  });

  it("leaves the device waiting when the box is left empty, never dialing with nothing", async () => {
    await openWaiting();
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(sentConfig()).toMatchObject({ password: MARKER, api_key: MARKER });
  });
});

describe("carrying a device's settings to another driver", () => {
  it("never carries a saved login, which is filed under the old driver", () => {
    const form = { host: "192.0.2.10", port: "4352", password: MARKER };
    expect(carryOnDriverChange(form, "network", widget, gadget)).toEqual({
      host: "192.0.2.10",
      port: "23",
    });
  });
});

describe("Add Device and Duplicate", () => {
  it("never copies the source's saved logins into a duplicate", async () => {
    seed([device], { proj1: connection });
    render(<AddDeviceDialog onClose={() => {}} prefill={device} />);

    await waitFor(() => expect(screen.getByLabelText("Password")).toBeInTheDocument());
    expect(screen.queryByText("Saved on this system")).toBeNull();
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.getByLabelText("API Key")).toHaveValue("");
    expect(screen.queryByDisplayValue(MARKER)).toBeNull();
    expect(screen.getByDisplayValue("eco")).toBeInTheDocument();
  });

  it("re-reads the device from the server after saving a typed login", async () => {
    seed([], {});
    render(<AddDeviceDialog onClose={() => {}} />);
    await pickDriver("Acme Gadget");
    const id = (screen.getByLabelText(/^Device ID/) as HTMLInputElement).value;
    expect(id).not.toBe("");
    await userEvent.type(screen.getByPlaceholderText("192.168.1.100"), "192.0.2.11");
    await userEvent.type(screen.getByLabelText("Password"), "Hunter2-Acme");
    await userEvent.click(screen.getByRole("button", { name: "Add Device" }));

    await waitFor(() => expect(mocks.syncDeviceFromServer).toHaveBeenCalledWith(id));
  });

  it("does not re-read a device added with no login", async () => {
    seed([], {});
    render(<AddDeviceDialog onClose={() => {}} />);
    await pickDriver("Acme Gadget");
    await userEvent.type(screen.getByPlaceholderText("192.168.1.100"), "192.0.2.12");
    await userEvent.click(screen.getByRole("button", { name: "Add Device" }));

    await waitFor(() => expect(useProjectStore.getState().save).toHaveBeenCalled());
    expect(mocks.syncDeviceFromServer).not.toHaveBeenCalled();
  });
});
