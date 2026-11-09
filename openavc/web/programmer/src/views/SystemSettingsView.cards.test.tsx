import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, fireEvent, waitFor, within } from "@testing-library/react";

// Settings: what each plain card's fields write and send (Appearance,
// Network's own fields, Access, SSH, Logging, Updates, Kiosk), the numbers a
// cleared box falls back to, and the SSH switch's answers. The server and the
// project are the invented ones in settingsHarness.

vi.mock("../api/restClient", async () => (await import("../test/settingsHarness")).api);
vi.mock("../store/projectStore", async () => (await import("../test/settingsHarness")).projectStoreModule);
vi.mock("../store/toastStore", async () => (await import("../test/settingsHarness")).toast);
vi.mock("../components/shared/clipboard", async () => (await import("../test/settingsHarness")).clipboard);
vi.mock("../components/system/HostNetworkCard", () => ({ HostNetworkCard: () => null }));
vi.mock("../components/shared/VariableKeyPicker", () => ({ VariableKeyPicker: () => null }));

import { SystemSettingsView } from "./SystemSettingsView";
import { ApiError } from "../api/errors";
import { api, clipboard, renderLoaded, resetHarness, saveButton, served, toast } from "../test/settingsHarness";

/** The switch on the row that says `text`. */
function switchBeside(text: string): HTMLElement {
  let el: HTMLElement | null = screen.getByText(text);
  while (el && !el.querySelector("[role=switch]")) el = el.parentElement;
  return el!.querySelector("[role=switch]") as HTMLElement;
}

/** The input or select in the row whose label is `label`. */
function fieldBeside(label: string): HTMLInputElement | HTMLSelectElement {
  const el = screen.getByText(label, { selector: "label" });
  return el.parentElement!.querySelector("input, select") as HTMLInputElement | HTMLSelectElement;
}

async function saved(): Promise<unknown> {
  fireEvent.click(saveButton());
  await waitFor(() => expect(api.updateSystemConfig).toHaveBeenCalledTimes(1));
  return api.updateSystemConfig.mock.calls[0][0];
}

beforeEach(() => {
  resetHarness();
});

describe("Appearance", () => {
  afterEach(() => {
    localStorage.removeItem("openavc-theme");
    delete document.documentElement.dataset.theme;
  });

  it("shows the theme this browser keeps, dark when it keeps none", async () => {
    await renderLoaded(<SystemSettingsView />);
    expect((fieldBeside("Theme") as HTMLSelectElement).value).toBe("dark");
  });

  it("shows a kept light theme", async () => {
    localStorage.setItem("openavc-theme", "light");
    await renderLoaded(<SystemSettingsView />);
    expect((fieldBeside("Theme") as HTMLSelectElement).value).toBe("light");
  });

  it("switches at once, keeps the choice in this browser, and saves nothing", async () => {
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(fieldBeside("Theme"), { target: { value: "light" } });
    expect(localStorage.getItem("openavc-theme")).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect((fieldBeside("Theme") as HTMLSelectElement).value).toBe("light");
    expect(saveButton().disabled).toBe(true);
  });
});

