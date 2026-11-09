import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * The banners that say why a device is away, on its page. Exactly one of them
 * explains the device at a time, in this order of precedence: the driver is
 * not installed; Live Test or a Device Audit holds it; a command is restarting
 * it; a command put it to sleep; it waits for a login; it is offline for a
 * reason. The first four do not depend on whether the device is connected;
 * the last two need it disconnected and enabled. Where they sit on the page
 * matters too: the first four above product hardware's own panel, the last
 * two below it.
 */

const mocks = vi.hoisted(() => ({
  getDevice: vi.fn(),
  listMissingDrivers: vi.fn(),
  installCommunityDriver: vi.fn(),
  resumeDevice: vi.fn(),
}));

vi.mock("../../api/restClient", () => ({
  getDevice: mocks.getDevice,
  listMissingDrivers: mocks.listMissingDrivers,
  installCommunityDriver: mocks.installCommunityDriver,
  resumeDevice: mocks.resumeDevice,
  deleteDevice: vi.fn(),
  getDeviceSettings: vi.fn(async () => ({ settings: {} })),
  reconnectDevice: vi.fn(),
  sendCommand: vi.fn(),
  setDeviceSetting: vi.fn(),
  testDeviceConnection: vi.fn(),
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
vi.mock("./DriverUpdateLine", () => ({ DriverUpdateLine: () => null }));
vi.mock("./InstalledEquipment", () => ({
  InstalledEquipment: () => <div data-testid="installed-panel" />,
}));
vi.mock("./NeedsLoginBanner", () => ({
  NeedsLoginBanner: ({ fields }: { fields: string[] }) => (
    <div data-testid="needs-login-banner">{fields.join(",")}</div>
  ),
}));
vi.mock("./ProblemReportDialog", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./ProblemReportDialog")>()),
  ProblemReportDialog: ({ context }: { context: unknown }) => (
    <div data-testid="report-dialog">{JSON.stringify(context)}</div>
  ),
}));
vi.mock("../../components/plugins/PluginExtensions", () => ({
  DevicePanelSlot: () => null,
  ContextActionRenderer: () => null,
}));

import { useProjectStore } from "../../store/projectStore";
import { ApiError } from "../../api/errors";
import { DeviceDetail } from "./DeviceDetail";

const DETAIL = "Nothing answered at 10.0.0.9.";

function setLive(next: Record<string, unknown>) {
  for (const k of Object.keys(liveState)) delete liveState[k];
  Object.assign(liveState, { "device.amp.enabled": true, ...next });
}

function setProject(enabled = true, driver = "acme_amp") {
  useProjectStore.setState({
    project: {
      devices: [{ id: "amp", driver, name: "Rack Amp", config: {}, enabled }],
      connections: {},
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

async function showPage(props: Partial<Parameters<typeof DeviceDetail>[0]> = {}) {
  const view = render(
    <DeviceDetail
      deviceId="amp"
      onEdit={() => {}}
      onDeleted={() => {}}
      onDuplicate={() => {}}
      {...props}
    />,
  );
  await screen.findByRole("button", { name: /Test/ });
  return view;
}

/** The text outside the Live State table, which lists offline_detail too. */
function outsideTable(text: string | RegExp): HTMLElement[] {
  return screen.queryAllByText(text).filter((el) => !el.closest("table"));
}

/** Which of the away banners are on the page, by what each one says. */
function bannersShown(): string[] {
  const shown: string[] = [];
  if (screen.queryByText("Driver Not Installed")) shown.push("orphan");
  if (screen.queryByText(/^Paused/)) shown.push("paused");
  if (screen.queryByText("Restarting.")) shown.push("restarting");
  if (screen.queryByText("Asleep.")) shown.push("asleep");
  if (screen.queryByTestId("needs-login-banner")) shown.push("needs_login");
  if (screen.queryByText("Offline")) shown.push("offline");
  return shown;
}

beforeEach(() => {
  vi.clearAllMocks();
  setProject();
  setInfo();
  mocks.listMissingDrivers.mockResolvedValue([]);
  setLive({});
});

describe("which away banner explains the device", () => {
  const away = {
    "device.amp.connected": false,
    "device.amp.offline_reason": "unreachable",
    "device.amp.offline_detail": "",
  };

  it("the missing driver wins over everything else", async () => {
    setLive({
      ...away,
      "device.amp.orphaned": true,
      "device.amp.paused": true,
      "device.amp.restarting": true,
      "device.amp.asleep": true,
      "device.amp.offline_detail": DETAIL,
    });
    await showPage();
    expect(bannersShown()).toEqual(["orphan"]);
    expect(outsideTable(DETAIL)).toEqual([]);
  });

  it("a held device reads as held, not restarting, asleep or offline", async () => {
    setLive({
      ...away,
      "device.amp.paused": true,
      "device.amp.restarting": true,
      "device.amp.asleep": true,
      "device.amp.offline_detail": DETAIL,
    });
    await showPage();
    expect(bannersShown()).toEqual(["paused"]);
    expect(outsideTable(DETAIL)).toEqual([]);
  });

  it("restarting wins over asleep, and over offline", async () => {
    setLive({ ...away, "device.amp.restarting": true, "device.amp.asleep": true });
    await showPage();
    expect(bannersShown()).toEqual(["restarting"]);
  });

  it("restarting shows its sentence when the server sends one", async () => {
    setLive({ ...away, "device.amp.restarting": true, "device.amp.offline_detail": "Restarting. Back in 40 s." });
    await showPage();
    expect(outsideTable("Restarting. Back in 40 s.")).toHaveLength(1);
    expect(screen.queryByText("Offline")).toBeNull();
  });

  it("restarting and asleep show even while the device still reads connected", async () => {
    setLive({ "device.amp.connected": true, "device.amp.restarting": true });
    await showPage();
    expect(bannersShown()).toEqual(["restarting"]);
  });

  it("asleep shows while connected too, and wins over offline", async () => {
    setLive({ ...away, "device.amp.connected": true, "device.amp.asleep": true });
    await showPage();
    expect(bannersShown()).toEqual(["asleep"]);
  });

  it("a paused device that still reads connected shows the pause", async () => {
    setLive({ "device.amp.connected": true, "device.amp.paused": true, "device.amp.paused_for": "audit" });
    await showPage();
    expect(screen.getByText("Paused for a Device Audit. It reconnects when the audit finishes.")).toBeTruthy();
  });

  it("offline needs a sentence, a disconnected device and an enabled one", async () => {
    setLive({ ...away, "device.amp.offline_detail": DETAIL });
    const first = await showPage();
    expect(bannersShown()).toEqual(["offline"]);
    expect(outsideTable(DETAIL)).toHaveLength(1);
    first.unmount();

    setLive({ ...away });
    const noSentence = await showPage();
    expect(bannersShown()).toEqual([]);
    noSentence.unmount();

    setLive({ ...away, "device.amp.connected": true, "device.amp.offline_detail": DETAIL });
    const connected = await showPage();
    expect(bannersShown()).toEqual([]);
    connected.unmount();

    setProject(false);
    setLive({ ...away, "device.amp.offline_detail": DETAIL });
    await showPage();
    expect(bannersShown()).toEqual([]);
  });

  it("a device waiting for a login asks for it in place of the offline banner", async () => {
    setInfo({ needs_login: ["password"] });
    setLive({ ...away, "device.amp.offline_reason": "login_missing", "device.amp.offline_detail": DETAIL });
    await showPage();
    await screen.findByTestId("needs-login-banner");
    expect(bannersShown()).toEqual(["needs_login"]);
    expect(screen.getByTestId("needs-login-banner").textContent).toBe("password");
  });

  it("waiting for a login with no setting named shows neither banner", async () => {
    setInfo({ needs_login: [] });
    setLive({ ...away, "device.amp.offline_reason": "login_missing", "device.amp.offline_detail": DETAIL });
    await showPage();
    expect(bannersShown()).toEqual([]);
  });

  it("the login box gives way to a pause, and needs the device disconnected and enabled", async () => {
    setInfo({ needs_login: ["password"] });
    setLive({ ...away, "device.amp.offline_reason": "login_missing", "device.amp.paused": true });
    const paused = await showPage();
    expect(bannersShown()).toEqual(["paused"]);
    paused.unmount();

    setLive({ ...away, "device.amp.offline_reason": "login_missing", "device.amp.connected": true });
    const connected = await showPage();
    expect(bannersShown()).toEqual([]);
    connected.unmount();

    setProject(false);
    setLive({ ...away, "device.amp.offline_reason": "login_missing" });
    await showPage();
    expect(bannersShown()).toEqual([]);
  });
});

describe("where the away banners sit beside product hardware's panel", () => {
  function before(a: Element, b: Element): boolean {
    return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  }

  beforeEach(() => setInfo({ ownership: "manufacturer" }));

  it.each([
    ["restarting", { "device.amp.restarting": true }, "Restarting."],
    ["asleep", { "device.amp.asleep": true }, "Asleep."],
    ["paused", { "device.amp.paused": true }, "Paused. Press Resume to reconnect it."],
  ])("%s sits above it", async (_name, flags, text) => {
    setLive({ "device.amp.connected": false, ...flags });
    await showPage();
    expect(before(screen.getByText(text), screen.getByTestId("installed-panel"))).toBe(true);
  });

  it("the missing driver sits above it", async () => {
    setLive({ "device.amp.orphaned": true });
    await showPage();
    expect(before(screen.getByText("Driver Not Installed"), screen.getByTestId("installed-panel"))).toBe(true);
  });

  it("offline and the login box sit below it", async () => {
    setLive({ "device.amp.connected": false, "device.amp.offline_reason": "unreachable", "device.amp.offline_detail": DETAIL });
    const offline = await showPage();
    expect(before(screen.getByTestId("installed-panel"), screen.getByText("Offline"))).toBe(true);
    offline.unmount();

    setInfo({ ownership: "manufacturer", needs_login: ["password"] });
    setLive({ "device.amp.connected": false, "device.amp.offline_reason": "login_missing" });
    await showPage();
    const box = await screen.findByTestId("needs-login-banner");
    expect(before(screen.getByTestId("installed-panel"), box)).toBe(true);
  });

  it("children in trouble sit above it", async () => {
    setInfo({ ownership: "manufacturer", driver_info: { child_entity_types: { port: { label: "Port", label_plural: "Ports" } } } });
    setLive({ "device.amp.connected": true, "device.amp.port.01.online": false, "device.amp.port.02.online": true });
    await showPage();
    const banner = await screen.findByTestId("child-trouble-banner");
    expect(before(banner, screen.getByTestId("installed-panel"))).toBe(true);
  });
});

describe("the Offline banner", () => {
  const offline = {
    "device.amp.connected": false,
    "device.amp.offline_reason": "connection_refused",
    "device.amp.offline_detail": DETAIL,
  };

  it("shows the driver's own troubleshooting line under the reason", async () => {
    setInfo({ driver_info: { help: { connection: "Turn on network control\nin the menu." } } });
    setLive(offline);
    await showPage();
    const hint = await screen.findByText(/Turn on network control/);
    expect(hint.style.whiteSpace).toBe("pre-line");
  });

  it("says it keeps reconnecting until the retries stop", async () => {
    setLive(offline);
    await showPage();
    expect(screen.getByText(/Reconnecting automatically, and will keep trying/)).toBeTruthy();
  });

  it("points a refused sign-in at Edit when the driver has a sign-in field", async () => {
    setInfo({ driver_info: { config_schema: { password: { type: "string", secret: true } } } });
    setLive({ ...offline, "device.amp.offline_reason": "auth_failed", "device.amp.reconnect_failed": true });
    await showPage();
    expect(await screen.findByText(/the sign-in details are under Edit/)).toBeTruthy();
  });

  it("says a refused sign-in is not retried, without Edit, when there is nothing to sign in with", async () => {
    setLive({ ...offline, "device.amp.offline_reason": "auth_failed", "device.amp.reconnect_failed": true });
    await showPage();
    expect(screen.getByText(/Not retrying, so repeated sign-ins/)).toBeTruthy();
    expect(screen.queryByText(/under Edit/)).toBeNull();
  });

  it("product hardware that stopped retrying points at its own status", async () => {
    setInfo({ ownership: "manufacturer" });
    setLive({ ...offline, "device.amp.reconnect_failed": true });
    await showPage();
    expect(
      screen.getByText(/Automatic reconnect is paused\. Check this product's OpenAVC status, then press Reconnect\./),
    ).toBeTruthy();
  });

  it("product hardware with no simulator says what simulation does instead", async () => {
    setInfo({ ownership: "manufacturer" });
    setLive({ ...offline, "device.amp.offline_reason": "no_simulator", "device.amp.reconnect_failed": true });
    await showPage();
    expect(screen.getByText(/Not connecting while simulation runs/)).toBeTruthy();
    expect(screen.queryByText(/Automatic reconnect is paused/)).toBeNull();
  });

  it("offers Report a problem when the driver could be at fault, with the banner's words", async () => {
    setLive(offline);
    await showPage();
    const reports = screen.getAllByRole("button", { name: "Report a problem" });
    expect(reports).toHaveLength(2);
    fireEvent.click(reports[1]);
    expect(JSON.parse(screen.getByTestId("report-dialog").textContent ?? "")).toEqual({
      source: "offline_banner",
      error: `Offline. ${DETAIL}`,
    });
  });

  it("offers no Report a problem for a fault no driver change fixes", async () => {
    setLive({ ...offline, "device.amp.offline_reason": "unreachable" });
    await showPage();
    expect(screen.getAllByRole("button", { name: "Report a problem" })).toHaveLength(1);
  });
});

describe("the paused banner", () => {
  it("names who holds it and resumes the device", async () => {
    mocks.resumeDevice.mockResolvedValue({});
    setLive({ "device.amp.paused": true, "device.amp.paused_for": "live_test" });
    await showPage();
    expect(screen.getByText(/Paused for Live Test in the Driver Builder/)).toBeTruthy();
    fireEvent.click(screen.getByTitle("Clear the pause and reconnect now"));
    expect(mocks.resumeDevice).toHaveBeenCalledWith("amp");
  });

  it("keeps the page when the resume is refused", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    mocks.resumeDevice.mockRejectedValue(new Error("nope"));
    setLive({ "device.amp.paused": true });
    await showPage();
    fireEvent.click(screen.getByRole("button", { name: /Resume/ }));
    await waitFor(() => expect(err).toHaveBeenCalled());
    expect(screen.getByText("Paused. Press Resume to reconnect it.")).toBeTruthy();
    err.mockRestore();
  });
});

describe("the banner for children in trouble", () => {
  const types = { port: { label: "Port", label_plural: "Ports", label_field: "name" } };

  it("names the children that are not answering while the device is up", async () => {
    setInfo({ driver_info: { child_entity_types: types } });
    setLive({
      "device.amp.connected": true,
      "device.amp.port.01.online": false,
      "device.amp.port.01.name": "Podium",
      "device.amp.port.02.online": true,
      "device.amp.port.03.online": false,
    });
    await showPage();
    const banner = await screen.findByTestId("child-trouble-banner");
    expect(banner.textContent).toContain("2 of 3 ports");
    expect(banner.textContent).toContain("Podium, 03");
  });

  it("is not shown while the device itself is offline", async () => {
    setInfo({ driver_info: { child_entity_types: types } });
    setLive({ "device.amp.connected": false, "device.amp.port.01.online": false });
    await showPage();
    expect(screen.queryByTestId("child-trouble-banner")).toBeNull();
  });
});

describe("the missing-driver banner", () => {
  const orphaned = { "device.amp.orphaned": true };

  it("installs the driver from the community catalog and reads the device again", async () => {
    let finish: (v: unknown) => void = () => {};
    mocks.installCommunityDriver.mockReturnValue(new Promise((r) => { finish = r; }));
    mocks.listMissingDrivers.mockResolvedValue([
      { driver_id: "acme_amp", community_match: { file_url: "https://x/acme_amp.avcdriver", min_platform_version: "0.30.0" } },
    ]);
    setLive(orphaned);
    await showPage();
    fireEvent.click(await screen.findByRole("button", { name: "Install from Community" }));
    expect(mocks.installCommunityDriver).toHaveBeenCalledWith("acme_amp", "https://x/acme_amp.avcdriver", "0.30.0");
    expect(screen.getByRole("button", { name: "Installing..." })).toBeTruthy();
    const reads = mocks.getDevice.mock.calls.length;
    await act(async () => finish({}));
    await waitFor(() => expect(mocks.getDevice.mock.calls.length).toBe(reads + 1));
    expect(screen.getByRole("button", { name: "Install from Community" })).toBeTruthy();
  });

  it("sends no minimum version when the catalog names none", async () => {
    mocks.installCommunityDriver.mockResolvedValue({});
    mocks.listMissingDrivers.mockResolvedValue([
      { driver_id: "acme_amp", community_match: { file_url: "u", min_platform_version: null } },
    ]);
    setLive(orphaned);
    await showPage();
    fireEvent.click(await screen.findByRole("button", { name: "Install from Community" }));
    expect(mocks.installCommunityDriver).toHaveBeenCalledWith("acme_amp", "u", undefined);
  });

  it("says why an install failed", async () => {
    mocks.installCommunityDriver.mockRejectedValue(new ApiError(400, JSON.stringify({ detail: "Needs a newer OpenAVC." })));
    mocks.listMissingDrivers.mockResolvedValue([
      { driver_id: "acme_amp", community_match: { file_url: "u", min_platform_version: null } },
    ]);
    setLive(orphaned);
    await showPage();
    fireEvent.click(await screen.findByRole("button", { name: "Install from Community" }));
    expect(await screen.findByText("Install failed: Needs a newer OpenAVC.")).toBeTruthy();
  });

  it("says when the catalog has no such driver, and offers Browse Drivers", async () => {
    const onBrowseDrivers = vi.fn();
    setLive(orphaned);
    await showPage({ onBrowseDrivers });
    expect(screen.getByText('This device needs the driver "acme_amp" which is not installed.')).toBeTruthy();
    expect(await screen.findByText(/This driver isn't in the community catalog/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Browse Drivers" }));
    expect(onBrowseDrivers).toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Install from Community" })).toBeNull();
  });

  it("offers no Browse Drivers when the page has nowhere to send it", async () => {
    setLive(orphaned);
    await showPage();
    await screen.findByText(/This driver isn't in the community catalog/);
    expect(screen.queryByRole("button", { name: "Browse Drivers" })).toBeNull();
  });

  it("reads a failed catalog lookup as no match", async () => {
    mocks.listMissingDrivers.mockRejectedValue(new Error("offline"));
    setLive(orphaned);
    await showPage();
    expect(await screen.findByText(/This driver isn't in the community catalog/)).toBeTruthy();
  });

  it("names product hardware as unavailable, with no catalog advice", async () => {
    mocks.listMissingDrivers.mockResolvedValue([{ driver_id: "acme_amp", built_in_equipment: true }]);
    setLive(orphaned);
    await showPage({ onBrowseDrivers: () => {} });
    expect(await screen.findByText("Product hardware unavailable")).toBeTruthy();
    expect(screen.queryByText(/community catalog/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Browse Drivers" })).toBeNull();
  });

  it("Reassign Driver opens the device's settings", async () => {
    const onEdit = vi.fn();
    setLive(orphaned);
    await showPage({ onEdit });
    fireEvent.click(screen.getByRole("button", { name: "Reassign Driver" }));
    expect(onEdit).toHaveBeenCalledWith(expect.objectContaining({ id: "amp", driver: "acme_amp" }));
  });

  it("asks the catalog about this device's own driver", async () => {
    setProject(true, "acme_other");
    setLive(orphaned);
    await showPage();
    await waitFor(() => expect(mocks.listMissingDrivers).toHaveBeenCalled());
    expect(await screen.findByText('This device needs the driver "acme_other" which is not installed.')).toBeTruthy();
  });
});
