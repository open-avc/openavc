import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, within } from "@testing-library/react";

// Two things every card on Settings shares: a field the service's environment
// holds is locked and says where it is set, and a few rules refuse the Save
// before it is sent. Each held field is pinned one by one, because the lock
// and the line under it are written per field.

vi.mock("../api/restClient", async () => (await import("../test/settingsHarness")).api);
vi.mock("../store/projectStore", async () => (await import("../test/settingsHarness")).projectStoreModule);
vi.mock("../store/toastStore", async () => (await import("../test/settingsHarness")).toast);
vi.mock("../components/shared/clipboard", async () => (await import("../test/settingsHarness")).clipboard);
vi.mock("../components/system/HostNetworkCard", () => ({ HostNetworkCard: () => null }));
vi.mock("../components/shared/VariableKeyPicker", () => ({ VariableKeyPicker: () => null }));

import { SystemSettingsView } from "./SystemSettingsView";
import { cloudCert, renderLoaded, resetHarness, saveButton, served } from "../test/settingsHarness";

/** The switch in the same row as a piece of text. */
function switchBeside(text: string): HTMLElement {
  let el: HTMLElement | null = screen.getByText(text);
  while (el && !within(el).queryByRole("switch")) el = el.parentElement;
  return within(el!).getByRole("switch");
}

function hold(fields: Record<string, string>, deploymentType = "linux_package", owner?: string) {
  served.config._environment = {
    deployment_type: deploymentType,
    overrides: fields,
    ...(owner ? { configuration_owner: owner } : {}),
  };
}

const notes = () => screen.queryAllByTestId("env-held-note");

beforeEach(() => {
  resetHarness();
});

