import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Settings: what Save does once its checks pass (the project first, what is
// sent, what follows it), which changes ask for a restart, where the restart
// dialog waits, the "Change who can connect?" question, and what the page
// says when an answer it reads on opening does not come. The server and the
// project are the invented ones in settingsHarness.

vi.mock("../api/restClient", async () => (await import("../test/settingsHarness")).api);
vi.mock("../store/projectStore", async () => (await import("../test/settingsHarness")).projectStoreModule);
vi.mock("../store/toastStore", async () => (await import("../test/settingsHarness")).toast);
vi.mock("../components/shared/clipboard", async () => (await import("../test/settingsHarness")).clipboard);
vi.mock("../components/system/HostNetworkCard", () => ({ HostNetworkCard: () => null }));
vi.mock("../components/shared/VariableKeyPicker", () => ({ VariableKeyPicker: () => null }));
// The restart dialog's own behaviour has its own tests; these pin what the
// page hands it.
vi.mock("../components/shared/RestartProgressDialog", async () => {
  const { createElement: h } = await import("react");
  return {
    RestartProgressDialog: (props: {
      targetUrl: string; alsoTry?: string[]; isProtocolSwitch: boolean; expectsNewCert: boolean;
      onClose: () => void; handoff?: { lines: unknown; saved: unknown };
    }) => h("div", { "data-testid": "restart-dialog" },
      h("pre", { "data-testid": "restart-dialog-props" }, JSON.stringify({
        targetUrl: props.targetUrl,
        alsoTry: props.alsoTry ?? null,
        isProtocolSwitch: props.isProtocolSwitch,
        expectsNewCert: props.expectsNewCert,
        handoff: !!props.handoff,
      })),
      props.handoff ? h("div", { "data-testid": "handoff-lines" }, props.handoff.lines as never) : null,
      props.handoff ? h("div", { "data-testid": "handoff-saved" }, props.handoff.saved as never) : null,
      h("button", { onClick: props.onClose }, "Close restart"),
    ),
  };
});

import { SystemSettingsView } from "./SystemSettingsView";
import { ApiError } from "../api/errors";
import {
  RUNNING_ANY,
  api,
  projectState,
  renderLoaded,
  resetHarness,
  saveButton,
  served,
  toast,
} from "../test/settingsHarness";

const ORIGIN = window.location.origin;
const PORT = window.location.port;

const dialogProps = () => JSON.parse(screen.getByTestId("restart-dialog-props").textContent ?? "{}");
const logLevel = () => screen.getByLabelText("Log level") as HTMLSelectElement;
const httpPort = () => screen.getByLabelText("HTTP port") as HTMLInputElement;
const apiKeyBox = () => screen.getByPlaceholderText("No API key set") as HTMLInputElement;
const banner = () => screen.queryByTestId("restart-banner");
const listenField = () => screen.getByLabelText("Accept connections from") as HTMLSelectElement;

/** The switch on the row that says `text`. */
function switchBeside(text: string): HTMLElement {
  let el: HTMLElement | null = screen.getByText(text);
  while (el && !el.querySelector("[role=switch]")) el = el.parentElement;
  return el!.querySelector("[role=switch]") as HTMLElement;
}

/** The server's answer for a choice: everything a preview carries. */
function preview(over: Record<string, unknown> = {}) {
  return {
    mode: "any", adapter_name: "", label: "Any network", panel_addresses: [] as string[],
    connected_through: "192.168.1.40", serves_this_address: true, keeps_this_connection: true,
    safety_net: false, problem: null, consequences: [] as Array<{ code: string; message: string }>,
    held_by: null, cloud_remote_access: false,
    ...over,
  };
}

/** Save, then wait until it has finished. */
async function saveAndSettle() {
  fireEvent.click(saveButton());
  await waitFor(() => expect(saveButton().textContent).toBe("Save"));
}

beforeEach(() => {
  resetHarness();
});

describe("Save, once its checks pass", () => {
  it("saves the project first, then the system settings", async () => {
    projectState.dirty = true;
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(logLevel(), { target: { value: "warning" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.updateSystemConfig).toHaveBeenCalledTimes(1));
    expect(projectState.save).toHaveBeenCalledTimes(1);
    expect(projectState.save.mock.invocationCallOrder[0])
      .toBeLessThan(api.updateSystemConfig.mock.invocationCallOrder[0]);
    expect(api.updateSystemConfig).toHaveBeenCalledWith({ logging: { level: "warning" } });
  });

  it("leaves the project alone when only a system setting changed", async () => {
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(logLevel(), { target: { value: "warning" } });
    await saveAndSettle();
    expect(api.updateSystemConfig).toHaveBeenCalledTimes(1);
    expect(projectState.save).not.toHaveBeenCalled();
  });

  it("sends nothing to the server when only the project changed, and says it saved", async () => {
    projectState.dirty = true;
    await renderLoaded(<SystemSettingsView />);
    fireEvent.click(saveButton());
    await waitFor(() => expect(toast.showSuccess).toHaveBeenCalledWith("Settings saved."));
    expect(projectState.save).toHaveBeenCalledTimes(1);
    expect(api.updateSystemConfig).not.toHaveBeenCalled();
  });

  it("stops, and says so, when the project could not be saved", async () => {
    projectState.dirty = true;
    projectState.save.mockRejectedValueOnce(new ApiError(409, JSON.stringify({ detail: "Someone else saved first." })));
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(logLevel(), { target: { value: "warning" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(toast.showError).toHaveBeenCalledWith(
      "Could not save project settings: Someone else saved first.",
    ));
    expect(api.updateSystemConfig).not.toHaveBeenCalled();
    expect(toast.showSuccess).not.toHaveBeenCalled();
    // Nothing was saved, so the change is still waiting.
    expect(saveButton().disabled).toBe(false);
  });

  it("sends nothing, and says nothing, when all that changed is a secret sent back as the server gave it", async () => {
    await renderLoaded(<SystemSettingsView />);
    await userEvent.type(apiKeyBox(), "***");
    expect(saveButton().disabled).toBe(false);
    fireEvent.click(saveButton());
    await new Promise((r) => setTimeout(r, 50));
    expect(api.updateSystemConfig).not.toHaveBeenCalled();
    expect(toast.showSuccess).not.toHaveBeenCalled();
    expect(toast.showError).not.toHaveBeenCalled();
  });

  it("leaves that secret out and sends the rest", async () => {
    await renderLoaded(<SystemSettingsView />);
    await userEvent.type(apiKeyBox(), "***");
    fireEvent.change(logLevel(), { target: { value: "error" } });
    await saveAndSettle();
    expect(api.updateSystemConfig).toHaveBeenCalledWith({ logging: { level: "error" } });
  });

  it("says Saving... and stays off while the save is in flight", async () => {
    let finish: (v: { status: string; updated_sections: string[] }) => void = () => {};
    api.updateSystemConfig.mockImplementationOnce(() => new Promise((r) => { finish = r; }));
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(logLevel(), { target: { value: "warning" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(saveButton().textContent).toBe("Saving..."));
    expect(saveButton().disabled).toBe(true);
    finish({ status: "ok", updated_sections: ["logging"] });
    await waitFor(() => expect(saveButton().textContent).toBe("Save"));
  });

  it("reads the config and the certificate status again, and has nothing left to save", async () => {
    await renderLoaded(<SystemSettingsView />);
    expect(api.getSystemConfig).toHaveBeenCalledTimes(1);
    expect(api.getTlsStatus).toHaveBeenCalledTimes(1);
    served.config.logging.level = "warning";
    fireEvent.change(logLevel(), { target: { value: "warning" } });
    await saveAndSettle();
    expect(api.getSystemConfig).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(api.getTlsStatus).toHaveBeenCalledTimes(2));
    expect(saveButton().disabled).toBe(true);
    expect(logLevel().value).toBe("warning");
    expect(toast.showSuccess).toHaveBeenCalledWith("Settings saved.");
    expect(screen.queryByText("Restart to apply changes?")).toBeNull();
  });

  it("says a restart is needed, and asks for one, when a change needs it", async () => {
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(httpPort(), { target: { value: "9090" } });
    await saveAndSettle();
    expect(api.updateSystemConfig).toHaveBeenCalledWith({ network: { http_port: 9090 } });
    expect(toast.showSuccess).toHaveBeenCalledWith("Settings saved. Restart required for some changes to take effect.");
    expect(await screen.findByText("Restart to apply changes?")).toBeTruthy();
  });
});

describe("Which changes ask for a restart", () => {
  it.each([
    ["the HTTP port", () => fireEvent.change(httpPort(), { target: { value: "9090" } })],
    ["Short URLs", () => fireEvent.click(switchBeside("Short URLs (port 80 redirect)"))],
    ["an HTTPS setting", () => fireEvent.click(switchBeside("Enable HTTPS"))],
  ])("%s does, saved or not", async (_name, change) => {
    await renderLoaded(<SystemSettingsView />);
    expect(banner()).toBeNull();
    change();
    expect(banner()?.textContent).toContain(
      "Network or security changes need a restart. Save first, then restart from here.",
    );
    expect(within(banner()!).queryByRole("button", { name: "Restart now" })).toBeNull();
    await saveAndSettle();
    expect(banner()?.textContent).toContain("Saved. Restart the server to apply network or security changes.");
    expect(within(banner()!).getByRole("button", { name: "Restart now" })).toBeTruthy();
  });

  it.each([
    ["the log level", () => fireEvent.change(logLevel(), { target: { value: "debug" } })],
    ["Reach devices through", () => fireEvent.change(screen.getByLabelText("Reach devices through"), { target: { value: "" } })],
    ["the API key", () => fireEvent.change(apiKeyBox(), { target: { value: "k" } })],
  ])("%s does not", async (_name, change) => {
    await renderLoaded(<SystemSettingsView />);
    change();
    expect(banner()).toBeNull();
  });

});

describe("The restart prompt after a save", () => {
  async function savedNeedingRestart() {
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(httpPort(), { target: { value: "9090" } });
    await saveAndSettle();
    await screen.findByText("Restart to apply changes?");
  }

  it("Restart now opens the restart dialog at this page's address", async () => {
    await savedNeedingRestart();
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Restart now" }));
    await screen.findByTestId("restart-dialog");
    expect(screen.queryByText("Restart to apply changes?")).toBeNull();
    expect(dialogProps()).toEqual({
      targetUrl: `${ORIGIN}/programmer`, alsoTry: [], isProtocolSwitch: false, expectsNewCert: false, handoff: false,
    });
  });

  it("Later closes it and leaves the banner's Restart now, which opens the dialog", async () => {
    await savedNeedingRestart();
    fireEvent.click(screen.getByRole("button", { name: "Later" }));
    expect(screen.queryByText("Restart to apply changes?")).toBeNull();
    expect(screen.queryByTestId("restart-dialog")).toBeNull();
    fireEvent.click(within(banner()!).getByRole("button", { name: "Restart now" }));
    await screen.findByTestId("restart-dialog");
  });

  it("closing the restart dialog leaves the banner up to try again", async () => {
    await savedNeedingRestart();
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Restart now" }));
    await screen.findByTestId("restart-dialog");
    fireEvent.click(screen.getByRole("button", { name: "Close restart" }));
    expect(screen.queryByTestId("restart-dialog")).toBeNull();
    expect(within(banner()!).getByRole("button", { name: "Restart now" })).toBeTruthy();
  });
});

describe("Kiosk settings", () => {
  async function savedKioskChange(alsoPort = false) {
    served.version = { ...served.version, kiosk_available: true };
    await renderLoaded(<SystemSettingsView />);
    await screen.findByText("Kiosk mode");
    fireEvent.click(switchBeside("Kiosk mode"));
    if (alsoPort) fireEvent.change(httpPort(), { target: { value: "9090" } });
    await saveAndSettle();
    await screen.findByText("Reboot Required");
  }

  it("asks for a reboot after a save, in place of the restart prompt", async () => {
    await savedKioskChange(true);
    expect(api.updateSystemConfig).toHaveBeenCalledWith({ kiosk: { enabled: true }, network: { http_port: 9090 } });
    expect(screen.getByText("Kiosk display settings require a reboot to take effect. Reboot now?")).toBeTruthy();
    expect(screen.queryByText("Restart to apply changes?")).toBeNull();
  });

  it("Reboot Now reboots and says so", async () => {
    await savedKioskChange();
    fireEvent.click(screen.getByRole("button", { name: "Reboot Now" }));
    await waitFor(() => expect(toast.showSuccess).toHaveBeenCalledWith("Device is rebooting..."));
    expect(api.rebootSystem).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Reboot Required")).toBeNull();
  });

  it("says to restart by hand when the reboot is refused", async () => {
    api.rebootSystem.mockRejectedValueOnce(new Error("no"));
    await savedKioskChange();
    fireEvent.click(screen.getByRole("button", { name: "Reboot Now" }));
    await waitFor(() => expect(toast.showError).toHaveBeenCalledWith("Reboot not available. Restart the device manually."));
  });

  it("Later says the reboot is still owed", async () => {
    await savedKioskChange();
    fireEvent.click(screen.getByRole("button", { name: "Later" }));
    expect(toast.showSuccess).toHaveBeenLastCalledWith("Settings saved. Reboot the device for changes to take effect.");
    expect(api.rebootSystem).not.toHaveBeenCalled();
    expect(screen.queryByText("Reboot Required")).toBeNull();
  });
});

describe("Where the restart dialog waits", () => {
  /** A saved choice of who can connect that the server is not running yet,
   *  so the banner offers Restart now with nothing unsaved. */
  function waitingChoice(listen: Record<string, unknown> = {}) {
    served.config._listen = { ...RUNNING_ANY, mode: "this_computer", label: "This computer only", ...listen };
  }

  async function restartFromBanner() {
    await renderLoaded(<SystemSettingsView />);
    fireEvent.click(within(await screen.findByTestId("restart-banner")).getByRole("button", { name: "Restart now" }));
    await screen.findByTestId("restart-dialog");
  }

  it("asks nothing more when the server says no change of who can connect is waiting", async () => {
    waitingChoice({ restart_needed: false });
    // Were it asked, this answer would move the page to 127.0.0.1.
    api.previewListen.mockImplementation(async () => preview({ serves_this_address: false, keeps_this_connection: true }));
    await restartFromBanner();
    expect(api.previewListen.mock.calls.every((c) => JSON.stringify(c[0]) === '{"accept_connections_from":"any"}')).toBe(true);
    expect(dialogProps()).toEqual({
      targetUrl: `${ORIGIN}/programmer`, alsoTry: [], isProtocolSwitch: false, expectsNewCert: false, handoff: false,
    });
  });

  it("waits at the saved listener when this page reached the server directly", async () => {
    served.config.tls = { ...served.config.tls, enabled: true, port: 8443 };
    waitingChoice({ http_port: Number(PORT || 80), https_port: null });
    await restartFromBanner();
    expect(dialogProps()).toEqual({
      targetUrl: "https://localhost:8443/programmer", alsoTry: [], isProtocolSwitch: true, expectsNewCert: true, handoff: false,
    });
  });

  it("waits at HTTPS's own port, 8443 when none is saved", async () => {
    served.config.tls = { ...served.config.tls, enabled: true, port: undefined };
    waitingChoice({ http_port: Number(PORT || 80), https_port: null });
    await restartFromBanner();
    expect(dialogProps().targetUrl).toBe("https://localhost:8443/programmer");
  });

  it("moves a browser on this computer to 127.0.0.1 when the choice leaves its address out", async () => {
    waitingChoice({ mode: "any", label: "Any network", restart_needed: true });
    served.config.network.accept_connections_from = "this_computer";
    api.previewListen.mockImplementation(async () => preview({
      mode: "this_computer", serves_this_address: false, keeps_this_connection: true,
    }));
    await restartFromBanner();
    expect(api.previewListen).toHaveBeenCalledWith({ accept_connections_from: "this_computer" });
    expect(dialogProps()).toEqual({
      targetUrl: `http://127.0.0.1${PORT ? `:${PORT}` : ""}/programmer`, alsoTry: [],
      isProtocolSwitch: false, expectsNewCert: false, handoff: false,
    });
  });

  it("waits where it always has when the server's answer does not come", async () => {
    waitingChoice({ restart_needed: true });
    api.previewListen.mockRejectedValue(new Error("down"));
    await restartFromBanner();
    expect(dialogProps().targetUrl).toBe(`${ORIGIN}/programmer`);
    expect(dialogProps().handoff).toBe(false);
  });

  it("hands over to the new choice's own addresses when it cuts this browser off", async () => {
    waitingChoice({ restart_needed: true, label: "Any network" });
    served.config.network.accept_connections_from = "adapter";
    served.config.network.accept_connections_adapter = { name: "Ethernet", mac: "50-EB-F6-D2-FC-29" };
    api.previewListen.mockImplementation(async () => preview({
      mode: "adapter", adapter_name: "Ethernet", label: "Ethernet only",
      panel_addresses: ["10.20.30.5", "10.20.30.6"], connected_through: "",
      serves_this_address: false, keeps_this_connection: false, safety_net: true,
    }));
    await restartFromBanner();
    expect(api.previewListen).toHaveBeenCalledWith({
      accept_connections_from: "adapter", accept_connections_adapter: { name: "Ethernet", mac: "50-EB-F6-D2-FC-29" },
    });
    const at = (ip: string) => `http://${ip}${PORT ? `:${PORT}` : ""}/programmer`;
    expect(dialogProps()).toEqual({
      targetUrl: at("10.20.30.5"), alsoTry: [at("10.20.30.5"), at("10.20.30.6")],
      isProtocolSwitch: false, expectsNewCert: false, handoff: true,
    });
    const lines = screen.getByTestId("handoff-lines").textContent ?? "";
    expect(lines).toContain(at("10.20.30.6"));
    expect(lines).toContain(`http://localhost${PORT ? `:${PORT}` : ""}/programmer`);
    expect(lines).toContain("OpenAVC goes back to Any network");
    // Handed over: nothing on this page offers another restart.
    expect(banner()).toBeNull();
  });

  it("cut off with no address to go to, waits at this computer's own", async () => {
    waitingChoice({ mode: "any", label: "Any network", restart_needed: true });
    served.config.network.accept_connections_from = "this_computer";
    api.previewListen.mockImplementation(async () => preview({
      mode: "this_computer", serves_this_address: false, keeps_this_connection: false, safety_net: true,
    }));
    await restartFromBanner();
    expect(dialogProps()).toEqual({
      targetUrl: `http://localhost${PORT ? `:${PORT}` : ""}/programmer`, alsoTry: [],
      isProtocolSwitch: false, expectsNewCert: false, handoff: true,
    });
    expect(screen.getByTestId("handoff-lines").textContent).toContain("If nobody opens the Programmer that way within 10 minutes, OpenAVC goes back to Any network.");
  });

  it("offers no restart for a saved choice when the server does not say what it runs", async () => {
    served.config._listen = undefined;
    served.config.network.accept_connections_from = "this_computer";
    await renderLoaded(<SystemSettingsView />);
    expect(banner()).toBeNull();
  });
});

describe("The Change who can connect? question, when the server does not say what it runs", () => {
  it("names the choice it goes back to generically", async () => {
    served.config._listen = undefined;
    api.previewListen.mockImplementation(async () => preview({
      mode: "this_computer", serves_this_address: false, keeps_this_connection: false, safety_net: true,
    }));
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(listenField(), { target: { value: "this_computer" } });
    fireEvent.click(saveButton());
    const text = within(await screen.findByRole("alertdialog")).getByTestId("listen-cut-off-warning").textContent ?? "";
    expect(text).toContain("OpenAVC goes back to the previous choice.");
  });
});

describe("The Change who can connect? question", () => {
  async function askedAfterChoosingThisComputer() {
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(listenField(), { target: { value: "this_computer" } });
    fireEvent.click(saveButton());
    return within(await screen.findByRole("alertdialog")).getByTestId("listen-cut-off-warning").textContent ?? "";
  }

  it("gives the Programmer's addresses on the HTTPS port on screen when HTTPS is on", async () => {
    served.config.tls = { ...served.config.tls, enabled: true, port: 9443 };
    api.previewListen.mockImplementation(async () => preview({
      mode: "adapter", adapter_name: "Ethernet", panel_addresses: ["10.20.30.5"],
      serves_this_address: false, keeps_this_connection: false, safety_net: true,
    }));
    const text = await askedAfterChoosingThisComputer();
    expect(text).toContain("https://10.20.30.5:9443/programmer");
    expect(text).toContain("https://localhost:9443/programmer");
    expect(text).toContain("You're connected through 192.168.1.40.");
  });

  it("uses 8443 when HTTPS is on with no port, and this page's host when the server names no address", async () => {
    served.config.tls = { ...served.config.tls, enabled: true, port: undefined };
    api.previewListen.mockImplementation(async () => preview({
      mode: "adapter", adapter_name: "Ethernet", panel_addresses: ["10.20.30.5"], connected_through: "",
      serves_this_address: false, keeps_this_connection: false, safety_net: true,
    }));
    const text = await askedAfterChoosingThisComputer();
    expect(text).toContain("https://10.20.30.5:8443/programmer");
    expect(text).toContain(`You're connected through ${window.location.hostname}.`);
  });

  it("gives the HTTP port on screen while HTTPS is off, and no network address outside a chosen adapter", async () => {
    api.previewListen.mockImplementation(async () => preview({
      mode: "this_computer", panel_addresses: ["10.20.30.5"],
      serves_this_address: false, keeps_this_connection: false, safety_net: true,
    }));
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(httpPort(), { target: { value: "8181" } });
    fireEvent.change(listenField(), { target: { value: "this_computer" } });
    fireEvent.click(saveButton());
    const text = within(await screen.findByRole("alertdialog")).getByTestId("listen-cut-off-warning").textContent ?? "";
    expect(text).toContain("http://localhost:8181/programmer");
    expect(text).not.toContain("10.20.30.5");
    expect(text).toContain("OpenAVC goes back to Any network.");
  });

  it("closes once Restart and apply is pressed, and saves", async () => {
    api.previewListen.mockImplementation(async () => preview({
      mode: "this_computer", serves_this_address: false, keeps_this_connection: false, safety_net: true,
    }));
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(listenField(), { target: { value: "this_computer" } });
    fireEvent.click(saveButton());
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Restart and apply" }));
    expect(screen.queryByText("Change who can connect?")).toBeNull();
    await waitFor(() => expect(api.updateSystemConfig).toHaveBeenCalledTimes(1));
  });

  it("closes on Cancel and saves nothing", async () => {
    api.previewListen.mockImplementation(async () => preview({
      mode: "this_computer", serves_this_address: false, keeps_this_connection: false, safety_net: true,
    }));
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(listenField(), { target: { value: "this_computer" } });
    fireEvent.click(saveButton());
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("Change who can connect?")).toBeNull();
    await new Promise((r) => setTimeout(r, 30));
    expect(api.updateSystemConfig).not.toHaveBeenCalled();
  });

  it("says why the change could not be checked, and saves nothing", async () => {
    await renderLoaded(<SystemSettingsView />);
    api.previewListen.mockRejectedValue(new ApiError(500, JSON.stringify({ detail: "Adapters unreadable" })));
    fireEvent.change(listenField(), { target: { value: "this_computer" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(toast.showError).toHaveBeenCalledWith(
      "Could not check the change to Accept connections from: Adapters unreadable",
    ));
    expect(api.updateSystemConfig).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("is not asked, and no restart is, when the choice on screen is the saved one again", async () => {
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(listenField(), { target: { value: "this_computer" } });
    fireEvent.change(listenField(), { target: { value: "any" } });
    fireEvent.change(logLevel(), { target: { value: "warning" } });
    const asked = api.previewListen.mock.calls.length;
    await saveAndSettle();
    expect(api.updateSystemConfig).toHaveBeenCalledWith({
      network: { accept_connections_from: "any", accept_connections_adapter: {} },
      logging: { level: "warning" },
    });
    // Only the page's own preview of the choice may have asked since.
    expect(api.previewListen.mock.calls.slice(asked).every((c) => JSON.stringify(c[0]) === '{"accept_connections_from":"any"}')).toBe(true);
    expect(screen.queryByText("Change who can connect?")).toBeNull();
    expect(screen.queryByText("Restart to apply changes?")).toBeNull();
    expect(toast.showSuccess).toHaveBeenCalledWith("Settings saved.");
  });

  it("saves without asking, and asks for the restart, when the server says this browser keeps its connection", async () => {
    api.previewListen.mockImplementation(async () => preview({ mode: "this_computer", safety_net: false }));
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(listenField(), { target: { value: "this_computer" } });
    await saveAndSettle();
    expect(api.updateSystemConfig).toHaveBeenCalledWith({
      network: { accept_connections_from: "this_computer", accept_connections_adapter: {} },
    });
    expect(toast.showSuccess).toHaveBeenCalledWith("Settings saved. Restart required for some changes to take effect.");
    expect(await screen.findByText("Restart to apply changes?")).toBeTruthy();
  });
});

describe("Where panels open, for the choice the server runs", () => {
  it("lists the running choice's addresses when the server has no answer for the choice on screen", async () => {
    served.config._listen = { ...RUNNING_ANY, network_addresses: ["10.0.0.9"] };
    await renderLoaded(<SystemSettingsView />);
    await waitFor(() => expect(screen.getByTestId("panels-open-at").textContent).toContain("http://10.0.0.9:8080/panel"));
  });

  it("lists none once another choice is on screen", async () => {
    served.config._listen = { ...RUNNING_ANY, network_addresses: ["10.0.0.9"] };
    await renderLoaded(<SystemSettingsView />);
    await screen.findByTestId("panels-open-at");
    fireEvent.change(listenField(), { target: { value: "this_computer" } });
    fireEvent.change(listenField(), { target: { value: "any" } });
    expect(screen.getByTestId("panels-open-at")).toBeTruthy();
    fireEvent.change(listenField(), { target: { value: "this_computer" } });
    expect(screen.queryByTestId("panels-open-at")).toBeNull();
  });

  it("lists the server's answer over the running choice's addresses", async () => {
    served.config._listen = { ...RUNNING_ANY, network_addresses: ["10.0.0.9"] };
    api.previewListen.mockImplementation(async () => preview({ panel_addresses: ["10.0.0.7"] }));
    await renderLoaded(<SystemSettingsView />);
    await waitFor(() => expect(screen.getByTestId("panels-open-at").textContent).toContain("http://10.0.0.7:8080/panel"));
    expect(screen.getByTestId("panels-open-at").textContent).not.toContain("10.0.0.9");
  });

  it("asks only about the choice that stays on screen for a moment", async () => {
    await renderLoaded(<SystemSettingsView />);
    await waitFor(() => expect(api.previewListen).toHaveBeenCalledTimes(1));
    fireEvent.change(listenField(), { target: { value: "this_computer" } });
    await new Promise((r) => setTimeout(r, 60));
    fireEvent.change(listenField(), { target: { value: "any" } });
    await new Promise((r) => setTimeout(r, 300));
    expect(api.previewListen.mock.calls.map((c) => c[0])).toEqual([
      { accept_connections_from: "any" },
      { accept_connections_from: "any" },
    ]);
  });

  it("keeps the answer for the choice on screen when an earlier choice's answer comes late", async () => {
    const pending: Array<(p: ReturnType<typeof preview>) => void> = [];
    api.previewListen.mockImplementation(() => new Promise((r) => { pending.push(r); }));
    await renderLoaded(<SystemSettingsView />);
    // The first question, for Any network, waits.
    await waitFor(() => expect(pending).toHaveLength(1));
    fireEvent.change(listenField(), { target: { value: "this_computer" } });
    await waitFor(() => expect(pending).toHaveLength(2));
    pending[1](preview({ mode: "this_computer", panel_addresses: [] }));
    await waitFor(() => expect(screen.getByTestId("accept-connections-help").textContent).toBeTruthy());
    expect(screen.queryByTestId("panels-open-at")).toBeNull();
    // Any network's answer arrives after: it is for a choice no longer on screen.
    pending[0](preview({ panel_addresses: ["10.0.0.7"] }));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("panels-open-at")).toBeNull();
  });

  it("forgets the server's answer when a later one fails", async () => {
    served.config._listen = { ...RUNNING_ANY, network_addresses: ["10.0.0.9"] };
    api.previewListen.mockImplementationOnce(async () => preview({ panel_addresses: ["10.0.0.7"] }));
    await renderLoaded(<SystemSettingsView />);
    await waitFor(() => expect(screen.getByTestId("panels-open-at").textContent).toContain("10.0.0.7"));
    api.previewListen.mockRejectedValue(new Error("gone"));
    fireEvent.change(listenField(), { target: { value: "this_computer" } });
    await waitFor(() => expect(api.previewListen).toHaveBeenCalledWith({ accept_connections_from: "this_computer" }));
    await waitFor(() => expect(screen.queryByTestId("panels-open-at")).toBeNull());
  });
});

describe("What the page reads when it opens", () => {
  it("says the config could not be loaded, and draws nothing else", async () => {
    api.getSystemConfig.mockRejectedValueOnce(new ApiError(500, JSON.stringify({ detail: "Disk full" })));
    const { render } = await import("@testing-library/react");
    render(<SystemSettingsView />);
    await waitFor(() => expect(toast.showError).toHaveBeenCalledWith("Failed to load config: Disk full"));
    expect(screen.getByText("Loading...")).toBeTruthy();
  });

  it("says the update settings could not be loaded", async () => {
    api.getSystemVersion.mockRejectedValueOnce(new Error("down"));
    await renderLoaded(<SystemSettingsView />);
    await waitFor(() => expect(screen.getByText("Update settings could not be loaded. Reload this page to try again.")).toBeTruthy());
    expect(screen.queryByText("Check for updates")).toBeNull();
  });

  it("says where updates are managed in place of the update settings, when the server says", async () => {
    served.version = { ...served.version, update_guidance: "Updates come from the package manager." };
    await renderLoaded(<SystemSettingsView />);
    await waitFor(() => expect(screen.getByText("Updates come from the package manager.")).toBeTruthy());
    expect(screen.queryByText("Check for updates")).toBeNull();
  });

  it("says it is loading the update settings until the server answers", async () => {
    let answer: (v: Record<string, unknown>) => void = () => {};
    api.getSystemVersion.mockImplementationOnce(() => new Promise((r) => { answer = r; }));
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText("Loading update settings...")).toBeTruthy();
    answer({ ...served.version, update_guidance: "" });
    await waitFor(() => expect(screen.getByText("Check for updates")).toBeTruthy());
  });

  it("treats a server that sends no guidance as one with nothing to say", async () => {
    served.version = { version: "0", channel: "stable", platform: "linux", kiosk_available: false, panel_dim_available: false };
    await renderLoaded(<SystemSettingsView />);
    await waitFor(() => expect(screen.getByText("Check for updates")).toBeTruthy());
  });

  it("still opens when the adapters and the certificate status cannot be read", async () => {
    api.getNetworkAdapters.mockRejectedValueOnce(new Error("no"));
    api.getTlsStatus.mockRejectedValueOnce(new Error("no"));
    api.getSshStatus.mockRejectedValueOnce(new Error("no"));
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText("Network")).toBeTruthy();
    expect(screen.queryByText("SSH access")).toBeNull();
  });
});