describe("Each plain field writes its setting", () => {
  it.each([
    ["HTTP port", "9090", { network: { http_port: 9090 } }],
    ["HTTP port", "", { network: { http_port: 8080 } }],
    ["Max file size (MB)", "200", { logging: { max_size_mb: 200 } }],
    ["Max file size (MB)", "", { logging: { max_size_mb: 50 } }],
    ["Max log files", "9", { logging: { max_files: 9 } }],
    ["Max log files", "", { logging: { max_files: 5 } }],
    ["Check interval (hours)", "6", { updates: { auto_check_interval_hours: 6 } }],
    ["Check interval (hours)", "", { updates: { auto_check_interval_hours: 24 } }],
    ["Log level", "debug", { logging: { level: "debug" } }],
    ["Channel", "beta", { updates: { channel: "beta" } }],
  ])("%s set to %j sends %j", async (label, value, body) => {
    await renderLoaded(<SystemSettingsView />);
    await screen.findByText(label, { selector: "label" });
    fireEvent.change(fieldBeside(label), { target: { value } });
    expect(await saved()).toEqual(body);
  });

  it("Username sends what is typed", async () => {
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(screen.getByPlaceholderText("No username set"), { target: { value: "tech" } });
    expect(await saved()).toEqual({ auth: { programmer_username: "tech" } });
  });

  it.each([
    ["File logging", { logging: { file_enabled: false } }],
    ["Check for updates", { updates: { check_enabled: false } }],
    ["Notify only", { updates: { notify_only: true } }],
    ["Short URLs (port 80 redirect)", { network: { port80_redirect: true } }],
  ])("%s's switch sends %j", async (label, body) => {
    await renderLoaded(<SystemSettingsView />);
    await screen.findByText(label);
    fireEvent.click(switchBeside(label));
    expect(await saved()).toEqual(body);
  });

  it("shows each saved value", async () => {
    served.config.logging = { level: "error", file_enabled: false, max_size_mb: 75, max_files: 3 };
    served.config.updates = { check_enabled: false, channel: "beta", auto_check_interval_hours: 12, notify_only: true };
    served.config.network.port80_redirect = true;
    await renderLoaded(<SystemSettingsView />);
    await screen.findByText("Check for updates");
    expect(fieldBeside("Log level").value).toBe("error");
    expect(fieldBeside("Max file size (MB)").value).toBe("75");
    expect(fieldBeside("Max log files").value).toBe("3");
    expect(fieldBeside("Channel").value).toBe("beta");
    expect(fieldBeside("Check interval (hours)").value).toBe("12");
    expect(switchBeside("File logging").getAttribute("aria-checked")).toBe("false");
    expect(switchBeside("Check for updates").getAttribute("aria-checked")).toBe("false");
    expect(switchBeside("Notify only").getAttribute("aria-checked")).toBe("true");
    expect(switchBeside("Short URLs (port 80 redirect)").getAttribute("aria-checked")).toBe("true");
  });
});

describe("Kiosk", () => {
  it("is not shown where the server cannot run a kiosk", async () => {
    await renderLoaded(<SystemSettingsView />);
    await screen.findByText("Check for updates");
    expect(screen.queryByText("Kiosk mode")).toBeNull();
  });

  it("shows the saved values and sends each change", async () => {
    served.version = { ...served.version, kiosk_available: true };
    served.config.kiosk = { enabled: true, target_url: "http://localhost:8080/panel", cursor_visible: true };
    await renderLoaded(<SystemSettingsView />);
    await screen.findByText("Kiosk mode");
    expect(switchBeside("Kiosk mode").getAttribute("aria-checked")).toBe("true");
    expect(switchBeside("Show cursor").getAttribute("aria-checked")).toBe("true");
    expect(fieldBeside("Target URL").value).toBe("http://localhost:8080/panel");
    fireEvent.click(switchBeside("Show cursor"));
    fireEvent.change(fieldBeside("Target URL"), { target: { value: "http://localhost:8080/panel?p=2" } });
    fireEvent.click(switchBeside("Kiosk mode"));
    expect(await saved()).toEqual({
      kiosk: { cursor_visible: false, target_url: "http://localhost:8080/panel?p=2", enabled: false },
    });
  });
});

