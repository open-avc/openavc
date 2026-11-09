import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";

/**
 * What a device's driver brings to its page beyond the readings: the editors
 * it opts in to (the no-code protocol editor, the IR code set, a row editor for
 * each table setting), drawn between Device Settings and Send Command, and
 * OpenAVC's own SSH key for a device that connects over SSH and can sign in
 * with a key.
 */

const mocks = vi.hoisted(() => ({
  getDevice: vi.fn(),
  getDeviceSettings: vi.fn(),
  getSshPublicKey: vi.fn(),
  editors: [] as { kind: string; props: Record<string, unknown> }[],
  installed: [] as Record<string, unknown>[],
}));

vi.mock("../../api/restClient", () => ({
  getDevice: mocks.getDevice,
  deleteDevice: vi.fn(),
  reconnectDevice: vi.fn(),
  testDeviceConnection: vi.fn(),
  getDeviceSettings: mocks.getDeviceSettings,
  installCommunityDriver: vi.fn(),
  listMissingDrivers: vi.fn(() => new Promise(() => {})),
  resumeDevice: vi.fn(),
  sendCommand: vi.fn(),
  setDeviceSetting: vi.fn(),
}));
vi.mock("../../api/auditClient", () => ({ getAuditDeviceTarget: vi.fn() }));
vi.mock("../../api/deviceClient", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/deviceClient")>()),
  getSshPublicKey: mocks.getSshPublicKey,
}));

const liveState: Record<string, unknown> = {};
vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: unknown) => unknown) => selector({ liveState }),
}));

vi.mock("./ChildEntities", () => ({ ChildEntities: () => null }));
vi.mock("./DeviceStateLog", () => ({ DeviceStateLog: () => null }));
vi.mock("./DeviceProtocolLog", () => ({ DeviceProtocolLog: () => null }));
vi.mock("./QuickActions", () => ({ QuickActions: () => null }));
vi.mock("./InstalledEquipment", () => ({
  InstalledEquipment: (props: Record<string, unknown>) => {
    mocks.installed.push(props);
    return <div data-testid="installed-panel" />;
  },
}));
vi.mock("./DriverUpdateLine", () => ({ DriverUpdateLine: () => null }));
vi.mock("../../components/plugins/PluginExtensions", () => ({
  DevicePanelSlot: () => null,
  ContextActionRenderer: () => null,
}));
vi.mock("./InlineProtocolEditor", () => ({
  InlineProtocolEditor: (props: Record<string, unknown>) => {
    mocks.editors.push({ kind: "inline", props });
    return <div data-testid="editor-inline" />;
  },
}));
vi.mock("./IrCodesEditor", () => ({
  IrCodesEditor: (props: Record<string, unknown>) => {
    mocks.editors.push({ kind: "ir", props });
    return <div data-testid="editor-ir" />;
  },
}));
vi.mock("./ConfigTableEditor", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./ConfigTableEditor")>()),
  ConfigTableEditor: (props: Record<string, unknown>) => {
    mocks.editors.push({ kind: "table", props });
    return <div data-testid={`editor-table-${String(props.fieldKey)}`} />;
  },
}));

import { useProjectStore } from "../../store/projectStore";
import type { DriverInfo } from "../../api/types";
import { DeviceDetail } from "./DeviceDetail";

function setLive(next: Record<string, unknown>) {
  for (const k of Object.keys(liveState)) delete liveState[k];
  Object.assign(liveState, { "device.amp.enabled": true, ...next });
}

