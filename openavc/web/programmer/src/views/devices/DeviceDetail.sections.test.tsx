import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";

/**
 * The device page's Bridge Ports section (a bridge's ports and what is bound
 * to each), and when the page reads the device from the running system again:
 * on the events that change what the device offers, never drawing an answer
 * for a device it has stopped showing.
 */

const mocks = vi.hoisted(() => ({
  getDevice: vi.fn(),
  irProps: [] as Record<string, unknown>[],
}));

vi.mock("../../api/restClient", () => ({
  getDevice: mocks.getDevice,
  deleteDevice: vi.fn(),
  reconnectDevice: vi.fn(),
  testDeviceConnection: vi.fn(),
  getDeviceSettings: vi.fn(async () => ({ settings: {} })),
  installCommunityDriver: vi.fn(),
  listMissingDrivers: vi.fn(() => new Promise(() => {})),
  resumeDevice: vi.fn(),
  sendCommand: vi.fn(),
  setDeviceSetting: vi.fn(),
}));
vi.mock("../../api/auditClient", () => ({ getAuditDeviceTarget: vi.fn() }));

const liveState: Record<string, unknown> = {};
vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: unknown) => unknown) => selector({ liveState }),
}));

vi.mock("./ChildEntities", () => ({ ChildEntities: () => null }));
vi.mock("./DeviceStateLog", () => ({ DeviceStateLog: () => null }));
vi.mock("./DeviceProtocolLog", () => ({ DeviceProtocolLog: () => null }));
vi.mock("./QuickActions", () => ({ QuickActions: () => null }));
vi.mock("./InstalledEquipment", () => ({ InstalledEquipment: () => null }));
vi.mock("./DriverUpdateLine", () => ({ DriverUpdateLine: () => null }));
vi.mock("./BridgeIrPort", () => ({
  BridgeIrPort: (props: Record<string, unknown>) => {
    mocks.irProps.push(props);
    return <div data-testid={`ir-${props.portId}`}>{String(props.portLabel)}</div>;
  },
}));
vi.mock("./OrphanBanner", () => ({
  OrphanBanner: ({ onActivated }: { onActivated: () => void }) => (
    <button onClick={onActivated}>activated</button>
  ),
}));
vi.mock("../../components/plugins/PluginExtensions", () => ({
  DevicePanelSlot: () => null,
  ContextActionRenderer: () => null,
}));

import { useProjectStore } from "../../store/projectStore";
import { ApiError } from "../../api/errors";
import { DeviceDetail } from "./DeviceDetail";

const itach = { id: "itach", driver: "acme_bridge", name: "Rack iTach", config: {}, enabled: true };

function setLive(next: Record<string, unknown>) {
  for (const k of Object.keys(liveState)) delete liveState[k];
  Object.assign(liveState, { "device.itach.enabled": true, ...next });
}

function setProject(devices: Record<string, unknown>[], connections: Record<string, unknown>) {
  useProjectStore.setState({
    project: {
      devices, connections,
      device_groups: [], macros: [], monitors: [],
      ui: { pages: [], master_elements: [] },
    } as never,
  });
}

const PORTS = [
  { id: "1:1", label: "Serial 1", kind: "serial", passthrough_port: 4999 },
  { id: "1:2", kind: "serial" },
  { id: "1:3", label: "IR 1", kind: "ir" },
  { id: "1:4", kind: "relay", passthrough_port: 5000 },
];

function setInfo(extra: Record<string, unknown> = {}) {
  mocks.getDevice.mockResolvedValue({
    id: "itach", name: "Rack iTach", driver: "acme_bridge", connected: false, enabled: true,
    commands: {}, driver_info: { bridge: { ports: PORTS } }, ...extra,
  });
}

function page(deviceId = "itach", onOpenDevice?: (id: string) => void) {
  return (
    <DeviceDetail
      deviceId={deviceId}
      onEdit={() => {}}
      onDeleted={() => {}}
      onDuplicate={() => {}}
      onOpenDevice={onOpenDevice}
    />
  );
}

/** Each port's box, as its text. */
function portBoxes(): string[] {
  const title = screen.getByRole("heading", { name: "Bridge Ports" });
  const list = title.parentElement!.lastElementChild!;
  return Array.from(list.children).map((c) => c.textContent ?? "");
}