describe("API key", () => {
  const keyBox = () => screen.getByPlaceholderText("No API key set") as HTMLInputElement;

  it("Generate fills in a new url-safe key of 32 random bytes", async () => {
    await renderLoaded(<SystemSettingsView />);
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    const body = (await saved()) as { auth: { api_key: string } };
    expect(Object.keys(body)).toEqual(["auth"]);
    expect(body.auth.api_key).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });

  it("Generate gives a different key each time", async () => {
    await renderLoaded(<SystemSettingsView />);
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    const first = keyBox().value;
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    expect(keyBox().value).not.toBe(first);
    expect(keyBox().value).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });

  it("offers Copy for a key on screen, and says Copied.", async () => {
    await renderLoaded(<SystemSettingsView />);
    expect(screen.queryByTitle("Copy API key")).toBeNull();
    fireEvent.change(keyBox(), { target: { value: "abc" } });
    fireEvent.click(screen.getByTitle("Copy API key"));
    await waitFor(() => expect(toast.showSuccess).toHaveBeenCalledWith("Copied."));
    expect(clipboard.copyToClipboard).toHaveBeenCalledWith("abc");
  });

  it("says nothing when the copy did not happen", async () => {
    clipboard.copyToClipboard.mockImplementation(async () => false);
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(keyBox(), { target: { value: "abc" } });
    fireEvent.click(screen.getByTitle("Copy API key"));
    await waitFor(() => expect(clipboard.copyToClipboard).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(toast.showSuccess).not.toHaveBeenCalled();
  });

  it("offers no Copy for a stored key the server does not send", async () => {
    served.config.auth.api_key = "***";
    await renderLoaded(<SystemSettingsView />);
    // The stored password and the stored key both show as set.
    expect(screen.getAllByPlaceholderText("Set (hidden)")).toHaveLength(2);
    expect(screen.queryByPlaceholderText("No API key set")).toBeNull();
    expect(screen.queryByTitle("Copy API key")).toBeNull();
  });
});

describe("Panel access", () => {
  it("sends Approved panels only when it is chosen from Anyone on the network", async () => {
    served.config.panels.access = "open";
    await renderLoaded(<SystemSettingsView />);
    const approved = screen.getByText("Approved panels only").closest("label")!.querySelector("input")!;
    fireEvent.click(approved);
    expect(await saved()).toEqual({ panels: { access: "approved" } });
  });

  it("reads anything but the word open as approved panels only", async () => {
    served.config.panels.access = "Open";
    await renderLoaded(<SystemSettingsView />);
    const radio = (text: string) => screen.getByText(text).closest("label")!.querySelector("input") as HTMLInputElement;
    expect(radio("Approved panels only").checked).toBe(true);
    expect(radio("Anyone on the network").checked).toBe(false);
  });
});

describe("The missing-password warning", () => {
  const warning = () => screen.queryByText(/accessible on the network with no authentication/);

  it("is shown with no password and no key on a network choice", async () => {
    served.config.auth = { programmer_username: "", programmer_password: "", api_key: "" };
    await renderLoaded(<SystemSettingsView />);
    expect(warning()).toBeTruthy();
  });

  it.each([
    ["a stored password", { programmer_password: "***", api_key: "" }],
    ["a stored API key", { programmer_password: "", api_key: "***" }],
    ["an API key on screen", { programmer_password: "", api_key: "k" }],
  ])("is not shown with %s", async (_name, auth) => {
    served.config.auth = { programmer_username: "", ...auth };
    await renderLoaded(<SystemSettingsView />);
    expect(warning()).toBeNull();
  });
});

describe("Advertise on the network", () => {
  it("names the chosen adapter's network, or says chosen when it has no name", async () => {
    served.config.network.accept_connections_from = "adapter";
    served.config.network.accept_connections_adapter = { name: "Ethernet 9", mac: "00-00-00-00-00-09" };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByTestId("advertise-help").textContent).toBe(
      "Lets the OpenAVC Panel app on the Ethernet 9 network find this system in its list. Turning this off hides the system from the list; devices can still connect by address.",
    );
  });

  it("says chosen when the adapter has no name at all", async () => {
    served.config.network.accept_connections_from = "adapter";
    served.config.network.accept_connections_adapter = { mac: "00-00-00-00-00-09" };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByTestId("advertise-help").textContent).toContain("on the chosen network");
  });

  it("says what Any network advertises", async () => {
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByTestId("advertise-help").textContent).toBe(
      "Lets the OpenAVC Panel app find this system in its list. Turning this off hides the system from the list; devices can still connect by address.",
    );
  });
});

