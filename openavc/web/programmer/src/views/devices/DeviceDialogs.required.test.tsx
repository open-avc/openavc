import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// A driver's required config field is refused blank at Add and Edit Device,
// by its label. The IP address is asked only of the connection the picker is
// set to: a device switched to "Through a bridge" is asked for the bridge and
// the port it is wired to instead, never an IP address.

vi.mock("../../api/restClient", () => ({
  listDrivers: vi.fn(),
  updateDevice: vi.fn(),
}));
vi.mock("../../api/systemClient", () => ({
  getSerialPorts: vi.fn(() => Promise.resolve({ ports: [] })),
}));

import * as api from "../../api/restClient";
import type { DeviceConfig, DriverInfo } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { AddDeviceDialog, EditDeviceDialog } from "./DeviceDialogs";

const camera = {
  id: "acme_camera",
  name: "Acme Camera",
  manufacturer: "Acme",
  category: "camera",
  transport: "tcp",
  transports: ["tcp", "serial"],
  commands: {},
  default_config: { port: 5500 },
  config_schema: {
    host: { type: "string", required: true, label: "IP Address" },
    port: { type: "integer", default: 5500, label: "Port" },
    model: {
      type: "enum",
      required: true,
      label: "Model",
      values: [
        { value: "w12", label: "W12" },
        { value: "w20", label: "W20" },
      ],
    },
  },
} as unknown as DriverInfo;

const update = vi.fn();
const save = vi.fn(() => Promise.resolve());

function seedProject(devices: DeviceConfig[] = [], connections = {}) {
  useProjectStore.setState({
    project: { devices, connections } as never,
    update,
    save,
  });
}

async function pickCamera() {
  await userEvent.click(screen.getByPlaceholderText("Search drivers..."));
  await userEvent.click(await screen.findByText("Acme Camera"));
}

describe("required config fields in the device dialogs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listDrivers).mockResolvedValue([camera]);
    seedProject();
  });

  it("Add Device refuses a blank Model by name, then saves once it is chosen", async () => {
    render(<AddDeviceDialog onClose={() => {}} />);
    await pickCamera();
    await userEvent.type(screen.getByPlaceholderText("192.168.1.100"), "10.0.0.5");

    await userEvent.click(screen.getByRole("button", { name: "Add Device" }));
    expect(screen.getByText("Model is required.")).toBeInTheDocument();
    expect(update).not.toHaveBeenCalled();

    await userEvent.selectOptions(screen.getByLabelText(/^Model/), "w12");
    await userEvent.click(screen.getByRole("button", { name: "Add Device" }));
    expect(update).toHaveBeenCalledTimes(1);
    const patch = update.mock.calls[0][0];
    expect(patch.devices[0].config).toEqual({ model: "w12" });
    expect(patch.connections.camera_1).toEqual({ host: "10.0.0.5", port: 5500 });
  });

  it("brings the refusal into view each time, at the top of a dialog that scrolls", async () => {
    const scrolled = vi.fn();
    Element.prototype.scrollIntoView = scrolled;
    render(<AddDeviceDialog onClose={() => {}} />);
    await pickCamera();
    await userEvent.type(screen.getByPlaceholderText("192.168.1.100"), "10.0.0.5");
    await userEvent.click(screen.getByRole("button", { name: "Add Device" }));
    await userEvent.click(screen.getByRole("button", { name: "Add Device" }));
    const banner = screen.getByText("Model is required.");
    expect(scrolled).toHaveBeenCalledTimes(2);
    expect(scrolled.mock.contexts.every((el) => el === banner)).toBe(true);
  });

  it("marks the picker's IP Address as required and asks for it on the network", async () => {
    render(<AddDeviceDialog onClose={() => {}} />);
    await pickCamera();
    expect(screen.getByPlaceholderText("192.168.1.100")).toHaveAttribute("aria-required", "true");
    await userEvent.selectOptions(screen.getByLabelText(/^Model/), "w12");
    await userEvent.click(screen.getByRole("button", { name: "Add Device" }));
    expect(screen.getByText("IP Address is required.")).toBeInTheDocument();
    expect(update).not.toHaveBeenCalled();
  });

  it("asks for the bridge, not an IP address, through a bridge before one is picked", async () => {
    render(<AddDeviceDialog onClose={() => {}} />);
    await pickCamera();
    await userEvent.click(screen.getByRole("button", { name: "Through a bridge" }));
    await userEvent.selectOptions(screen.getByLabelText(/^Model/), "w12");
    await userEvent.click(screen.getByRole("button", { name: "Add Device" }));
    expect(screen.getByText("Bridge is required.")).toBeInTheDocument();
    expect(screen.queryByText(/IP Address/)).toBeNull();
    expect(update).not.toHaveBeenCalled();
  });

  it("does not ask for an IP address of a direct serial connection", async () => {
    render(<AddDeviceDialog onClose={() => {}} />);
    await pickCamera();
    await userEvent.click(screen.getByRole("button", { name: "Direct serial" }));
    await userEvent.selectOptions(screen.getByLabelText(/^Model/), "w12");
    await userEvent.click(screen.getByRole("button", { name: "Add Device" }));
    expect(screen.queryByText(/is required\./)).toBeNull();
    expect(update).toHaveBeenCalledTimes(1);
  });

  it("Edit Device reopens a bridge saved before its port on the bridge and asks for the port", async () => {
    const device = { id: "cam1", driver: "acme_camera", name: "Cam", config: { model: "w12" } };
    seedProject([device as DeviceConfig], { cam1: { bridge: "itach1", baudrate: 9600 } });
    render(<EditDeviceDialog device={device as DeviceConfig} onClose={() => {}} onSaved={() => {}} />);
    await waitFor(() => expect(screen.getByLabelText(/^Model/)).toHaveValue("w12"));

    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(screen.getByText("Bridge port is required.")).toBeInTheDocument();
    expect(screen.queryByText(/IP Address/)).toBeNull();
    expect(api.updateDevice).not.toHaveBeenCalled();
  });

  it("Edit Device saves a bridge with its port, asking no IP address", async () => {
    const device = { id: "cam1", driver: "acme_camera", name: "Cam", config: { model: "w12" } };
    seedProject([device as DeviceConfig], {
      cam1: { bridge: "itach1", bridge_port: "serial:1", baudrate: 9600 },
    });
    render(<EditDeviceDialog device={device as DeviceConfig} onClose={() => {}} onSaved={() => {}} />);
    await waitFor(() => expect(screen.getByLabelText(/^Model/)).toHaveValue("w12"));

    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(screen.queryByText(/is required\./)).toBeNull();
    expect(api.updateDevice).toHaveBeenCalledTimes(1);
  });

  it("Edit Device refuses a Model cleared to blank and sends nothing", async () => {
    const device = { id: "cam1", driver: "acme_camera", name: "Cam", config: { model: "w20" } };
    seedProject([device as DeviceConfig], { cam1: { host: "10.0.0.9", port: 5500 } });
    render(<EditDeviceDialog device={device as DeviceConfig} onClose={() => {}} onSaved={() => {}} />);
    const model = await screen.findByLabelText(/^Model/);
    await waitFor(() => expect(model).toHaveValue("w20"));

    await userEvent.selectOptions(model, "");
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(screen.getByText("Model is required.")).toBeInTheDocument();
    expect(api.updateDevice).not.toHaveBeenCalled();
  });
});
