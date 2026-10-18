import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Add Device's connection picker, for a driver whose own transport is serial
// but which also connects over the network. What the picker shows is what is
// saved: opened on Network (IP), the device is saved as a network device, not
// as one that opens a serial port named after the TCP port. And switching to
// Direct serial and back gives the Port its default again.

vi.mock("../../api/restClient", () => ({
  listDrivers: vi.fn(),
  updateDevice: vi.fn(),
}));
vi.mock("../../api/systemClient", () => ({
  getSerialPorts: vi.fn(() => Promise.resolve({ ports: [] })),
}));

import * as api from "../../api/restClient";
import type { DriverInfo } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { AddDeviceDialog } from "./DeviceDialogs";

const matrix = {
  id: "acme_matrix",
  name: "Acme Matrix",
  manufacturer: "Acme",
  category: "switcher",
  transport: "serial",
  transports: ["serial", "tcp"],
  commands: {},
  default_config: { host: "", port: 5000, baudrate: 19200 },
  config_schema: {
    host: { type: "string", required: true, label: "IP Address" },
    port: { type: "integer", default: 5000, label: "Port" },
    baudrate: { type: "integer", default: 19200, label: "Baud Rate" },
  },
} as unknown as DriverInfo;

const update = vi.fn();

async function pickMatrix() {
  render(<AddDeviceDialog onClose={() => {}} />);
  await userEvent.click(screen.getByPlaceholderText("Search drivers..."));
  await userEvent.click(await screen.findByText("Acme Matrix"));
}

describe("Add Device's connection picker, serial-first driver", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listDrivers).mockResolvedValue([matrix]);
    useProjectStore.setState({
      project: { devices: [], connections: {} } as never,
      update,
      save: vi.fn(() => Promise.resolve()),
    });
  });

  it("saves the network transport it opens on", async () => {
    await pickMatrix();
    await userEvent.type(screen.getByPlaceholderText("192.168.1.100"), "10.0.0.5");
    await userEvent.click(screen.getByRole("button", { name: "Add Device" }));

    expect(update).toHaveBeenCalledTimes(1);
    const saved = JSON.stringify(update.mock.calls[0][0]);
    expect(saved).toContain('"transport":"tcp"');
  });

  it("gives Port its default again after Direct serial and back", async () => {
    await pickMatrix();
    await userEvent.click(screen.getByRole("button", { name: "Direct serial" }));
    await userEvent.click(screen.getByRole("button", { name: "Network (IP)" }));
    expect(screen.getByPlaceholderText("5000")).toHaveValue(5000);
  });
});
