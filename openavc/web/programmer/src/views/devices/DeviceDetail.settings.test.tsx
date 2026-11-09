import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * Device Settings on a device's page (the settings the driver can read and
 * write on the device itself: an input name, a volume, a network option) and
 * the Device Log's two tabs below it. A setting is changed in place, checked
 * before it is sent, and marked pending while the device has not reported the
 * new value back.
 */

const mocks = vi.hoisted(() => ({
  getDevice: vi.fn(),
  getDeviceSettings: vi.fn(),
  setDeviceSetting: vi.fn(),
}));

vi.mock("../../api/restClient", () => ({
  getDevice: mocks.getDevice,
  getDeviceSettings: mocks.getDeviceSettings,
  setDeviceSetting: mocks.setDeviceSetting,
  deleteDevice: vi.fn(),
  installCommunityDriver: vi.fn(),
  listMissingDrivers: vi.fn(async () => []),
  reconnectDevice: vi.fn(),
  resumeDevice: vi.fn(),
  sendCommand: vi.fn(),
  testDeviceConnection: vi.fn(),
}));
vi.mock("../../api/auditClient", () => ({ getAuditDeviceTarget: vi.fn() }));

const liveState: Record<string, unknown> = {};
vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: unknown) => unknown) => selector({ liveState }),
}));

vi.mock("./ChildEntities", () => ({ ChildEntities: () => null }));
vi.mock("./DeviceStateLog", () => ({
  DeviceStateLog: ({ deviceId }: { deviceId: string }) => <div data-testid="state-log">{deviceId}</div>,
}));
vi.mock("./DeviceProtocolLog", () => ({
  DeviceProtocolLog: ({ deviceId }: { deviceId: string }) => <div data-testid="protocol-log">{deviceId}</div>,
}));
vi.mock("./QuickActions", () => ({ QuickActions: () => null }));
vi.mock("./DriverUpdateLine", () => ({ DriverUpdateLine: () => null }));
vi.mock("./InstalledEquipment", () => ({ InstalledEquipment: () => null }));
vi.mock("../../components/plugins/PluginExtensions", () => ({
  DevicePanelSlot: () => null,
  ContextActionRenderer: () => null,
}));

import { useProjectStore } from "../../store/projectStore";
import { ApiError } from "../../api/errors";
import { DeviceDetail } from "./DeviceDetail";

const SETTINGS = {
  volume: { label: "Volume", help: "Output level.", type: "integer", min: 0, max: 100, current_value: 20 },
  input: {
    label: "Input", type: "enum", state_key: "active_input",
    values: [{ value: "hdmi1", label: "HDMI 1" }, { value: "hdmi2", label: "HDMI 2" }],
    current_value: "hdmi2",
  },
  eco: { label: "Eco Mode", type: "boolean", current_value: false },
  name: { label: "Name", type: "string", current_value: null, default: "Amp" },
  serial: { label: "Serial", type: "string", current_value: null },
};

function setLive(next: Record<string, unknown>) {
  for (const k of Object.keys(liveState)) delete liveState[k];
  Object.assign(liveState, { "device.amp.enabled": true, "device.amp.connected": true, ...next });
}

function setProject({ enabled = true, pending = {} as Record<string, unknown> } = {}) {
  useProjectStore.setState({
    project: {
      devices: [{ id: "amp", driver: "acme_amp", name: "Rack Amp", config: {}, enabled, pending_settings: pending }],
      connections: {},
      device_groups: [], macros: [], monitors: [],
      ui: { pages: [], master_elements: [] },
    } as never,
  });
}

function setInfo(extra: Record<string, unknown> = {}) {
  mocks.getDevice.mockResolvedValue({
    id: "amp", name: "Rack Amp", driver: "acme_amp", connected: true,
    enabled: true, commands: {}, driver_info: {}, ...extra,
  });
}

async function showPage() {
  const view = render(
    <DeviceDetail deviceId="amp" onEdit={() => {}} onDeleted={() => {}} onDuplicate={() => {}} />,
  );
  await screen.findByRole("button", { name: /Test/ });
  return view;
}

/** The row of one setting, found by its label. */
function row(label: string): HTMLElement {
  let el: HTMLElement | null = screen.getByText(label);
  while (el && el.parentElement && !el.parentElement.style.borderBottom) el = el.parentElement;
  return el!.parentElement!;
}

function editButton(label: string): HTMLButtonElement {
  return row(label).querySelector("button") as HTMLButtonElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  setProject();
  setInfo();
  setLive({});
  mocks.getDeviceSettings.mockResolvedValue({ settings: SETTINGS });
  mocks.setDeviceSetting.mockResolvedValue({});
});