describe("Each field the environment can hold", () => {
  const cases: Array<[string, string, () => HTMLElement, () => void]> = [
    ["network.http_port", "OPENAVC_PORT", () => screen.getByLabelText("HTTP port"), () => {}],
    ["network.port80_redirect", "OPENAVC_PORT80_REDIRECT", () => switchBeside("Short URLs (port 80 redirect)"), () => {}],
    ["discovery.advertise", "OPENAVC_MDNS_ADVERTISE", () => screen.getByRole("switch", { name: "Advertise on the network" }), () => {}],
    ["tls.enabled", "OPENAVC_TLS", () => switchBeside("Enable HTTPS"), () => {}],
    ["tls.auto_generate", "OPENAVC_TLS_AUTO", () => screen.getByRole("radio", { name: "Auto-generate (recommended)" }),
      () => { served.config.tls.enabled = true; }],
    ["tls.cert_file", "OPENAVC_TLS_CERT", () => screen.getByRole("radio", { name: "Use my own certificate" }),
      () => { served.config.tls.enabled = true; }],
    ["tls.key_file", "OPENAVC_TLS_KEY", () => screen.getByRole("radio", { name: "Auto-generate (recommended)" }),
      () => { served.config.tls.enabled = true; }],
    ["tls.port", "OPENAVC_TLS_PORT", () => screen.getByLabelText("HTTPS port"), () => { served.config.tls.enabled = true; }],
    ["tls.redirect_http", "OPENAVC_TLS_REDIRECT", () => switchBeside("Redirect HTTP to HTTPS"), () => { served.config.tls.enabled = true; }],
    ["auth.programmer_username", "OPENAVC_PROGRAMMER_USERNAME", () => screen.getByPlaceholderText("No username set"), () => {}],
    ["auth.programmer_password", "OPENAVC_PROGRAMMER_PASSWORD", () => screen.getByPlaceholderText("Set (hidden)"), () => {}],
    ["auth.api_key", "OPENAVC_API_KEY", () => screen.getByRole("button", { name: "Generate" }), () => {}],
    ["logging.level", "OPENAVC_LOG_LEVEL", () => screen.getByLabelText("Log level"), () => {}],
    ["updates.check_enabled", "OPENAVC_UPDATE_CHECK", () => switchBeside("Check for updates"), () => {}],
    ["updates.channel", "OPENAVC_UPDATE_CHANNEL", () => screen.getByDisplayValue("Stable"), () => {}],
  ];

  it.each(cases)("%s is locked, with one line naming %s", async (field, variable, control, arrange) => {
    arrange();
    hold({ [field]: variable });
    await renderLoaded(<SystemSettingsView />);
    expect(control()).toBeDisabled();
    expect(notes()).toHaveLength(1);
    expect(notes()[0].textContent).toContain(`Set by the ${variable} line in a systemd override`);
  });

  it("locks nothing and says nothing when nothing is held", async () => {
    served.config.tls.enabled = true;
    await renderLoaded(<SystemSettingsView />);
    expect(notes()).toHaveLength(0);
    expect(screen.getByLabelText("HTTP port")).not.toBeDisabled();
    expect(screen.getByLabelText("HTTPS port")).not.toBeDisabled();
    expect(screen.getByRole("radio", { name: "Use my own certificate" })).not.toBeDisabled();
  });

  it("says the firmware manages it on product hardware, wherever the field is", async () => {
    hold({ "logging.level": "OPENAVC_LOG_LEVEL", "network.http_port": "OPENAVC_PORT" }, "android_appliance", "firmware");
    await renderLoaded(<SystemSettingsView />);
    expect(notes()).toHaveLength(2);
    for (const note of notes()) {
      expect(note.textContent).toContain("This setting is managed by the product's firmware.");
      expect(note.textContent).not.toContain("OPENAVC_");
    }
  });

  it("names the place for the kind of install", async () => {
    hold({ "logging.level": "OPENAVC_LOG_LEVEL" }, "docker");
    await renderLoaded(<SystemSettingsView />);
    expect(notes()[0].textContent).toContain("Set by OPENAVC_LOG_LEVEL in the container's environment.");
  });

  it("puts each line under its field in the grid", async () => {
    served.config.tls.enabled = true;
    hold({ "network.http_port": "OPENAVC_PORT", "tls.port": "OPENAVC_TLS_PORT", "panels.access": "OPENAVC_PANEL_ACCESS" });
    await renderLoaded(<SystemSettingsView />);
    const byVar = (v: string) => notes().find((n) => n.textContent?.includes(v))!;
    expect(byVar("OPENAVC_PORT ").style.gridColumn).toBe("2");
    expect(byVar("OPENAVC_TLS_PORT").style.gridColumn).toBe("2");
    expect(byVar("OPENAVC_PANEL_ACCESS").style.marginBottom).toBe("var(--space-sm)");
  });

  it("locks the trusted certificate while HTTPS is off and its switch is held", async () => {
    served.tls = { enabled: false, cloud_cert: cloudCert() };
    hold({ "tls.enabled": "OPENAVC_TLS" });
    await renderLoaded(<SystemSettingsView />);
    const get = screen.getByRole("button", { name: "Turn on HTTPS & get a trusted certificate" });
    expect(get).toBeDisabled();
    // The HTTPS switch's own line, and the card's.
    expect(notes()).toHaveLength(2);
    expect(notes()[1].style.marginBottom).toBe("var(--space-md)");
  });

  it("leaves the trusted certificate free once HTTPS is on, even with its switch held", async () => {
    served.config.tls.enabled = true;
    served.tls = { enabled: true, cloud_cert: cloudCert() };
    hold({ "tls.enabled": "OPENAVC_TLS" });
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByRole("button", { name: "Get a trusted certificate" })).not.toBeDisabled();
    expect(notes()).toHaveLength(1);
  });

  it("locks turning the trusted certificate off when its own flag is held", async () => {
    served.config.tls.enabled = true;
    served.config.tls.cloud_cert = true;
    served.tls = { enabled: true, cloud_cert: cloudCert({ enabled: true, active: true }) };
    hold({ "tls.cloud_cert": "OPENAVC_CLOUD_CERT" });
    await renderLoaded(<SystemSettingsView />);
    const off = screen.getByRole("button", { name: "Turn off trusted certificate" });
    expect(off).toBeDisabled();
    expect(off.style.opacity).toBe("0.5");
    expect(notes()).toHaveLength(1);
    expect(notes()[0].style.marginBottom).toBe("var(--space-sm)");
  });
});

