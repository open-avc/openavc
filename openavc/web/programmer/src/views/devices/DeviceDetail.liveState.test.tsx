import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * The device page's Live State table and the filter above it: which of the
 * device's keys it lists (a child's keys go to the Child Entities panel, which
 * hears how many), how a value reads (rounded, an option list summarised, a
 * reading nobody hears any more marked), what the filter narrows, and the
 * Monitor toggle on each row with its limits underneath, one row at a time.
 */

const mocks = vi.hoisted(() => ({
  getDevice: vi.fn(),
  childProps: [] as Record<string, unknown>[],
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

vi.mock("./ChildEntities", () => ({
  ChildEntities: (props: Record<string, unknown>) => {
    mocks.childProps.push(props);
    return <div data-testid="child-entities">{`${props.search}|${props.childKeyCount}|${props.parentAway}`}</div>;
  },
}));
vi.mock("./DeviceStateLog", () => ({ DeviceStateLog: () => null }));
vi.mock("./DeviceProtocolLog", () => ({ DeviceProtocolLog: () => null }));
vi.mock("./QuickActions", () => ({ QuickActions: () => null }));
vi.mock("./InstalledEquipment", () => ({ InstalledEquipment: () => null }));
vi.mock("./DriverUpdateLine", () => ({ DriverUpdateLine: () => null }));
vi.mock("../../components/shared/MonitorControl", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../components/shared/MonitorControl")>()),
  MonitorLimitsPanel: ({ monitor, declared }: { monitor: { key: string }; declared?: { label?: string } }) => (
    <div data-testid="limits-panel">{`${monitor.key}|${declared?.label ?? ""}`}</div>
  ),
}));
vi.mock("../../components/plugins/PluginExtensions", () => ({
  DevicePanelSlot: () => null,
  ContextActionRenderer: () => null,
}));

import { useProjectStore } from "../../store/projectStore";
import { DeviceDetail } from "./DeviceDetail";

function setLive(next: Record<string, unknown>) {
  for (const k of Object.keys(liveState)) delete liveState[k];
  Object.assign(liveState, { "device.amp.enabled": true, ...next });
}

function setProject(monitors: Record<string, unknown>[] = []) {
  useProjectStore.setState({
    project: {
      devices: [{ id: "amp", driver: "acme_amp", name: "Rack Amp", config: {}, enabled: true }],
      connections: {},
      device_groups: [], macros: [], monitors,
      ui: { pages: [], master_elements: [] },
    } as never,
  });
}

function setInfo(extra: Record<string, unknown> = {}) {
  mocks.getDevice.mockResolvedValue({
    id: "amp", name: "Rack Amp", driver: "acme_amp", connected: false,
    enabled: true, commands: {}, driver_info: {}, ...extra,
  });
}

async function showPage() {
  const view = render(
    <DeviceDetail deviceId="amp" onEdit={() => {}} onDeleted={() => {}} onDuplicate={() => {}} />,
  );
  await screen.findByRole("button", { name: /^Test/ });
  return view;
}

/** The Live State table's rows, as [key cell, value cell] text. */
function rows(): [string, string][] {
  return Array.from(document.querySelectorAll("tr[data-testid^='live-state-']")).map((tr) => {
    const cells = tr.querySelectorAll("td");
    return [cells[0].textContent ?? "", cells[1].textContent ?? ""];
  });
}

const row = (key: string) => screen.getByTestId(`live-state-${key}`);
const valueCell = (key: string) => row(key).querySelectorAll("td")[1] as HTMLElement;

describe("the device page's Live State", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.childProps.length = 0;
    setLive({});
    setProject();
    setInfo();
  });

  it("lists the device's own keys in the order they arrived, nothing else", async () => {
    setLive({
      "device.amp.power": "on",
      "device.amps.power": "other device",
      "device.tv.power": "off",
      "device.amp.volume": 40,
      "var.amp": 1,
    });
    await showPage();
    expect(rows()).toEqual([["enabled", "Yes"], ["power", "on"], ["volume", "40"]]);
  });

  it("says when there is nothing yet", async () => {
    setLive({});
    for (const k of Object.keys(liveState)) delete liveState[k];
    await showPage();
    expect(screen.getByText("No state values yet")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("rounds a fraction to the driver's step, with the full value on hover", async () => {
    setInfo({ driver_info: { state_variables: { level: { step: 0.5 }, temp: {} } } });
    setLive({
      "device.amp.connected": true,
      "device.amp.level": -12.3456, "device.amp.temp": 41.23456, "device.amp.gain": 3,
    });
    await showPage();
    expect(valueCell("level").textContent).toBe("-12.3");
    expect(valueCell("level").getAttribute("title")).toBe("-12.3456");
    expect(valueCell("temp").textContent).toBe("41.23");
    expect(valueCell("gain").getAttribute("title")).toBeNull();
  });

  it("keeps a child's keys for the Child Entities panel and says how many", async () => {
    setInfo({ driver_info: { child_entity_types: { channel: {}, zone: {} } } });
    setLive({
      "device.amp.channel.01.level": 3,
      "device.amp.channel.02.level": 4,
      "device.amp.zone.1.name": "Lobby",
      "device.amp.channelcount": 2,
      "device.amp.channel": "top",
    });
    await showPage();
    expect(rows().map(([k]) => k)).toEqual(["enabled", "channelcount", "channel"]);
    expect(screen.getByText(/3 child-entity state keys are shown in the/)).toBeTruthy();
    expect(screen.getByTestId("child-entities").textContent).toBe("|3|false");
  });

  it("says one child key in the singular, and a thousand with a separator", async () => {
    setInfo({ driver_info: { child_entity_types: { channel: {} } } });
    setLive({ "device.amp.channel.01.level": 3 });
    const view = await showPage();
    expect(screen.getByText(/1 child-entity state key is shown in the/)).toBeTruthy();
    view.unmount();
    const many: Record<string, unknown> = {};
    for (let i = 0; i < 1000; i++) many[`device.amp.channel.${i}.level`] = i;
    setLive(many);
    // Until the driver's child types arrive every key is a row, and a role
    // query over a thousand rows is slow: wait on the sentence instead.
    render(<DeviceDetail deviceId="amp" onEdit={() => {}} onDeleted={() => {}} onDuplicate={() => {}} />);
    expect(await screen.findByText(/1,000 child-entity state keys are shown/)).toBeTruthy();
  });

  it("says nothing about child keys when there are none", async () => {
    setLive({ "device.amp.power": "on" });
    await showPage();
    expect(screen.queryByText(/child-entity state/)).toBeNull();
    expect(screen.getByTestId("child-entities").textContent).toBe("|0|false");
  });

  it("tells the Child Entities panel when the device is held", async () => {
    setLive({ "device.amp.paused": true });
    await showPage();
    expect(screen.getByTestId("child-entities").textContent).toBe("|0|true");
  });

  it("summarises a list a command's picker reads, by label", async () => {
    const list = JSON.stringify(
      Array.from({ length: 10 }, (_, i) => ({ value: `in${i}`, label: `Input ${i}` })),
    );
    setInfo({
      commands: {
        route: { params: { input: { type: "enum", options_state: "inputs" }, level: { type: "integer" } } },
        bad: null,
        bare: {},
      },
      driver_info: {
        actions: [{ params: { preset: { options_state: "presets" } } }, null, { params: null }],
      },
    });
    setLive({
      "device.amp.inputs": list,
      "device.amp.presets": JSON.stringify([{ value: "p1", label: "Morning" }]),
      "device.amp.notes": JSON.stringify([{ value: "x", label: "Not a feed" }]),
    });
    await showPage();
    expect(valueCell("inputs").textContent).toBe(
      "10 options: Input 0, Input 1, Input 2, Input 3, Input 4, Input 5, Input 6, Input 7 (+2 more)",
    );
    expect(valueCell("inputs").querySelector("span")!.getAttribute("title")).toBe(
      "10 options: Input 0, Input 1, Input 2, Input 3, Input 4, Input 5, Input 6, Input 7, Input 8, Input 9",
    );
    expect(valueCell("presets").textContent).toBe("1 option: Morning");
    expect(valueCell("notes").textContent).toBe('[{"value":"x","label":"Not a feed"}]');
  });

  it("shows a feed that does not parse as it is", async () => {
    setInfo({ commands: { route: { params: { input: { options_state: "inputs" } } } } });
    setLive({ "device.amp.inputs": "not json" });
    await showPage();
    expect(valueCell("inputs").textContent).toBe("not json");
  });

  it("marks the driver's readings as last heard while the device is not connected", async () => {
    setInfo({ driver_info: { state_variables: { power: {}, input: {} } } });
    setLive({ "device.amp.power": "on", "device.amp.input": "", "device.amp.offline_reason": "timeout" });
    await showPage();
    expect(valueCell("power").getAttribute("title")).toBe(
      "The last value this device reported before it stopped answering. Not a current reading.",
    );
    expect(valueCell("power").style.color).toBe("var(--text-muted)");
    expect(within(row("power")).getByTestId("last-heard")).toBeTruthy();
    expect(within(row("input")).queryByTestId("last-heard")).toBeNull();
    expect(within(row("offline_reason")).queryByTestId("last-heard")).toBeNull();
  });

  it("marks nothing while the device is connected", async () => {
    setInfo({ driver_info: { state_variables: { power: {} } } });
    setLive({ "device.amp.connected": true, "device.amp.power": "on" });
    await showPage();
    expect(screen.queryByTestId("last-heard")).toBeNull();
    expect(valueCell("power").getAttribute("title")).toBeNull();
  });

  it("copies a row's full key", async () => {
    setLive({ "device.amp.power": "on" });
    await showPage();
    expect(within(row("power")).getByTitle("Copy full state key")).toBeTruthy();
  });

  describe("the filter", () => {
    it("narrows by key or by the value as shown, ignoring case and spaces around it", async () => {
      setLive({ "device.amp.power": "on", "device.amp.input": "HDMI 1", "device.amp.level": 0.123456 });
      await showPage();
      const filter = screen.getByTestId("device-filter");
      fireEvent.change(filter, { target: { value: "  POW " } });
      expect(rows().map(([k]) => k)).toEqual(["power"]);
      fireEvent.change(filter, { target: { value: "hdmi" } });
      expect(rows().map(([k]) => k)).toEqual(["input"]);
      fireEvent.change(filter, { target: { value: "0.12" } });
      expect(rows().map(([k]) => k)).toEqual(["level"]);
      fireEvent.change(filter, { target: { value: "0.1234" } });
      expect(rows()).toEqual([]);
    });

    it("says what matched nothing, and hands the words to the Child Entities panel", async () => {
      setLive({ "device.amp.power": "on" });
      await showPage();
      fireEvent.change(screen.getByTestId("device-filter"), { target: { value: " Zzz" } });
      expect(screen.getByText('No state matches " Zzz"')).toBeTruthy();
      expect(screen.getByTestId("child-entities").textContent).toBe(" Zzz|0|false");
    });

    it("treats spaces alone as no filter", async () => {
      setLive({ "device.amp.power": "on" });
      await showPage();
      fireEvent.change(screen.getByTestId("device-filter"), { target: { value: "   " } });
      expect(rows().map(([k]) => k)).toEqual(["enabled", "power"]);
    });

    it("counts a child's keys whatever the filter says", async () => {
      setInfo({ driver_info: { child_entity_types: { channel: {} } } });
      setLive({ "device.amp.channel.01.level": 3, "device.amp.channel.02.level": 4 });
      await showPage();
      fireEvent.change(screen.getByTestId("device-filter"), { target: { value: "nothing" } });
      expect(screen.getByText(/2 child-entity state keys are shown/)).toBeTruthy();
    });

    it("clears with its button, which is there only while it holds words", async () => {
      setLive({ "device.amp.power": "on" });
      await showPage();
      expect(screen.queryByTitle("Clear filter")).toBeNull();
      const filter = screen.getByTestId("device-filter") as HTMLInputElement;
      expect(filter.placeholder).toBe("Filter children and live state by id, label, name, or any value");
      fireEvent.change(filter, { target: { value: "pow" } });
      fireEvent.click(screen.getByTitle("Clear filter"));
      expect(filter.value).toBe("");
      expect(rows().map(([k]) => k)).toEqual(["enabled", "power"]);
      expect(screen.queryByTitle("Clear filter")).toBeNull();
    });
  });

  describe("Monitor", () => {
    it("starts watching a reading as the driver declares it, undoably, saved after a pause", async () => {
      const updateWithUndo = vi.fn();
      const debouncedSave = vi.fn();
      useProjectStore.setState({ updateWithUndo, debouncedSave } as never);
      setInfo({ driver_info: { state_variables: { lamp_hours: { label: "Lamp Hours", unit: "h", type: "integer" } } } });
      setLive({ "device.amp.connected": true, "device.amp.lamp_hours": 120 });
      await showPage();
      fireEvent.click(within(row("lamp_hours")).getByRole("button", { name: /Monitor/ }));
      expect(updateWithUndo).toHaveBeenCalledWith(
        { monitors: [{ key: "device.amp.lamp_hours", label: "Lamp Hours", unit: "h", type: "integer" }] },
        "Monitor device.amp.lamp_hours",
      );
      expect(debouncedSave).toHaveBeenCalledWith(1500);
    });

    it("opens one row's limits at a time, under the row, with the row's declaration", async () => {
      setProject([{ key: "device.amp.power" }, { key: "device.amp.input" }]);
      setInfo({ driver_info: { state_variables: { power: { label: "Power" } } } });
      setLive({ "device.amp.connected": true, "device.amp.power": "on", "device.amp.input": "hdmi1" });
      await showPage();
      expect(screen.queryByTestId("limits-panel")).toBeNull();
      expect(row("power").style.borderBottom).toBe("1px solid var(--border-color)");
      const opener = (key: string) =>
        within(row(key).querySelectorAll("td")[2] as HTMLElement).getAllByRole("button")[1];
      fireEvent.click(opener("power"));
      expect(screen.getAllByTestId("limits-panel").map((p) => p.textContent)).toEqual(["device.amp.power|Power"]);
      expect(row("power").style.borderBottom).toBe("");
      expect(row("power").nextElementSibling!.contains(screen.getByTestId("limits-panel"))).toBe(true);
      fireEvent.click(opener("input"));
      expect(screen.getAllByTestId("limits-panel").map((p) => p.textContent)).toEqual(["device.amp.input|"]);
      expect(row("power").style.borderBottom).toBe("1px solid var(--border-color)");
      fireEvent.click(opener("input"));
      expect(screen.queryByTestId("limits-panel")).toBeNull();
    });

    it("hands the Child Entities panel the project's monitors and the same save", async () => {
      const updateWithUndo = vi.fn();
      const debouncedSave = vi.fn();
      setProject([{ key: "device.amp.channel.01.level" }]);
      useProjectStore.setState({ updateWithUndo, debouncedSave } as never);
      await showPage();
      const props = mocks.childProps[mocks.childProps.length - 1];
      expect(props.monitors).toEqual([{ key: "device.amp.channel.01.level" }]);
      (props.onMonitorsChange as (n: unknown[], d: string) => void)([], "Stop monitoring x");
      expect(updateWithUndo).toHaveBeenCalledWith({ monitors: [] }, "Stop monitoring x");
      expect(debouncedSave).toHaveBeenCalledWith(1500);
    });

    it("passes an empty list when the project has no monitors", async () => {
      useProjectStore.setState({
        project: {
          devices: [{ id: "amp", driver: "acme_amp", name: "Rack Amp", config: {}, enabled: true }],
          connections: {}, device_groups: [], macros: [],
          ui: { pages: [], master_elements: [] },
        } as never,
      });
      await showPage();
      await waitFor(() => expect(mocks.childProps.length).toBeGreaterThan(0));
      expect(mocks.childProps[mocks.childProps.length - 1].monitors).toEqual([]);
    });
  });
});
