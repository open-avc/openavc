import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * The device page's header: the actions on a device (enable, Test, Audit this
 * device, Reconnect, Edit, Duplicate, Delete) and the line under its name,
 * and the two lines a check leaves on the page below the banners (why an
 * audit could not start, what Test Connection found).
 */

const mocks = vi.hoisted(() => ({
  getDevice: vi.fn(),
  deleteDevice: vi.fn(),
  reconnectDevice: vi.fn(),
  testDeviceConnection: vi.fn(),
  getAuditDeviceTarget: vi.fn(),
}));

vi.mock("../../api/restClient", () => ({
  getDevice: mocks.getDevice,
  deleteDevice: mocks.deleteDevice,
  reconnectDevice: mocks.reconnectDevice,
  testDeviceConnection: mocks.testDeviceConnection,
  getDeviceSettings: vi.fn(async () => ({ settings: {} })),
  installCommunityDriver: vi.fn(),
  listMissingDrivers: vi.fn(() => new Promise(() => {})),
  resumeDevice: vi.fn(),
  sendCommand: vi.fn(),
  setDeviceSetting: vi.fn(),
}));
vi.mock("../../api/auditClient", () => ({ getAuditDeviceTarget: mocks.getAuditDeviceTarget }));

const liveState: Record<string, unknown> = {};
vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: unknown) => unknown) => selector({ liveState }),
}));

vi.mock("./ChildEntities", () => ({ ChildEntities: () => null }));
vi.mock("./DeviceStateLog", () => ({ DeviceStateLog: () => null }));
vi.mock("./DeviceProtocolLog", () => ({ DeviceProtocolLog: () => null }));
vi.mock("./QuickActions", () => ({ QuickActions: () => null }));
vi.mock("./InstalledEquipment", () => ({ InstalledEquipment: () => null }));
vi.mock("./DriverUpdateLine", () => ({
  DriverUpdateLine: ({ driverId, onUpdated }: { driverId: string; onUpdated: () => void }) => (
    <button data-testid="driver-update-line" onClick={onUpdated}>{driverId}</button>
  ),
}));
vi.mock("./ProblemReportDialog", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./ProblemReportDialog")>()),
  ProblemReportDialog: ({ context, onClose }: { context: unknown; onClose: () => void }) => (
    <div data-testid="report-dialog">
      {JSON.stringify(context)}
      <button onClick={onClose}>close report</button>
    </div>
  ),
}));
vi.mock("../../components/plugins/PluginExtensions", () => ({
  DevicePanelSlot: () => null,
  ContextActionRenderer: () => null,
}));

import { useProjectStore } from "../../store/projectStore";
import { useAuditStore } from "../../store/auditStore";
import { ApiError } from "../../api/errors";
import { DeviceDetail } from "./DeviceDetail";

const amp = { id: "amp", driver: "acme_amp", name: "Rack Amp", config: {}, enabled: true };

function setLive(next: Record<string, unknown>) {
  for (const k of Object.keys(liveState)) delete liveState[k];
  Object.assign(liveState, { "device.amp.enabled": true, ...next });
}

function setProject(devices: Record<string, unknown>[] = [amp], extra: Record<string, unknown> = {}) {
  useProjectStore.setState({
    project: {
      devices,
      connections: {},
      device_groups: [], macros: [], monitors: [], triggers: [], scripts: [],
      ui: { pages: [], master_elements: [] },
      ...extra,
    } as never,
  });
}

function setInfo(extra: Record<string, unknown> = {}) {
  mocks.getDevice.mockResolvedValue({
    id: "amp", name: "Rack Amp", driver: "acme_amp", connected: false,
    enabled: true, commands: {}, driver_info: {}, ...extra,
  });
}

function showPage(props: Partial<Parameters<typeof DeviceDetail>[0]> = {}) {
  const handlers = {
    onEdit: vi.fn(),
    onDeleted: vi.fn(),
    onDuplicate: vi.fn(),
  };
  const view = render(<DeviceDetail deviceId="amp" {...handlers} {...props} />);
  return { ...handlers, ...view };
}

const button = (name: RegExp | string) => screen.getByRole("button", { name });
const noButton = (name: RegExp | string) => expect(screen.queryByRole("button", { name })).toBeNull();