afterEach(() => vi.useRealTimers());

describe("when Device Settings shows", () => {
  it("shows for an enabled, running device whose driver has settings", async () => {
    await showPage();
    expect(await screen.findByText("Device Settings")).toBeTruthy();
    expect(mocks.getDeviceSettings).toHaveBeenCalledWith("amp");
  });

  it.each([
    ["disabled in the project", () => setProject({ enabled: false })],
    ["not running", () => setLive({ "device.amp.enabled": false })],
    ["waiting for its driver", () => setInfo({ orphaned: true })],
  ])("is left out for a device %s", async (_name, arrange) => {
    arrange();
    await showPage();
    await act(async () => {});
    expect(screen.queryByText("Device Settings")).toBeNull();
    expect(mocks.getDeviceSettings).not.toHaveBeenCalled();
  });

  it("is left out when the driver declares no settings", async () => {
    mocks.getDeviceSettings.mockResolvedValue({ settings: {} });
    await showPage();
    await waitFor(() => expect(mocks.getDeviceSettings).toHaveBeenCalled());
    expect(screen.queryByText("Device Settings")).toBeNull();
  });

  it("is left out when the settings cannot be read", async () => {
    mocks.getDeviceSettings.mockRejectedValue(new Error("down"));
    await showPage();
    await waitFor(() => expect(mocks.getDeviceSettings).toHaveBeenCalled());
    expect(screen.queryByText("Device Settings")).toBeNull();
  });

  it("asks again every five seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await showPage();
    await screen.findByText("Device Settings");
    const before = mocks.getDeviceSettings.mock.calls.length;
    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(mocks.getDeviceSettings.mock.calls.length).toBe(before + 1);
  });
});

describe("a setting's row", () => {
  it("reads the live value under its state key, else the value the settings read gave", async () => {
    setLive({ "device.amp.active_input": "hdmi1", "device.amp.volume": 35 });
    await showPage();
    await screen.findByText("Device Settings");
    expect(row("Input").textContent).toContain("HDMI 1");
    expect(row("Volume").textContent).toContain("35");
    expect(row("Volume").textContent).toContain("Output level.");
    expect(row("Eco Mode").textContent).toContain("No");
    expect(row("Serial").textContent).toContain("(not set)");
  });

  it("marks a value that is waiting, in words that depend on the connection", async () => {
    setProject({ pending: { volume: 40 } });
    await showPage();
    await screen.findByText("Device Settings");
    expect(screen.getByText("pending").title).toBe(
      "Pending: 40. Sent, but the device has not reported it back yet. It will be sent again on the next connection.",
    );
  });

  it("says a waiting value goes out when a disconnected device connects", async () => {
    setProject({ pending: { volume: 40 } });
    setLive({ "device.amp.connected": false });
    await showPage();
    await screen.findByText("Device Settings");
    expect(screen.getByText("pending").title).toBe("Pending: 40, will be applied when device connects");
  });

  it("can be edited only while the device is connected", async () => {
    setLive({ "device.amp.connected": false });
    await showPage();
    await screen.findByText("Device Settings");
    const edit = editButton("Volume");
    expect(edit.disabled).toBe(true);
    expect(edit.title).toBe("Device must be connected to change settings");
    expect(edit.style.opacity).toBe("0.4");
  });
});

