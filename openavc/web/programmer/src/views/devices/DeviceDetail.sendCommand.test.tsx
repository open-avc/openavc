import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * Send Command on a device's page: pick one of the driver's commands, fill its
 * parameters, send it, and read what came back. A command that erases or
 * resets asks first; one that takes or hands back a file runs in the
 * background; a failure the driver could have caused offers Report a problem.
 */

const mocks = vi.hoisted(() => ({
  getDevice: vi.fn(),
  sendCommand: vi.fn(),
  fileRun: {
    view: { kind: "idle" } as { kind: string },
    busy: false,
    start: vi.fn(),
    stop: vi.fn(),
    clear: vi.fn(),
  },
  fileRunDevices: [] as string[],
}));

vi.mock("../../api/restClient", () => ({
  getDevice: mocks.getDevice,
  sendCommand: mocks.sendCommand,
  deleteDevice: vi.fn(),
  getDeviceSettings: vi.fn(async () => ({ settings: {} })),
  installCommunityDriver: vi.fn(),
  listMissingDrivers: vi.fn(async () => []),
  reconnectDevice: vi.fn(),
  resumeDevice: vi.fn(),
  setDeviceSetting: vi.fn(),
  testDeviceConnection: vi.fn(),
}));
vi.mock("../../api/auditClient", () => ({ getAuditDeviceTarget: vi.fn() }));

const liveState: Record<string, unknown> = {};
vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: unknown) => unknown) => selector({ liveState }),
}));

vi.mock("./CommandFileRun", () => ({
  useCommandFileRun: (deviceId: string) => {
    mocks.fileRunDevices.push(deviceId);
    return mocks.fileRun;
  },
  CommandFileRunStatus: ({ view, deviceId, deviceName, onStop, onReport }: {
    view: { kind: string }; deviceId: string; deviceName: string;
    onStop: () => void; onReport: (c: unknown) => void;
  }) => (
    <div data-testid="file-run">
      {`${view.kind}|${deviceId}|${deviceName}`}
      <button onClick={onStop}>stop-run</button>
      <button onClick={() => onReport({ source: "command_failure", error: "run failed" })}>report-run</button>
    </div>
  ),
}));
vi.mock("./ChildEntities", () => ({ ChildEntities: () => null }));
vi.mock("./DeviceStateLog", () => ({ DeviceStateLog: () => null }));
vi.mock("./DeviceProtocolLog", () => ({ DeviceProtocolLog: () => null }));
vi.mock("./QuickActions", () => ({ QuickActions: () => null }));
vi.mock("./DriverUpdateLine", () => ({ DriverUpdateLine: () => null }));
vi.mock("./InstalledEquipment", () => ({ InstalledEquipment: () => null }));
vi.mock("./ProblemReportDialog", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./ProblemReportDialog")>()),
  ProblemReportDialog: ({ context, deviceName }: { context: unknown; deviceName: string }) => (
    <div data-testid="report-dialog" data-device-name={deviceName}>{JSON.stringify(context)}</div>
  ),
}));
vi.mock("../../components/plugins/PluginExtensions", () => ({
  DevicePanelSlot: () => null,
  ContextActionRenderer: () => null,
}));

import { useProjectStore } from "../../store/projectStore";
import { ApiError } from "../../api/errors";
import { DeviceDetail } from "./DeviceDetail";

const COMMANDS = {
  power_on: { label: "Power On", help: "Turns the amplifier on." },
  set_input: {
    label: "Set Input",
    params: {
      input: { type: "enum", values: ["hdmi1", "hdmi2"], default: "hdmi2" },
      note: { type: "string" },
    },
  },
  set_level: { label: "Set Level", params: { level: { type: "integer", min: 0, max: 100, required: true } } },
  factory_reset: { label: "Factory Reset", confirm: "This erases every preset." },
  reboot: { label: "Reboot", confirm: true },
  upload_logo: {
    label: "Upload Logo",
    params: { image: { type: "file", required: true }, slot: { type: "string" } },
  },
};

function setLive(next: Record<string, unknown>) {
  for (const k of Object.keys(liveState)) delete liveState[k];
  Object.assign(liveState, { "device.amp.enabled": true, ...next });
}