describe("Save is refused before it is sent", () => {
  it("for an API key with no password, once a credential is edited", async () => {
    served.config.auth = { programmer_username: "", programmer_password: "", api_key: "" };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.queryByText(/An API key can't sign in to the Programmer\./)).toBeNull();
    fireEvent.change(screen.getByPlaceholderText("No API key set"), { target: { value: "k".repeat(40) } });
    expect(screen.getByText(/An API key can't sign in to the Programmer\./)).toBeTruthy();
    expect(saveButton()).toBeDisabled();
    expect(saveButton().title).toBe("Fix the validation errors below before saving");
    fireEvent.change(screen.getByPlaceholderText("No password set"), { target: { value: "longenough1" } });
    expect(screen.queryByText(/An API key can't sign in to the Programmer\./)).toBeNull();
    expect(saveButton()).not.toBeDisabled();
    expect(saveButton().title).toBe("");
  });

  it("not for a system already in that state that changes something else", async () => {
    served.config.auth = { programmer_username: "", programmer_password: "", api_key: "***" };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText(/An API key can't sign in to the Programmer\./)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Log level"), { target: { value: "debug" } });
    expect(saveButton()).not.toBeDisabled();
  });

  it("for an HTTPS port the HTTP port already uses", async () => {
    served.config.tls.enabled = true;
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(screen.getByLabelText("HTTPS port"), { target: { value: "8080" } });
    expect(screen.getByText("HTTPS port must differ from HTTP port")).toBeTruthy();
    expect(screen.getByLabelText("HTTPS port").style.borderColor).toBe("rgb(244, 67, 54)");
    expect(saveButton()).toBeDisabled();
    fireEvent.change(screen.getByLabelText("HTTPS port"), { target: { value: "9443" } });
    expect(screen.queryByText("HTTPS port must differ from HTTP port")).toBeNull();
    expect(saveButton()).not.toBeDisabled();
  });

  it("for an HTTPS port out of range, and an empty box reads as 8443", async () => {
    served.config.tls.enabled = true;
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(screen.getByLabelText("HTTPS port"), { target: { value: "70000" } });
    expect(screen.getByText("Port must be between 1 and 65535")).toBeTruthy();
    expect(saveButton()).toBeDisabled();
    fireEvent.change(screen.getByLabelText("HTTPS port"), { target: { value: "" } });
    expect((screen.getByLabelText("HTTPS port") as HTMLInputElement).value).toBe("8443");
    expect(saveButton()).not.toBeDisabled();
  });

  it("never for the HTTPS port while HTTPS is off", async () => {
    served.config.tls = { ...served.config.tls, enabled: false, port: 8080 };
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(screen.getByLabelText("Log level"), { target: { value: "debug" } });
    expect(saveButton()).not.toBeDisabled();
  });

  it("for your own certificate with nothing uploaded", async () => {
    served.config.tls = { ...served.config.tls, enabled: true, auto_generate: false, cert_file: "", key_file: "" };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText("Upload a certificate before saving.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Log level"), { target: { value: "debug" } });
    expect(saveButton()).toBeDisabled();
  });

  it("not for your own certificate once its files are set", async () => {
    served.config.tls = { ...served.config.tls, enabled: true, auto_generate: false, cert_file: "/c.pem", key_file: "/k.pem" };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.queryByText("Upload a certificate before saving.")).toBeNull();
    fireEvent.change(screen.getByLabelText("Log level"), { target: { value: "debug" } });
    expect(saveButton()).not.toBeDisabled();
  });

  it("for a file path that is only spaces", async () => {
    served.config.tls = { ...served.config.tls, enabled: true, auto_generate: false, cert_file: "  ", key_file: "/k.pem" };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText("Upload a certificate before saving.")).toBeTruthy();
  });
});