describe("SSH access", () => {
  const sshSwitch = () => switchBeside("Enable SSH");
  const sshLine = () => screen.getByText("Enable SSH").nextElementSibling!.textContent;

  it("is not shown where the server cannot change SSH", async () => {
    served.ssh = { supported: false, enabled: false };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.queryByText("SSH access")).toBeNull();
    expect(screen.getByText(/Set this if anyone else on your network could open the Programmer IDE\./).textContent)
      .not.toContain("SSH");
  });

  it.each([
    [true, "SSH is on", "true"],
    [false, "SSH is off", "false"],
    [null, "Current state unknown", "false"],
  ])("shows enabled %s as %s", async (enabled, line, checked) => {
    served.ssh = { supported: true, enabled };
    await renderLoaded(<SystemSettingsView />);
    await screen.findByText("SSH access");
    expect(sshLine()).toBe(line);
    expect(sshSwitch().getAttribute("aria-checked")).toBe(checked);
    expect(screen.getByText(/Set this if anyone else/).textContent).toContain(
      " On this controller it is also the SSH and console login for the openavc user.",
    );
  });

  it("turns on, and says so", async () => {
    served.ssh = { supported: true, enabled: false };
    await renderLoaded(<SystemSettingsView />);
    await screen.findByText("SSH access");
    fireEvent.click(sshSwitch());
    await waitFor(() => expect(toast.showSuccess).toHaveBeenCalledWith("SSH enabled."));
    expect(api.setSsh).toHaveBeenCalledWith(true);
    expect(sshLine()).toBe("SSH is on");
    expect(saveButton().disabled).toBe(true);
  });

  it("turns off, and says so", async () => {
    served.ssh = { supported: true, enabled: true };
    await renderLoaded(<SystemSettingsView />);
    await screen.findByText("SSH access");
    fireEvent.click(sshSwitch());
    await waitFor(() => expect(toast.showSuccess).toHaveBeenCalledWith("SSH disabled."));
    expect(api.setSsh).toHaveBeenCalledWith(false);
    expect(sshLine()).toBe("SSH is off");
  });

  it("says Applying... and takes no second click while the change is in flight", async () => {
    served.ssh = { supported: true, enabled: false };
    let answer: (v: { success: boolean }) => void = () => {};
    api.setSsh.mockImplementationOnce(() => new Promise((r) => { answer = r; }));
    await renderLoaded(<SystemSettingsView />);
    await screen.findByText("SSH access");
    fireEvent.click(sshSwitch());
    await waitFor(() => expect(sshLine()).toBe("Applying..."));
    fireEvent.click(sshSwitch());
    expect(api.setSsh).toHaveBeenCalledTimes(1);
    answer({ success: true });
    await waitFor(() => expect(sshLine()).toBe("SSH is on"));
  });

  it("reads the state again when the change is still being confirmed", async () => {
    served.ssh = { supported: true, enabled: false };
    api.setSsh.mockImplementationOnce(async () => ({ success: false, pending: true }));
    await renderLoaded(<SystemSettingsView />);
    await screen.findByText("SSH access");
    served.ssh = { supported: true, enabled: true };
    fireEvent.click(sshSwitch());
    await waitFor(() => expect(toast.showSuccess).toHaveBeenCalledWith("SSH change submitted. Confirming..."));
    await waitFor(() => expect(sshLine()).toBe("SSH is on"));
    expect(api.getSshStatus).toHaveBeenCalledTimes(2);
  });

  it("keeps what it showed when that read fails", async () => {
    served.ssh = { supported: true, enabled: false };
    api.setSsh.mockImplementationOnce(async () => ({ success: false, pending: true }));
    await renderLoaded(<SystemSettingsView />);
    await screen.findByText("SSH access");
    api.getSshStatus.mockRejectedValueOnce(new Error("down"));
    fireEvent.click(sshSwitch());
    await waitFor(() => expect(api.getSshStatus).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(sshLine()).toBe("SSH is off"));
    expect(toast.showError).not.toHaveBeenCalled();
  });

  it.each([
    [{ success: false, error: "systemctl failed" }, "SSH change failed: systemctl failed"],
    [{ success: false }, "SSH change failed: unknown error"],
  ])("says why it failed: %j", async (answer, message) => {
    served.ssh = { supported: true, enabled: false };
    api.setSsh.mockImplementationOnce(async () => answer);
    await renderLoaded(<SystemSettingsView />);
    await screen.findByText("SSH access");
    fireEvent.click(sshSwitch());
    await waitFor(() => expect(toast.showError).toHaveBeenCalledWith(message));
    expect(sshLine()).toBe("SSH is off");
  });

  it("says why when the request itself fails", async () => {
    served.ssh = { supported: true, enabled: false };
    api.setSsh.mockRejectedValueOnce(new ApiError(403, JSON.stringify({ detail: "Not on this install" })));
    await renderLoaded(<SystemSettingsView />);
    await screen.findByText("SSH access");
    fireEvent.click(sshSwitch());
    await waitFor(() => expect(toast.showError).toHaveBeenCalledWith("SSH change failed: Not on this install"));
    await waitFor(() => expect(sshLine()).toBe("SSH is off"));
  });
});