function setInfo(commands: Record<string, unknown> = COMMANDS) {
  mocks.getDevice.mockResolvedValue({
    id: "amp", name: "Rack Amp", driver: "acme_amp", connected: true,
    enabled: true, commands, driver_info: {},
  });
}

function page() {
  return <DeviceDetail deviceId="amp" onEdit={() => {}} onDeleted={() => {}} onDuplicate={() => {}} />;
}

async function showPage() {
  const view = render(page());
  await screen.findByRole("button", { name: /Test/ });
  return view;
}

async function pick(label: string) {
  fireEvent.click(await screen.findByTestId("command-picker"));
  fireEvent.click(screen.getByRole("option", { name: new RegExp(`^${label}`) }));
}

function sendButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;
}

function reportButtons() {
  return screen.getAllByRole("button", { name: "Report a problem" });
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.fileRun.busy = false;
  mocks.fileRun.view = { kind: "idle" };
  mocks.fileRunDevices.length = 0;
  useProjectStore.setState({
    project: {
      devices: [{ id: "amp", driver: "acme_amp", name: "Rack Amp", config: {}, enabled: true }],
      connections: {},
      device_groups: [], macros: [], monitors: [],
      ui: { pages: [], master_elements: [] },
    } as never,
  });
  setInfo();
  setLive({ "device.amp.connected": true, "device.amp.name": "Rack Amp" });
});

