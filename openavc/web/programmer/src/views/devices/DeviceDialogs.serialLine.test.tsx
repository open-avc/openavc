import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Add Device's serial line settings come from the driver contract's one
// table: a driver whose defaults are written as a manual writes them (Even,
// RTS/CTS) or at a rate the list does not name opens with those selected,
// and every parity and flow control a port takes is offered.

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

const widget = {
  id: "acme_widget",
  name: "Acme Widget",
  manufacturer: "Acme",
  category: "utility",
  transport: "serial",
  commands: {},
  default_config: { port: "", baudrate: 230400, parity: "Even", flow_control: "RTS/CTS" },
  config_schema: { port: { type: "string", required: true, label: "Serial Port" } },
} as unknown as DriverInfo;

function picker(label: string): HTMLSelectElement {
  const field = screen.getByText(label).parentElement as HTMLElement;
  return within(field).getByRole("combobox") as HTMLSelectElement;
}

describe("Add Device's serial line settings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listDrivers).mockResolvedValue([widget]);
    useProjectStore.setState({
      project: { devices: [], connections: {} } as never,
      update: vi.fn(),
      save: vi.fn(() => Promise.resolve()),
    });
  });

  it("opens with the driver's defaults selected, however they are written", async () => {
    render(<AddDeviceDialog onClose={() => {}} />);
    await userEvent.click(screen.getByPlaceholderText("Search drivers..."));
    await userEvent.click(await screen.findByText("Acme Widget"));

    expect(picker("Baud rate").value).toBe("230400");
    expect(picker("Parity").value).toBe("E");
    expect(picker("Flow control").value).toBe("hardware");
    const parities = Array.from(picker("Parity").options).map((o) => o.textContent);
    expect(parities).toEqual(["None", "Even", "Odd", "Mark", "Space"]);
    const flows = Array.from(picker("Flow control").options).map((o) => o.value);
    expect(flows).toEqual(["none", "hardware", "software"]);
  });
});