describe("the device page's header", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setLive({});
    setProject();
    setInfo();
    mocks.deleteDevice.mockResolvedValue({ status: "deleted" });
    mocks.reconnectDevice.mockResolvedValue({ status: "ok" });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("names the device from live state, then the project, then its id", async () => {
    setLive({ "device.amp.name": "Live Name" });
    const { unmount } = showPage();
    expect(screen.getByRole("heading", { name: "Live Name" })).toBeTruthy();
    unmount();
    setLive({});
    const second = showPage();
    expect(screen.getByRole("heading", { name: "Rack Amp" })).toBeTruthy();
    second.unmount();
    setProject([]);
    showPage();
    expect(screen.getByRole("heading", { name: "amp" })).toBeTruthy();
  });

  it("offers the actions on a running device only once it has answered", async () => {
    mocks.getDevice.mockReturnValue(new Promise(() => {}));
    showPage();
    noButton(/Enabled|Disabled/);
    noButton(/^Test/);
    noButton(/Audit this device/);
    noButton(/Edit/);
  });

  it("draws the actions in their order", async () => {
    showPage();
    await screen.findByRole("button", { name: /^Test/ });
    const header = screen.getByRole("heading", { name: "Rack Amp" }).parentElement!;
    const names = Array.from(header.querySelectorAll("button")).map((b) => b.textContent?.trim());
    expect(names).toEqual(["Enabled", "Test", "Audit this device", "Reconnect", "Edit", "Duplicate", "Delete"]);
  });

  it("turns an enabled device off in the project and saves it", async () => {
    const save = vi.fn();
    useProjectStore.setState({ debouncedSave: save } as never);
    showPage();
    const toggle = await screen.findByRole("button", { name: /Enabled/ });
    expect(toggle.getAttribute("title")).toBe("Disable device");
    fireEvent.click(toggle);
    const devices = useProjectStore.getState().project!.devices;
    expect(devices[0].enabled).toBe(false);
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith();
    expect(await screen.findByRole("button", { name: /Disabled/ })).toBeTruthy();
    expect(button(/Disabled/).getAttribute("title")).toBe("Enable device");
  });

  it("turns a disabled device on, leaving the other devices as they were", async () => {
    const save = vi.fn();
    const other = { id: "tv", driver: "acme_tv", name: "TV", config: {}, enabled: false };
    setProject([{ ...amp, enabled: false }, other]);
    useProjectStore.setState({ debouncedSave: save } as never);
    showPage();
    fireEvent.click(await screen.findByRole("button", { name: /Disabled/ }));
    const devices = useProjectStore.getState().project!.devices;
    expect(devices[0].enabled).toBe(true);
    expect(devices[1]).toBe(other);
  });

  it("does nothing on the toggle for a device the project does not hold", async () => {
    const save = vi.fn();
    setProject([]);
    useProjectStore.setState({ debouncedSave: save } as never);
    showPage();
    fireEvent.click(await screen.findByRole("button", { name: /Enabled/ }));
    expect(save).not.toHaveBeenCalled();
  });

  describe("Test", () => {
    it("says it is testing, then what it found, with the note under it", async () => {
      let answer!: (v: unknown) => void;
      mocks.testDeviceConnection.mockReturnValue(new Promise((r) => { answer = r; }));
      showPage();
      fireEvent.click(await screen.findByRole("button", { name: /^Test/ }));
      expect(mocks.testDeviceConnection).toHaveBeenCalledWith("amp");
      const busy = button(/Testing\.\.\./);
      expect((busy as HTMLButtonElement).disabled).toBe(true);
      expect(busy.style.opacity).toBe("0.6");
      await act(async () => {
        answer({
          success: true, latency_ms: 12, note: "The simulator answered.",
          connection: { transport: "tcp", host: "10.0.0.5", port: 4000 },
        });
      });
      expect(screen.getByText("Connected to 10.0.0.5:4000 (12 ms)")).toBeTruthy();
      expect(screen.getByText("The simulator answered.")).toBeTruthy();
      expect((button(/^Test$/) as HTMLButtonElement).disabled).toBe(false);
      const line = screen.getByText("Connected to 10.0.0.5:4000 (12 ms)");
      expect(line.style.color).toBe("var(--color-success)");
    });

    it("names a bridge by its project name", async () => {
      setProject([amp, { id: "itach", driver: "acme_bridge", name: "Rack iTach", config: {}, enabled: true }]);
      mocks.testDeviceConnection.mockResolvedValue({
        success: true, latency_ms: 3,
        connection: { transport: "tcp", host: "10.0.0.7", port: 4999, bridge: "itach" },
      });
      showPage();
      fireEvent.click(await screen.findByRole("button", { name: /^Test/ }));
      expect(await screen.findByText("Connected to 10.0.0.7:4999 through Rack iTach (3 ms)")).toBeTruthy();
    });

    it("shows the server's sentence when the test fails", async () => {
      mocks.testDeviceConnection.mockResolvedValue({
        success: false, error: "Nothing answered at 10.0.0.5:4000.", latency_ms: null,
      });
      showPage();
      fireEvent.click(await screen.findByRole("button", { name: /^Test/ }));
      const line = await screen.findByText("Nothing answered at 10.0.0.5:4000.");
      expect(line.style.color).toBe("var(--color-error)");
    });

    it("shows a refused request's sentence as a failed test", async () => {
      mocks.testDeviceConnection.mockRejectedValue(
        new ApiError(500, JSON.stringify({ detail: "The device is not running." })),
      );
      showPage();
      fireEvent.click(await screen.findByRole("button", { name: /^Test/ }));
      expect(await screen.findByText("The device is not running.")).toBeTruthy();
      expect((button(/^Test$/) as HTMLButtonElement).disabled).toBe(false);
    });

    it("clears the last result when a new test starts", async () => {
      mocks.testDeviceConnection.mockResolvedValueOnce({
        success: false, error: "First failure.", latency_ms: null,
      });
      showPage();
      fireEvent.click(await screen.findByRole("button", { name: /^Test/ }));
      await screen.findByText("First failure.");
      mocks.testDeviceConnection.mockReturnValue(new Promise(() => {}));
      fireEvent.click(button(/^Test$/));
      expect(screen.queryByText("First failure.")).toBeNull();
    });

    it("drops the result when the device connects", async () => {
      mocks.testDeviceConnection.mockResolvedValue({
        success: false, error: "Refused.", latency_ms: null,
      });
      const view = showPage();
      fireEvent.click(await screen.findByRole("button", { name: /^Test/ }));
      await screen.findByText("Refused.");
      setLive({ "device.amp.connected": true });
      view.rerender(<DeviceDetail deviceId="amp" onEdit={view.onEdit} onDeleted={view.onDeleted} onDuplicate={view.onDuplicate} />);
      await waitFor(() => expect(screen.queryByText("Refused.")).toBeNull());
    });
  });

  describe("Audit this device", () => {
    it("opens the audit on the device's address", async () => {
      const openWizard = vi.fn();
      useAuditStore.setState({ openWizard } as never);
      mocks.getAuditDeviceTarget.mockResolvedValue({ auditable: true, address: "10.0.0.5", reason: "" });
      showPage();
      const audit = await screen.findByRole("button", { name: /Audit this device/ });
      expect(audit.getAttribute("title")).toBe("Check this device in detail and write a report about it");
      fireEvent.click(audit);
      await waitFor(() => expect(openWizard).toHaveBeenCalledWith({ address: "10.0.0.5", deviceId: "amp" }));
      expect(mocks.getAuditDeviceTarget).toHaveBeenCalledWith("amp");
      expect(screen.queryByRole("status")).toBeNull();
    });

    it("says why it cannot, and the line can be dismissed", async () => {
      const openWizard = vi.fn();
      useAuditStore.setState({ openWizard } as never);
      mocks.getAuditDeviceTarget.mockResolvedValue({
        auditable: false, address: "", reason: "A device on a serial port cannot be audited.",
      });
      showPage();
      fireEvent.click(await screen.findByRole("button", { name: /Audit this device/ }));
      const line = await screen.findByRole("status");
      expect(line.textContent).toBe("A device on a serial port cannot be audited.");
      expect(openWizard).not.toHaveBeenCalled();
      fireEvent.click(button("Dismiss"));
      expect(screen.queryByRole("status")).toBeNull();
    });

    it("says what went wrong when the request fails, and is busy while it runs", async () => {
      let fail!: (e: unknown) => void;
      mocks.getAuditDeviceTarget.mockReturnValue(new Promise((_, r) => { fail = r; }));
      showPage();
      fireEvent.click(await screen.findByRole("button", { name: /Audit this device/ }));
      const audit = button(/Audit this device/) as HTMLButtonElement;
      expect(audit.disabled).toBe(true);
      expect(audit.style.opacity).toBe("0.6");
      await act(async () => {
        fail(new ApiError(409, JSON.stringify({ detail: "An audit is already running." })));
      });
      expect((await screen.findByRole("status")).textContent).toBe("An audit is already running.");
      expect((button(/Audit this device/) as HTMLButtonElement).disabled).toBe(false);
    });

    it("clears an earlier refusal when it is asked again", async () => {
      mocks.getAuditDeviceTarget.mockResolvedValueOnce({ auditable: false, address: "", reason: "Not now." });
      showPage();
      fireEvent.click(await screen.findByRole("button", { name: /Audit this device/ }));
      await screen.findByText("Not now.");
      mocks.getAuditDeviceTarget.mockReturnValue(new Promise(() => {}));
      fireEvent.click(button(/Audit this device/));
      expect(screen.queryByText("Not now.")).toBeNull();
    });

    it("is not offered for product hardware", async () => {
      setInfo({ ownership: "manufacturer" });
      showPage();
      const audit = (await screen.findByRole("button", { name: /Audit this device/ })) as HTMLButtonElement;
      expect(audit.disabled).toBe(true);
      expect(audit.getAttribute("title")).toBe(
        "Product hardware uses a local connection. Network audit is unavailable for this connection.",
      );
    });
  });

  describe("Reconnect", () => {
    it("is offered only for an enabled device that is not connected", async () => {
      setLive({ "device.amp.connected": true });
      const view = showPage();
      await screen.findByRole("button", { name: /^Test/ });
      noButton(/Reconnect/);
      view.unmount();
      setLive({});
      setProject([{ ...amp, enabled: false }]);
      showPage();
      await screen.findByRole("button", { name: /^Test/ });
      noButton(/Reconnect/);
    });

    it("is offered before the running system answers", () => {
      mocks.getDevice.mockReturnValue(new Promise(() => {}));
      showPage();
      expect(button(/Reconnect/).getAttribute("title")).toBe("Force reconnect");
    });

    it("asks the server, then reads Reconnecting for two seconds", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      showPage();
      fireEvent.click(await screen.findByRole("button", { name: /Reconnect/ }));
      expect(mocks.reconnectDevice).toHaveBeenCalledWith("amp");
      await act(async () => { await Promise.resolve(); });
      const busy = button(/Reconnecting\.\.\./) as HTMLButtonElement;
      expect(busy.disabled).toBe(true);
      expect(busy.style.opacity).toBe("0.6");
      await act(async () => { vi.advanceTimersByTime(1900); });
      expect(button(/Reconnecting\.\.\./)).toBeTruthy();
      await act(async () => { vi.advanceTimersByTime(200); });
      expect((button(/^Reconnect$/) as HTMLButtonElement).disabled).toBe(false);
    });

    it("reads Reconnecting for two seconds when the server refuses too", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const logged = vi.spyOn(console, "error").mockImplementation(() => {});
      mocks.reconnectDevice.mockRejectedValue(new Error("refused"));
      showPage();
      fireEvent.click(await screen.findByRole("button", { name: /Reconnect/ }));
      await act(async () => { await Promise.resolve(); });
      expect(button(/Reconnecting\.\.\./)).toBeTruthy();
      await act(async () => { vi.advanceTimersByTime(2100); });
      expect(button(/^Reconnect$/)).toBeTruthy();
      logged.mockRestore();
    });
  });

  describe("Edit, Duplicate and Delete", () => {
    it("hand the project's device to the page around it", async () => {
      const { onEdit, onDuplicate } = showPage();
      fireEvent.click(await screen.findByRole("button", { name: /Edit/ }));
      expect(onEdit).toHaveBeenCalledWith(amp);
      expect(button(/Edit/).getAttribute("title")).toBe("Edit device settings");
      fireEvent.click(button(/Duplicate/));
      expect(onDuplicate).toHaveBeenCalledWith(amp);
      expect(button(/Duplicate/).getAttribute("title")).toBe("Duplicate device");
    });

    it("asks before deleting, and Cancel puts the button back", async () => {
      showPage();
      fireEvent.click(await screen.findByRole("button", { name: /Delete/ }));
      expect(screen.getByText("Delete this device?")).toBeTruthy();
      fireEvent.click(button("Cancel"));
      expect(screen.queryByText("Delete this device?")).toBeNull();
      expect(button(/Delete/).getAttribute("title")).toBe("Delete device");
      expect(mocks.deleteDevice).not.toHaveBeenCalled();
    });

    it("deletes, says so while it does, and tells the page around it", async () => {
      let done!: (v: unknown) => void;
      mocks.deleteDevice.mockReturnValue(new Promise((r) => { done = r; }));
      const { onDeleted } = showPage();
      fireEvent.click(await screen.findByRole("button", { name: /Delete/ }));
      fireEvent.click(button("Yes, Delete"));
      expect(mocks.deleteDevice).toHaveBeenCalledWith("amp");
      const busy = button("Deleting...") as HTMLButtonElement;
      expect(busy.disabled).toBe(true);
      expect(busy.style.opacity).toBe("0.6");
      await act(async () => { done({ status: "deleted" }); });
      expect(onDeleted).toHaveBeenCalledWith("amp");
      expect(screen.queryByText("Delete this device?")).toBeNull();
    });

    it("closes the question without telling the page when the delete fails", async () => {
      const logged = vi.spyOn(console, "error").mockImplementation(() => {});
      mocks.deleteDevice.mockRejectedValue(new Error("refused"));
      const { onDeleted } = showPage();
      fireEvent.click(await screen.findByRole("button", { name: /Delete/ }));
      fireEvent.click(button("Yes, Delete"));
      await waitFor(() => expect(screen.queryByText("Delete this device?")).toBeNull());
      expect(onDeleted).not.toHaveBeenCalled();
      expect(button(/Delete/)).toBeTruthy();
      logged.mockRestore();
    });

    it("lists the first five places that use the device, and how many more", async () => {
      const macros = Array.from({ length: 7 }, (_, i) => ({
        id: `m${i}`, name: `Macro ${i}`,
        steps: [{ action: "device.command", device: "amp", command: "power_on" }],
      }));
      setProject([amp], { macros });
      showPage();
      fireEvent.click(await screen.findByRole("button", { name: /Delete/ }));
      expect(screen.getByText(/This device is referenced in 7 place\(s\):/)).toBeTruthy();
      const items = screen.getAllByRole("listitem").map((li) => li.textContent);
      expect(items).toHaveLength(6);
      expect(items[0]).toContain("Macro 0");
      expect(items[4]).toContain("Macro 4");
      expect(items[5]).toBe("...and 2 more");
    });

    it("says one more for a sixth place", async () => {
      const macros = Array.from({ length: 6 }, (_, i) => ({
        id: `m${i}`, name: `Macro ${i}`,
        steps: [{ action: "device.command", device: "amp", command: "power_on" }],
      }));
      setProject([amp], { macros });
      showPage();
      fireEvent.click(await screen.findByRole("button", { name: /Delete/ }));
      const items = screen.getAllByRole("listitem").map((li) => li.textContent);
      expect(items).toHaveLength(6);
      expect(items[5]).toBe("...and 1 more");
    });

    it("lists every place when there are five or fewer, with no warning when none", async () => {
      const macros = [{
        id: "m0", name: "Only Macro",
        steps: [{ action: "device.command", device: "amp", command: "power_on" }],
      }];
      setProject([amp], { macros });
      const view = showPage();
      fireEvent.click(await screen.findByRole("button", { name: /Delete/ }));
      expect(screen.getAllByRole("listitem")).toHaveLength(1);
      view.unmount();
      setProject([amp]);
      showPage();
      fireEvent.click(await screen.findByRole("button", { name: /Delete/ }));
      expect(screen.queryByText(/Warning:/)).toBeNull();
    });
  });

  describe("the line under the name", () => {
    it("shows the driver, the id and Report a problem", async () => {
      showPage();
      await screen.findByRole("button", { name: /^Test/ });
      expect(screen.getByText("acme_amp", { selector: "span" })).toBeTruthy();
      expect(screen.getByText("amp", { selector: "code" })).toBeTruthy();
      expect(screen.getByTitle("Copy device ID")).toBeTruthy();
      fireEvent.click(button("Report a problem"));
      expect(screen.getByTestId("report-dialog").textContent).toContain('{"source":"driver_line"}');
      fireEvent.click(button("close report"));
      expect(screen.queryByTestId("report-dialog")).toBeNull();
    });

    it("underlines Report a problem under the pointer", async () => {
      showPage();
      const link = button("Report a problem");
      fireEvent.mouseEnter(link);
      expect(link.style.textDecoration).toBe("underline");
      fireEvent.mouseLeave(link);
      expect(link.style.textDecoration).toBe("none");
    });

    it("offers a driver update for a device the page can manage, and reads the device again after one", async () => {
      showPage();
      const line = await screen.findByTestId("driver-update-line");
      expect(line.textContent).toBe("acme_amp");
      expect(mocks.getDevice).toHaveBeenCalledTimes(1);
      fireEvent.click(line);
      await waitFor(() => expect(mocks.getDevice).toHaveBeenCalledTimes(2));
    });

    it("offers no driver update for product hardware, an orphan, or before an answer", async () => {
      setInfo({ ownership: "manufacturer" });
      const first = showPage();
      await screen.findByRole("button", { name: /^Test/ });
      expect(screen.queryByTestId("driver-update-line")).toBeNull();
      first.unmount();
      setInfo();
      setLive({ "device.amp.orphaned": true });
      const second = showPage();
      await screen.findByRole("button", { name: /^Test/ });
      expect(screen.queryByTestId("driver-update-line")).toBeNull();
      second.unmount();
      setLive({});
      mocks.getDevice.mockReturnValue(new Promise(() => {}));
      showPage();
      expect(screen.queryByTestId("driver-update-line")).toBeNull();
    });
  });
});