describe("Send Command with nothing to send", () => {
  it("says commands arrive once a disconnected device connects", async () => {
    setInfo({});
    setLive({ "device.amp.connected": false });
    await showPage();
    expect(await screen.findByText(/Device is not connected\. Commands will be available once the device connects\./)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
  });

  it("says the driver defines none when a connected device has no commands", async () => {
    setInfo({});
    await showPage();
    expect(await screen.findByText(/No commands available\. The driver may not be loaded/)).toBeTruthy();
  });
});

describe("sending a command", () => {
  it("offers Send only once a command is chosen, with the command's help under it", async () => {
    await showPage();
    await screen.findByTestId("command-picker");
    expect(sendButton().disabled).toBe(true);
    expect(sendButton().style.background).toBe("var(--bg-hover)");
    await pick("Power On");
    expect(screen.getByText("Turns the amplifier on.")).toBeTruthy();
    expect(sendButton().disabled).toBe(false);
    expect(sendButton().style.background).toBe("var(--accent-bg)");
  });

  it("sends it and says it went", async () => {
    mocks.sendCommand.mockResolvedValue({ result: true });
    await showPage();
    await pick("Power On");
    fireEvent.click(sendButton());
    expect(mocks.sendCommand).toHaveBeenCalledWith("amp", "power_on", {});
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("Power On sent.");
    expect(status.querySelector("pre")).toBeNull();
  });

  it("holds Send while a command is going out", async () => {
    let finish: (v: unknown) => void = () => {};
    mocks.sendCommand.mockReturnValue(new Promise((r) => { finish = r; }));
    await showPage();
    await pick("Power On");
    fireEvent.click(sendButton());
    expect(sendButton().disabled).toBe(true);
    expect(sendButton().style.opacity).toBe("0.6");
    await act(async () => finish({ result: null }));
    expect(sendButton().disabled).toBe(false);
  });

  it("starts a choice on the driver's default and leaves blank parameters out", async () => {
    mocks.sendCommand.mockResolvedValue({ result: "ok" });
    await showPage();
    await pick("Set Input");
    fireEvent.click(sendButton());
    expect(mocks.sendCommand).toHaveBeenCalledWith("amp", "set_input", { input: "hdmi2" });
    expect((await screen.findByRole("status")).textContent).toContain("Set Input sent. The device answered: ok");
  });

  it("sends each parameter as its declared type, the way Live Test and the audit send it", async () => {
    setInfo({
      tune: {
        label: "Tune",
        params: {
          zone: { type: "integer", required: true },
          gain: { type: "number", decimals: 0 },
          mute: { type: "boolean", default: true },
        },
      },
    });
    mocks.sendCommand.mockResolvedValue({ result: true });
    await showPage();
    await pick("Tune");
    const [zone, gain] = screen.getAllByRole("spinbutton");
    fireEvent.change(zone, { target: { value: "050" } });
    fireEvent.change(gain, { target: { value: "26.4" } });
    fireEvent.click(sendButton());
    expect(mocks.sendCommand).toHaveBeenCalledWith("amp", "tune", { zone: 50, gain: 26.4, mute: true });
  });

  it("shows a structured answer below the line", async () => {
    mocks.sendCommand.mockResolvedValue({ result: { preset: 3 } });
    await showPage();
    await pick("Power On");
    fireEvent.click(sendButton());
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("Power On sent. The device answered:");
    expect(status.querySelector("pre")?.textContent).toBe(JSON.stringify({ preset: 3 }, null, 2));
  });

  it("holds Send while a required parameter is blank or out of range", async () => {
    await showPage();
    await pick("Set Level");
    expect(sendButton().disabled).toBe(true);
    const box = screen.getByRole("spinbutton");
    fireEvent.change(box, { target: { value: "150" } });
    expect(sendButton().disabled).toBe(true);
    fireEvent.change(box, { target: { value: "50" } });
    expect(sendButton().disabled).toBe(false);
  });

  it("choosing another command clears the last answer and the file run", async () => {
    mocks.sendCommand.mockResolvedValue({ result: true });
    await showPage();
    await pick("Power On");
    fireEvent.click(sendButton());
    await screen.findByRole("status");
    await pick("Set Input");
    expect(screen.queryByRole("status")).toBeNull();
    expect(mocks.fileRun.clear).toHaveBeenCalled();
  });

  it("keeps the chosen command when the device drops and comes back", async () => {
    const view = await showPage();
    await pick("Power On");
    setInfo({});
    setLive({ "device.amp.connected": false });
    view.rerender(page());
    await screen.findByText(/Device is not connected\./);
    setInfo();
    setLive({ "device.amp.connected": true });
    view.rerender(page());
    await waitFor(() => expect(screen.getByTestId("command-picker").textContent).toContain("Power On"));
    expect(screen.getByText("Turns the amplifier on.")).toBeTruthy();
  });
});

describe("a command that fails", () => {
  it("says why, and offers Report a problem with what was sent for a driver fault", async () => {
    mocks.sendCommand.mockRejectedValue(
      new ApiError(500, JSON.stringify({ detail: "The driver stopped.", code: "driver_error" })),
    );
    await showPage();
    await pick("Set Input");
    fireEvent.click(sendButton());
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("The driver stopped.");
    expect(status.style.fontSize).toBe("var(--font-size-sm)");
    const reports = reportButtons();
    expect(reports).toHaveLength(2);
    fireEvent.click(within(status).getByRole("button", { name: "Report a problem" }));
    expect(JSON.parse(screen.getByTestId("report-dialog").textContent ?? "")).toEqual({
      source: "command_failure",
      error: "The driver stopped.",
      command: "set_input",
      params: { input: "hdmi2" },
      status: 500,
      code: "driver_error",
    });
    expect(screen.getByTestId("report-dialog").dataset.deviceName).toBe("Rack Amp");
  });

  it("reads a refusal with no code as an empty code", async () => {
    mocks.sendCommand.mockRejectedValue(new ApiError(502, "Bad gateway"));
    await showPage();
    await pick("Power On");
    fireEvent.click(sendButton());
    const status = await screen.findByRole("status");
    fireEvent.click(within(status).getByRole("button", { name: "Report a problem" }));
    expect(JSON.parse(screen.getByTestId("report-dialog").textContent ?? "")).toMatchObject({ status: 502, code: "" });
  });

  it("offers Report a problem for an outcome the driver could not confirm", async () => {
    mocks.sendCommand.mockRejectedValue(
      new ApiError(409, JSON.stringify({ detail: "Not confirmed.", code: "command_incomplete" })),
    );
    await showPage();
    await pick("Power On");
    fireEvent.click(sendButton());
    await screen.findByText("Not confirmed.");
    expect(reportButtons()).toHaveLength(2);
  });

  it.each([
    ["a bad parameter", new ApiError(400, JSON.stringify({ detail: "Out of range." })), "Out of range."],
    ["a device that said no", new ApiError(409, JSON.stringify({ detail: "Busy." })), "Busy."],
    ["a thrown error", new Error("Network down"), "Network down"],
  ])("offers no Report a problem for %s", async (_name, error, text) => {
    mocks.sendCommand.mockRejectedValue(error);
    await showPage();
    await pick("Power On");
    fireEvent.click(sendButton());
    expect(await screen.findByText(text)).toBeTruthy();
    expect(reportButtons()).toHaveLength(1);
  });
});

describe("a command that asks first", () => {
  it("asks in the driver's words and sends only on yes", async () => {
    mocks.sendCommand.mockResolvedValue({ result: true });
    await showPage();
    await pick("Factory Reset");
    fireEvent.click(sendButton());
    const dialog = screen.getByRole("alertdialog");
    expect(dialog.textContent).toContain("Factory Reset");
    expect(dialog.textContent).toContain("This erases every preset.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(mocks.sendCommand).not.toHaveBeenCalled();

    fireEvent.click(sendButton());
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Send" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(mocks.sendCommand).toHaveBeenCalledWith("amp", "factory_reset", {});
  });

  it("asks a plain question when the driver only says to confirm", async () => {
    await showPage();
    await pick("Reboot");
    fireEvent.click(sendButton());
    expect(screen.getByRole("alertdialog").textContent).toContain("Send Reboot?");
  });
});

describe("across a drop of the device", () => {
  async function dropAndReturn(view: ReturnType<typeof render>) {
    setInfo({});
    setLive({ "device.amp.connected": false });
    view.rerender(page());
    await screen.findByText(/Device is not connected\./);
    setInfo();
    setLive({ "device.amp.connected": true });
    view.rerender(page());
    await screen.findByTestId("command-picker");
  }

  it("keeps a question that was being asked", async () => {
    const view = await showPage();
    await pick("Factory Reset");
    fireEvent.click(sendButton());
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    await dropAndReturn(view);
    await waitFor(() => expect(screen.getByRole("alertdialog").textContent).toContain("This erases every preset."));
  });

  it("keeps the last answer", async () => {
    mocks.sendCommand.mockResolvedValue({ result: true });
    const view = await showPage();
    await pick("Power On");
    fireEvent.click(sendButton());
    await screen.findByRole("status");
    await dropAndReturn(view);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Power On sent."));
  });
});

describe("a command that takes a file", () => {
  it("runs it in the background with the chosen file, never as a plain send", async () => {
    const { container } = await showPage();
    await pick("Upload Logo");
    expect(sendButton().disabled).toBe(true);
    const file = new File(["png"], "logo.png", { type: "image/png" });
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } });
    const slot = screen.getAllByRole("textbox").find((el) => el.dataset.testid !== "device-filter")!;
    fireEvent.change(slot, { target: { value: "2" } });
    expect(sendButton().disabled).toBe(false);
    fireEvent.click(sendButton());
    await waitFor(() => expect(mocks.fileRun.start).toHaveBeenCalled());
    expect(mocks.fileRun.start).toHaveBeenCalledWith("upload_logo", "Upload Logo", { slot: "2" }, { image: file });
    expect(mocks.sendCommand).not.toHaveBeenCalled();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("follows the run for this device, and stops or reports it from there", async () => {
    mocks.fileRun.view = { kind: "run" };
    await showPage();
    await screen.findByTestId("command-picker");
    expect(mocks.fileRunDevices.every((id) => id === "amp")).toBe(true);
    expect(screen.getByTestId("file-run").textContent).toContain("run|amp|Rack Amp");
    fireEvent.click(screen.getByRole("button", { name: "stop-run" }));
    expect(mocks.fileRun.stop).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "report-run" }));
    expect(JSON.parse(screen.getByTestId("report-dialog").textContent ?? "")).toEqual({
      source: "command_failure",
      error: "run failed",
    });
  });

  it("holds Send while a run is going, and leaves the run alone on a new choice", async () => {
    mocks.fileRun.busy = true;
    await showPage();
    await pick("Power On");
    expect(sendButton().disabled).toBe(true);
    expect(mocks.fileRun.clear).not.toHaveBeenCalled();
  });
});