describe("Keyboard focus", () => {
  it.each([
    ["the theme select", () => fieldBeside("Theme"), (el: HTMLElement) => fireEvent.change(el, { target: { value: "light" } })],
    ["Generate", () => screen.getByRole("button", { name: "Generate" }), (el: HTMLElement) => fireEvent.click(el)],
    ["the File logging switch", () => switchBeside("File logging"), (el: HTMLElement) => fireEvent.click(el)],
  ])("stays on %s after it is used", async (_name, find, use) => {
    await renderLoaded(<SystemSettingsView />);
    await screen.findByText("Check for updates");
    const el = find() as HTMLElement;
    el.focus();
    use(el);
    expect(document.activeElement).toBe(find());
    localStorage.removeItem("openavc-theme");
    delete document.documentElement.dataset.theme;
  });

  it("stays on the SSH switch through the change", async () => {
    served.ssh = { supported: true, enabled: false };
    await renderLoaded(<SystemSettingsView />);
    await screen.findByText("SSH access");
    const el = switchBeside("Enable SSH");
    el.focus();
    fireEvent.click(el);
    await waitFor(() => expect(toast.showSuccess).toHaveBeenCalledWith("SSH enabled."));
    expect(document.activeElement).toBe(switchBeside("Enable SSH"));
  });

  it("stays on the Save button's row after a save: nothing on the page is redrawn from scratch", async () => {
    await renderLoaded(<SystemSettingsView />);
    const logLevel = fieldBeside("Log level");
    logLevel.focus();
    fireEvent.change(logLevel, { target: { value: "debug" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.updateSystemConfig).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(saveButton().textContent).toBe("Save"));
    expect(document.activeElement).toBe(fieldBeside("Log level"));
    expect(within(document.body).queryByText("Loading...")).toBeNull();
  });
});

describe("How a setting's row is drawn", () => {
  it.each([
    "Beta includes pre-release versions.",
    "How often to check for new versions.",
    "Paired with the password on the Programmer sign-in screen. Defaults to admin; leave blank to accept any username.",
  ])("puts the help line %j in the control's column, in the help style", async (text) => {
    await renderLoaded(<SystemSettingsView />);
    const line = screen.getByText(text);
    expect(line.tagName).toBe("SPAN");
    expect(line.style.gridColumn).toBe("2");
    expect(line.style.fontSize).toBe("12px");
    expect(line.style.color).toBe("var(--text-muted)");
  });

  it("keeps the API key's box and its buttons on one line inside the lock", async () => {
    await renderLoaded(<SystemSettingsView />);
    const lock = screen.getByRole("button", { name: "Generate" }).closest("fieldset")!;
    expect(lock.style.display).toBe("flex");
    expect(lock.style.alignItems).toBe("center");
    expect(lock.style.gap).toBe("var(--space-sm)");
    expect(lock.contains(screen.getByPlaceholderText("No API key set"))).toBe(true);
  });
});