describe("changing a setting", () => {
  it("starts from the value on the device, and saves a checked number with Enter", async () => {
    setLive({ "device.amp.volume": 35 });
    await showPage();
    await screen.findByText("Device Settings");
    fireEvent.click(editButton("Volume"));
    const box = screen.getByRole("spinbutton") as HTMLInputElement;
    expect(box.value).toBe("35");
    expect(box.placeholder).toBe("0-100");
    expect(box.step).toBe("1");
    fireEvent.change(box, { target: { value: "55" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(mocks.setDeviceSetting).toHaveBeenCalledWith("amp", "volume", 55));
    expect(await screen.findByText("Setting saved successfully")).toBeTruthy();
    expect(screen.queryByRole("spinbutton")).toBeNull();
  });

  it("reads the settings again a second after a save", async () => {
    await showPage();
    await screen.findByText("Device Settings");
    fireEvent.click(editButton("Volume"));
    const before = mocks.getDeviceSettings.mock.calls.length;
    fireEvent.click(screen.getByTitle("Save"));
    await screen.findByText("Setting saved successfully");
    await waitFor(() => expect(mocks.getDeviceSettings.mock.calls.length).toBe(before + 1), { timeout: 2000 });
  });

  it("refuses a value that is not a number without sending it", async () => {
    await showPage();
    await screen.findByText("Device Settings");
    fireEvent.click(editButton("Volume"));
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "500" } });
    fireEvent.click(screen.getByTitle("Save"));
    expect(await screen.findByText("Error: Must be at most 100.")).toBeTruthy();
    expect(mocks.setDeviceSetting).not.toHaveBeenCalled();
    expect(screen.getByRole("spinbutton")).toBeTruthy();
  });

  it("says why the device refused it and keeps the box open", async () => {
    mocks.setDeviceSetting.mockRejectedValue(new ApiError(400, JSON.stringify({ detail: "Locked by the front panel." })));
    await showPage();
    await screen.findByText("Device Settings");
    fireEvent.click(editButton("Volume"));
    fireEvent.click(screen.getByTitle("Save"));
    expect(await screen.findByText("Error: Locked by the front panel.")).toBeTruthy();
    expect(screen.getByRole("spinbutton")).toBeTruthy();
  });

  it("Escape and Cancel close the box and clear the last result", async () => {
    await showPage();
    await screen.findByText("Device Settings");
    fireEvent.click(editButton("Volume"));
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "x" } });
    fireEvent.click(screen.getByTitle("Save"));
    await screen.findByText(/^Error:/);
    fireEvent.keyDown(screen.getByRole("spinbutton"), { key: "Escape" });
    expect(screen.queryByRole("spinbutton")).toBeNull();
    expect(screen.queryByText(/^Error:/)).toBeNull();

    fireEvent.click(editButton("Volume"));
    fireEvent.click(screen.getByTitle("Cancel"));
    expect(screen.queryByRole("spinbutton")).toBeNull();
  });

  it("offers Yes and No for a switch, and sends true or false", async () => {
    await showPage();
    await screen.findByText("Device Settings");
    fireEvent.click(editButton("Eco Mode"));
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("false");
    expect([...select.options].map((o) => o.textContent)).toEqual(["Yes", "No"]);
    fireEvent.change(select, { target: { value: "true" } });
    fireEvent.click(screen.getByTitle("Save"));
    await waitFor(() => expect(mocks.setDeviceSetting).toHaveBeenCalledWith("amp", "eco", true));
  });

  it("offers the declared choices by label for a choice", async () => {
    await showPage();
    await screen.findByText("Device Settings");
    fireEvent.click(editButton("Input"));
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("hdmi2");
    expect([...select.options].map((o) => [o.value, o.textContent])).toEqual([
      ["hdmi1", "HDMI 1"], ["hdmi2", "HDMI 2"],
    ]);
    fireEvent.change(select, { target: { value: "hdmi1" } });
    fireEvent.click(screen.getByTitle("Save"));
    await waitFor(() => expect(mocks.setDeviceSetting).toHaveBeenCalledWith("amp", "input", "hdmi1"));
  });

  it("starts an unset text setting from its default, else blank", async () => {
    await showPage();
    await screen.findByText("Device Settings");
    fireEvent.click(editButton("Name"));
    const named = screen.getAllByRole("textbox").find((el) => el.dataset.testid !== "device-filter") as HTMLInputElement;
    expect(named.value).toBe("Amp");
    expect(named.type).toBe("text");
    fireEvent.keyDown(named, { key: "Escape" });

    fireEvent.click(editButton("Serial"));
    const blank = screen.getAllByRole("textbox").find((el) => el.dataset.testid !== "device-filter") as HTMLInputElement;
    expect(blank.value).toBe("");
    expect(blank.placeholder).toBe("");
  });
});

describe("the Device Log", () => {
  it("opens on the protocol log and switches to state changes", async () => {
    await showPage();
    expect(screen.getByTestId("protocol-log").textContent).toBe("amp");
    expect(screen.queryByTestId("state-log")).toBeNull();
    const protocol = screen.getByRole("button", { name: "Protocol" });
    const changes = screen.getByRole("button", { name: "State Changes" });
    expect(protocol.style.background).toBe("var(--accent-bg)");
    expect(changes.style.background).toBe("var(--bg-hover)");
    fireEvent.click(changes);
    expect(screen.getByTestId("state-log").textContent).toBe("amp");
    expect(screen.queryByTestId("protocol-log")).toBeNull();
    expect(changes.style.fontWeight).toBe("600");
    fireEvent.click(protocol);
    expect(screen.getByTestId("protocol-log")).toBeTruthy();
  });
});