function setProject(config: Record<string, unknown> = {}, connection?: Record<string, unknown>) {
  useProjectStore.setState({
    project: {
      devices: [{ id: "amp", driver: "acme_amp", name: "Rack Amp", config, enabled: true }],
      connections: connection ? { amp: connection } : {},
      device_groups: [], macros: [], monitors: [],
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

async function showPage(installedDriver?: Record<string, unknown>) {
  const view = render(
    <DeviceDetail
      deviceId="amp"
      onEdit={() => {}}
      onDeleted={() => {}}
      onDuplicate={() => {}}
      installedDriver={installedDriver as DriverInfo | undefined}
    />,
  );
  await screen.findByRole("button", { name: /Test/ });
  await act(async () => { await Promise.resolve(); });
  return view;
}

function before(a: Element, b: Element): boolean {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.editors.length = 0;
  mocks.installed.length = 0;
  mocks.getDeviceSettings.mockResolvedValue({ settings: {} });
  mocks.getSshPublicKey.mockReturnValue(new Promise(() => {}));
  setProject();
  setInfo();
  setLive({});
});

describe("the editors a driver opts in to", () => {
  function editorsShown(): string[] {
    return screen.queryAllByTestId(/^editor-/).map((el) => el.dataset.testid ?? "");
  }

  it("draws none for a driver that asks for none", async () => {
    setInfo({
      driver_info: {
        inline_protocol: "true",
        ir_codes: 1,
        config_schema: { host: { type: "string" }, rows: { type: "list" } },
      },
    });
    await showPage();
    expect(editorsShown()).toEqual([]);
  });

  it("draws the no-code protocol editor with the driver's own declaration", async () => {
    const driverInfo = { inline_protocol: true, commands: { on: { send: "ON" } } };
    setInfo({ driver_info: driverInfo });
    setLive({ "device.amp.connected": true });
    await showPage();
    expect(editorsShown()).toEqual(["editor-inline"]);
    const last = mocks.editors.filter((e) => e.kind === "inline").at(-1)!;
    expect(last.props).toEqual({
      deviceId: "amp", driverInfo, connected: true, onSaved: expect.any(Function),
    });
  });

  it("draws the IR code set for a driver that opts in", async () => {
    setInfo({ driver_info: { ir_codes: true } });
    await showPage();
    expect(editorsShown()).toEqual(["editor-ir"]);
    const last = mocks.editors.filter((e) => e.kind === "ir").at(-1)!;
    expect(last.props).toEqual({ deviceId: "amp", connected: false, onSaved: expect.any(Function) });
  });

  it("draws one row editor per table setting, in the order the driver lists them", async () => {
    const registers = { type: "table", label: "Registers", columns: { addr: { type: "integer" } } };
    const scenes = { type: "table", label: "Scenes" };
    setInfo({
      driver_info: { config_schema: { registers, host: { type: "string" }, scenes, port: { type: "integer" } } },
    });
    await showPage();
    expect(editorsShown()).toEqual(["editor-table-registers", "editor-table-scenes"]);
    const tables = mocks.editors.filter((e) => e.kind === "table");
    expect(tables.at(-2)!.props).toEqual({
      deviceId: "amp", fieldKey: "registers", fieldSchema: registers, connected: false,
      onSaved: expect.any(Function),
    });
    expect(tables.at(-1)!.props).toMatchObject({ fieldKey: "scenes", fieldSchema: scenes });
  });

  it("tells each of them whether the device is connected", async () => {
    setInfo({ driver_info: { inline_protocol: true, ir_codes: true, config_schema: { scenes: { type: "table" } } } });
    setLive({ "device.amp.connected": true });
    await showPage();
    for (const kind of ["inline", "ir", "table"]) {
      expect(mocks.editors.filter((e) => e.kind === kind).at(-1)!.props.connected).toBe(true);
    }
  });

  it("draws them in a fixed order, below Device Settings and above Send Command", async () => {
    mocks.getDeviceSettings.mockResolvedValue({
      settings: { volume: { label: "Volume", type: "integer", current_value: 1 } },
    });
    setInfo({
      commands: { power_on: { label: "Power On" } },
      driver_info: {
        config_schema: { scenes: { type: "table" } },
        ir_codes: true,
        inline_protocol: true,
      },
    });
    await showPage();
    const settings = await screen.findByRole("heading", { name: /Device Settings/ });
    expect(editorsShown()).toEqual(["editor-inline", "editor-ir", "editor-table-scenes"]);
    expect(before(settings, screen.getByTestId("editor-inline"))).toBe(true);
    expect(before(screen.getByTestId("editor-table-scenes"), screen.getByRole("heading", { name: "Send Command" }))).toBe(true);
  });

  it("reads the device again when any of them saves", async () => {
    setInfo({ driver_info: { inline_protocol: true, ir_codes: true, config_schema: { scenes: { type: "table" } } } });
    await showPage();
    for (const kind of ["inline", "ir", "table"]) {
      const reads = mocks.getDevice.mock.calls.length;
      const onSaved = mocks.editors.filter((e) => e.kind === kind).at(-1)!.props.onSaved as () => void;
      await act(async () => onSaved());
      await waitFor(() => expect(mocks.getDevice.mock.calls.length).toBe(reads + 1));
      expect(mocks.getDevice).toHaveBeenLastCalledWith("amp");
    }
  });
});

describe("product hardware's own panel", () => {
  it("gets the device, the name it goes by and the wait the running system gives", async () => {
    setInfo({ ownership: "manufacturer", completion_wait: { default: 5, max: 30 } });
    setLive({ "device.amp.name": "Lobby Amp" });
    await showPage();
    expect(screen.getByTestId("installed-panel")).toBeTruthy();
    expect(mocks.installed.at(-1)).toEqual({ deviceId: "amp", name: "Lobby Amp", completionWait: { default: 5, max: 30 } });
  });

  it("is not drawn for a device that is not product hardware", async () => {
    await showPage();
    expect(screen.queryByTestId("installed-panel")).toBeNull();
    expect(mocks.installed).toEqual([]);
  });
});

describe("the line for children in trouble", () => {
  it("sits below a pause, which can hold a device that still reads connected", async () => {
    setInfo({ driver_info: { child_entity_types: { port: { label: "Port", label_plural: "Ports" } } } });
    setLive({
      "device.amp.connected": true,
      "device.amp.paused": true,
      "device.amp.port.01.online": false,
    });
    await showPage();
    const line = await screen.findByTestId("child-trouble-banner");
    expect(line.textContent).toContain("1 of 1 port");
    expect(before(screen.getByText(/^Paused/), line)).toBe(true);
  });
});

describe("OpenAVC's SSH key on the page", () => {
  const offersKey = {
    config_schema: { ssh_auth_method: { type: "enum", values: ["password", "key"] } },
    default_config: { ssh_auth_method: "password" },
  };
  const passwordOnly = {
    config_schema: { username: { type: "string" } },
    default_config: { ssh_auth_method: "password" },
  };
  /** A driver that offers no choice and names no default: the device's own
   *  setting decides. */
  const choiceFromDevice = { config_schema: { username: { type: "string" } } };

  const shown = () => screen.queryByTestId("ssh-key-section") !== null;

  it("shows for an SSH device whose driver offers key sign-in, below the banners and above the filter", async () => {
    setInfo({ connection: { transport: "ssh" }, driver_info: offersKey });
    setLive({
      "device.amp.connected": false,
      "device.amp.offline_reason": "connection_refused",
      "device.amp.offline_detail": "Refused.",
    });
    await showPage();
    const card = screen.getByTestId("ssh-key-section");
    expect(card.textContent).toContain("OpenAVC SSH key");
    expect(before(screen.getByText("Offline"), card)).toBe(true);
    expect(before(card, screen.getByPlaceholderText(/Filter children and live state/))).toBe(true);
  });

  it("does not show on another transport", async () => {
    setInfo({ connection: { transport: "tcp" }, driver_info: offersKey });
    await showPage();
    expect(shown()).toBe(false);
  });

  it("does not show for a password-only driver", async () => {
    setInfo({ connection: { transport: "ssh" }, driver_info: passwordOnly });
    await showPage();
    expect(shown()).toBe(false);
  });

  it("asks the running driver, whatever the installed one says", async () => {
    setInfo({ connection: { transport: "ssh" }, driver_info: passwordOnly });
    const off = await showPage(offersKey);
    expect(shown()).toBe(false);
    off.unmount();

    setInfo({ connection: { transport: "ssh" }, driver_info: offersKey });
    await showPage(passwordOnly);
    expect(shown()).toBe(true);
  });

  it("asks the installed driver while nothing runs the device", async () => {
    setInfo({ connection: { transport: "ssh" }, driver_info: {} });
    const on = await showPage(offersKey);
    expect(shown()).toBe(true);
    on.unmount();

    await showPage(passwordOnly);
    expect(shown()).toBe(false);
  });

  it("reads the connections row's sign-in method over the device's own setting", async () => {
    setInfo({ connection: { transport: "ssh" }, driver_info: choiceFromDevice });
    setProject({ ssh_auth_method: "key" }, { ssh_auth_method: "password" });
    const off = await showPage();
    expect(shown()).toBe(false);
    off.unmount();

    setProject({ ssh_auth_method: "password" }, { ssh_auth_method: "key" });
    const on = await showPage();
    expect(shown()).toBe(true);
    on.unmount();

    setProject({ ssh_auth_method: "password" });
    await showPage();
    expect(shown()).toBe(false);
  });
});
