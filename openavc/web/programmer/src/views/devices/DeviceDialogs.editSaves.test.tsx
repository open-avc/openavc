import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Edit Device saves what its form shows. Picking another driver keeps what
// belongs to the device (its address, its serial port or bridge port, its
// login) and takes the rest from the new driver. A connection setting the form
// no longer holds is removed rather than kept out of sight: a device moved off
// a bridge stops going through it.

vi.mock("../../api/restClient", () => ({
  listDrivers: vi.fn(),
  updateDevice: vi.fn(() => Promise.resolve({ status: "updated", device_id: "x" })),
}));
vi.mock("../../api/systemClient", () => ({
  getSerialPorts: vi.fn(() => Promise.resolve({ ports: [] })),
}));

import * as api from "../../api/restClient";
import type { DeviceConfig, DriverInfo } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { carryOnDriverChange, EditDeviceDialog } from "./DeviceDialogs";

const driver = (over: Record<string, unknown>) =>
  ({ manufacturer: "Acme", category: "camera", commands: {}, ...over }) as unknown as DriverInfo;

const widget = driver({
  id: "acme_widget",
  name: "Acme Widget",
  transport: "tcp",
  transports: ["tcp", "serial"],
  default_config: { port: 23 },
  config_schema: {
    host: { type: "string", required: true, label: "IP Address" },
    port: { type: "integer", label: "Port" },
    password: { type: "string", secret: true, label: "Password" },
  },
});
const widgetPro = driver({
  id: "acme_widget_pro",
  name: "Acme Widget Pro",
  transport: "tcp",
  transports: ["tcp", "serial"],
  default_config: { port: 4352, baudrate: 19200 },
  config_schema: {
    host: { type: "string", required: true, label: "IP Address" },
    port: { type: "integer", label: "Port" },
    password: { type: "string", secret: true, label: "Password" },
  },
});
const networkOnly = driver({
  id: "acme_net",
  name: "Acme Net",
  transport: "http",
  default_config: { port: 80 },
  config_schema: { host: { type: "string", required: true, label: "IP Address" } },
});
const irOnly = driver({ id: "acme_ir", name: "Acme IR", transport: "bridge", config_schema: {} });

describe("carryOnDriverChange", () => {
  it("keeps the IP address and the login, and takes the new driver's port", () => {
    const form = { host: "10.0.0.5", port: "23", password: "s3cret", transport: "tcp" };
    expect(carryOnDriverChange(form, "network", widget, widgetPro)).toEqual({
      host: "10.0.0.5",
      port: "4352",
      baudrate: "19200",
      password: "s3cret",
    });
  });

  it("keeps a serial device's port and line settings when the new driver speaks serial", () => {
    const form = { transport: "serial", port: "/dev/ttyUSB0", usb_serial: "A1B2", baudrate: "9600", parity: "E" };
    expect(carryOnDriverChange(form, "serial", widget, widgetPro)).toEqual({
      transport: "serial",
      port: "/dev/ttyUSB0",
      usb_serial: "A1B2",
      baudrate: "9600",
      parity: "E",
    });
  });

  it("keeps a bridge binding between two serial drivers, never into an IR one", () => {
    const form = { bridge: "itach_1", bridge_port: "serial:1", baudrate: "9600" };
    expect(carryOnDriverChange(form, "bridge", widget, widgetPro)).toEqual({
      bridge: "itach_1",
      bridge_port: "serial:1",
      baudrate: "9600",
    });
    expect(carryOnDriverChange(form, "bridge", widget, irOnly)).toEqual({});
  });

  it("keeps nothing of a connection the new driver does not offer", () => {
    const form = { transport: "serial", port: "COM3", baudrate: "9600" };
    expect(carryOnDriverChange(form, "serial", widget, networkOnly)).toEqual({ port: "80" });
  });
});

describe("Edit Device saves what its form shows", () => {
  function open(device: DeviceConfig, connection: Record<string, unknown>) {
    useProjectStore.setState({
      project: { devices: [device], connections: { [device.id]: connection } } as never,
      load: vi.fn(() => Promise.resolve()),
    });
    render(<EditDeviceDialog device={device} onClose={() => {}} onSaved={() => {}} />);
  }

  async function pickDriver(name: string) {
    await userEvent.click(screen.getByPlaceholderText("Search drivers..."));
    await userEvent.click(await screen.findByText(name));
  }

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listDrivers).mockResolvedValue([widget, widgetPro, networkOnly, irOnly]);
  });

  it("keeps the IP address when another driver is picked", async () => {
    const device = { id: "w1", driver: "acme_widget", name: "W1", config: {} };
    open(device as DeviceConfig, { host: "10.0.0.5", port: 23 });
    await waitFor(() => expect(screen.getByPlaceholderText("192.168.1.100")).toHaveValue("10.0.0.5"));

    await pickDriver("Acme Widget Pro");
    expect(screen.getByPlaceholderText("192.168.1.100")).toHaveValue("10.0.0.5");
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(screen.queryByText(/is required\./)).toBeNull();
    const sent = vi.mocked(api.updateDevice).mock.calls[0][1];
    expect(sent.driver).toBe("acme_widget_pro");
    expect(sent.config).toMatchObject({ host: "10.0.0.5", port: 4352 });
  });

  it("brings back the saved settings when the device's own driver is picked again", async () => {
    const device = { id: "w1", driver: "acme_widget", name: "W1", config: {} };
    open(device as DeviceConfig, { host: "10.0.0.5", port: 2323 });
    await waitFor(() => expect(screen.getByPlaceholderText("192.168.1.100")).toHaveValue("10.0.0.5"));
    await pickDriver("Acme Net");
    await pickDriver("Acme Widget");
    expect(screen.getByDisplayValue("2323")).toBeInTheDocument();
  });

  it("removes the bridge binding when the device is moved to the network", async () => {
    const device = { id: "w1", driver: "acme_widget", name: "W1", config: {} };
    open(device as DeviceConfig, { bridge: "itach_1", bridge_port: "serial:1", baudrate: 9600 });
    await userEvent.click(await screen.findByRole("button", { name: "Network (IP)" }));
    await userEvent.type(screen.getByPlaceholderText("192.168.1.100"), "10.0.0.9");
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    const sent = vi.mocked(api.updateDevice).mock.calls[0][1];
    expect(sent.config).toMatchObject({
      host: "10.0.0.9", bridge: null, bridge_port: null, baudrate: null,
    });
  });

  it("removes a connection setting the form cleared", async () => {
    const device = { id: "w1", driver: "acme_widget", name: "W1", config: {} };
    open(device as DeviceConfig, { host: "10.0.0.5", port: 2323 });
    await waitFor(() => expect(screen.getByPlaceholderText("192.168.1.100")).toHaveValue("10.0.0.5"));
    await userEvent.clear(screen.getByDisplayValue("2323"));
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    const sent = vi.mocked(api.updateDevice).mock.calls[0][1];
    expect(sent.config).toMatchObject({ host: "10.0.0.5", port: null });
  });
});