describe("Bridge Ports", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.irProps.length = 0;
    setLive({});
    setInfo();
  });

  it("lists each port with what is bound to it, by name, else id", async () => {
    setProject(
      [
        itach,
        { id: "proj", driver: "acme_proj", name: "Projector", config: {}, enabled: true },
        { id: "tv", driver: "acme_tv", name: "", config: {}, enabled: true },
        { id: "other", driver: "acme_tv", name: "Elsewhere", config: {}, enabled: true },
      ],
      {
        itach: { host: "10.0.0.7" },
        proj: { bridge: "itach", bridge_port: "1:2" },
        tv: { bridge: "itach", bridge_port: "1:2" },
        other: { bridge: "itach2", bridge_port: "1:1" },
      },
    );
    const onOpenDevice = vi.fn();
    render(page("itach", onOpenDevice));
    await screen.findByRole("heading", { name: "Bridge Ports" });
    expect(portBoxes()).toEqual([
      "Serial 1serial1:1Nothing bound (pass-through TCP 4999)",
      "1:2serial1:2Bound: Projector, tv",
      "IR 1ir1:3Nothing boundIR 1",
      "1:4relay1:4Nothing bound",
    ]);
    expect(screen.getByText(/Other devices connect through this bridge's ports/)).toBeTruthy();
    const link = screen.getByRole("link", { name: "Open this unit's web interface" });
    expect(link.getAttribute("href")).toBe("http://10.0.0.7");
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("hands an IR port its bridge, label, connection, bound devices and the way to open one", async () => {
    const tv = { id: "tv", driver: "acme_tv", name: "TV", config: {}, enabled: true };
    setProject([itach, tv], { itach: {}, tv: { bridge: "itach", bridge_port: "1:3" } });
    setLive({ "device.itach.connected": true });
    const onOpenDevice = vi.fn();
    render(page("itach", onOpenDevice));
    await screen.findByTestId("ir-1:3");
    const props = mocks.irProps[mocks.irProps.length - 1];
    expect(props).toEqual({
      bridgeId: "itach", portId: "1:3", portLabel: "IR 1", connected: true,
      bound: [tv], onOpenDevice,
    });
  });

  it("offers no web link without a host", async () => {
    setProject([itach], {});
    render(page());
    await screen.findByRole("heading", { name: "Bridge Ports" });
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("is not drawn for a device that advertises no ports", async () => {
    setProject([itach], {});
    setInfo({ driver_info: { bridge: { ports: [] } } });
    render(page());
    await screen.findByRole("button", { name: /^Test/ });
    expect(screen.queryByRole("heading", { name: "Bridge Ports" })).toBeNull();
    setInfo({ driver_info: {} });
  });
});

describe("reading the device again", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setLive({});
    setInfo({ driver_info: {} });
    setProject([itach], {});
  });

  async function shown() {
    const view = render(page());
    await screen.findByRole("button", { name: /^Test/ });
    expect(mocks.getDevice).toHaveBeenCalledTimes(1);
    return view;
  }

  it.each([
    ["it connects", { "device.itach.connected": true }],
    ["its web page is found", { "device.itach.web_ui_url": "http://10.0.0.7/" }],
    ["the running system turns it off", { "device.itach.enabled": false }],
    ["it starts waiting for a login", { "device.itach.offline_reason": "login_missing" }],
  ])("when %s", async (_what, change) => {
    const view = await shown();
    setLive(change);
    view.rerender(page());
    await waitFor(() => expect(mocks.getDevice).toHaveBeenCalledTimes(2));
    expect(mocks.getDevice).toHaveBeenLastCalledWith("itach");
  });

  it("not when a reading changes", async () => {
    const view = await shown();
    setLive({ "device.itach.power": "on" });
    view.rerender(page());
    await act(async () => { await Promise.resolve(); });
    expect(mocks.getDevice).toHaveBeenCalledTimes(1);
  });

  it("draws nothing it heard for a device it has stopped showing", async () => {
    let answer!: (v: unknown) => void;
    mocks.getDevice.mockReturnValueOnce(new Promise((r) => { answer = r; }));
    mocks.getDevice.mockReturnValueOnce(new Promise(() => {}));
    setProject([itach, { id: "tv", driver: "acme_tv", name: "TV", config: {}, enabled: true }], {});
    const view = render(page("itach"));
    view.rerender(page("tv"));
    await act(async () => {
      answer({
        id: "itach", name: "Rack iTach", driver: "acme_bridge", connected: false, enabled: true,
        commands: {}, driver_info: { bridge: { ports: PORTS } },
      });
    });
    expect(screen.queryByRole("heading", { name: "Bridge Ports" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Test/ })).toBeNull();
  });

  it("does not take a 404 for a device it has stopped showing as this one not running", async () => {
    let refuse!: (e: unknown) => void;
    mocks.getDevice.mockReturnValueOnce(new Promise((_, r) => { refuse = r; }));
    mocks.getDevice.mockReturnValueOnce(new Promise(() => {}));
    setProject([itach, { id: "tv", driver: "acme_tv", name: "TV", config: {}, enabled: true }], {});
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    const view = render(page("itach"));
    view.rerender(page("tv"));
    await act(async () => {
      refuse(new ApiError(404, JSON.stringify({ detail: "Device 'itach' not found" })));
    });
    expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Delete/ })).toBeNull();
    logged.mockRestore();
  });

  it("takes the device as the running system has it once its driver is installed", async () => {
    setLive({ "device.itach.orphaned": true });
    render(page());
    await screen.findByRole("button", { name: /^Test/ });
    setInfo();
    screen.getByRole("button", { name: "activated" }).click();
    await screen.findByRole("heading", { name: "Bridge Ports" });
    expect(mocks.getDevice).toHaveBeenCalledTimes(2);
  });
});
