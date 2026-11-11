import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, fireEvent, waitFor, act } from "@testing-library/react";

// Settings > Security: the HTTPS switch, where the certificate comes from
// (made here, or uploaded), what the running certificate is, and the
// trusted certificate the cloud can issue. The certificates and the cloud's
// answers are invented.

vi.mock("../api/restClient", async () => (await import("../test/settingsHarness")).api);
vi.mock("../store/projectStore", async () => (await import("../test/settingsHarness")).projectStoreModule);
vi.mock("../store/toastStore", async () => (await import("../test/settingsHarness")).toast);
vi.mock("../components/shared/clipboard", async () => (await import("../test/settingsHarness")).clipboard);
vi.mock("../components/system/HostNetworkCard", () => ({ HostNetworkCard: () => null }));
vi.mock("../components/shared/VariableKeyPicker", () => ({ VariableKeyPicker: () => null }));

import { SystemSettingsView } from "./SystemSettingsView";
import {
  api, CERT, clipboard, cloudCert, fileText, renderLoaded, resetHarness, saveButton, served, toast,
} from "../test/settingsHarness";

function switchBeside(text: string): HTMLElement {
  let el: HTMLElement | null = screen.getByText(text);
  while (el && !el.querySelector("[role=switch]")) el = el.parentElement;
  return el!.querySelector("[role=switch]") as HTMLElement;
}

const fileInputs = () => Array.from(document.querySelectorAll("input[type=file]")) as HTMLInputElement[];
const pem = (name: string, body = "x".repeat(2048)) => new File([body], name, { type: "application/x-pem-file" });

beforeEach(() => {
  resetHarness();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("The HTTPS switch", () => {
  it("says where the Programmer will be, and asks for a restart once switched on", async () => {
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText(/After restart, the Programmer IDE will be at/).textContent)
      .toContain("http://<server>:8080/programmer");
    expect(screen.queryByTestId("restart-banner")).toBeNull();
    fireEvent.click(switchBeside("Enable HTTPS"));
    expect(screen.getByText(/After restart, the Programmer IDE will be at/).textContent)
      .toContain("https://<server>:8443/programmer");
    expect(screen.getByTestId("restart-banner").textContent)
      .toContain("Network or security changes need a restart. Save first, then restart from here.");
    expect(screen.getByText(/You're enabling HTTPS while connected over/).textContent)
      .toContain("https://<server>:8443/programmer");
    expect(saveButton()).not.toBeDisabled();
  });

  it("shows nothing about certificates while it is off", async () => {
    await renderLoaded(<SystemSettingsView />);
    expect(screen.queryByText("Certificate source")).toBeNull();
    expect(screen.queryByLabelText("HTTPS port")).toBeNull();
  });

  it("sends HTTPS on, with a changed port and redirect, on Save", async () => {
    await renderLoaded(<SystemSettingsView />);
    fireEvent.click(switchBeside("Enable HTTPS"));
    fireEvent.change(screen.getByLabelText("HTTPS port"), { target: { value: "9443" } });
    fireEvent.click(switchBeside("Redirect HTTP to HTTPS"));
    expect(screen.getByText(/Old links to/).textContent).toContain("http://<server>:8080");
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.updateSystemConfig).toHaveBeenCalled());
    expect(api.updateSystemConfig.mock.calls[0][0]).toEqual({
      tls: { enabled: true, port: 9443, redirect_http: false },
    });
  });
});

describe("A certificate made here", () => {
  beforeEach(() => {
    served.config.tls.enabled = true;
  });

  it("says one will be made at the next restart before HTTPS runs", async () => {
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText("The self-signed certificate will be generated on the next restart.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Download CA certificate/ })).toBeNull();
    expect(screen.queryByLabelText("Install on")).toBeNull();
  });

  it("shows the running certificate, its fingerprint and its expiry", async () => {
    served.tls = { enabled: true, mode: "auto", cert: CERT };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText(/trusts this server automatically/)).toBeTruthy();
    expect(screen.getByText("Verify fingerprint")).toBeTruthy();
    expect(screen.getAllByText("AA:BB:CC:DD").length).toBe(2);
    expect(screen.getByText("200 days")).toBeTruthy();
    expect(screen.getByText("Current certificate")).toBeTruthy();
    expect(screen.getByText("CN=OpenAVC Root CA")).toBeTruthy();
    expect(screen.getByText(/Valid for:/).parentElement?.textContent).toContain("192.168.1.40, localhost");
  });

  it.each([
    [-1, "Expired"],
    [12, "12 days left"],
    [30, "30 days"],
  ])("labels %i days to expiry as %s", async (days, label) => {
    served.tls = { enabled: true, mode: "auto", cert: { ...CERT, days_until_expiry: days } };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText(label)).toBeTruthy();
  });

  it("says what is wrong with the running certificate", async () => {
    served.tls = {
      enabled: true, mode: "auto",
      cert: { ...CERT, warnings: ["expired", "hostname-mismatch", "odd-thing"] },
      error: "Could not read the key",
    };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText(/Certificate is expired\. Generate a new one; Certificate does not cover this server's current hostname\/IP\. Regenerate after IP change; odd-thing/)).toBeTruthy();
    expect(screen.getByText("Could not read the key")).toBeTruthy();
  });

  it("names an expiring certificate", async () => {
    served.tls = { enabled: true, mode: "auto", cert: { ...CERT, warnings: ["expiring-soon"] } };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText("Certificate expires in under 30 days")).toBeTruthy();
  });

  it("copies the fingerprint and says so for a moment", async () => {
    served.tls = { enabled: true, mode: "auto", cert: CERT };
    await renderLoaded(<SystemSettingsView />);
    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    });
    expect(clipboard.copyToClipboard).toHaveBeenCalledWith("AA:BB:CC:DD");
    expect(screen.getByRole("button", { name: "Copied" })).toBeTruthy();
    await act(async () => {
      vi.advanceTimersByTime(1600);
    });
    expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
  });

  it("says so when the copy did not happen", async () => {
    served.tls = { enabled: true, mode: "auto", cert: CERT };
    clipboard.copyToClipboard.mockResolvedValueOnce(false);
    await renderLoaded(<SystemSettingsView />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(toast.showError).toHaveBeenCalledWith("Couldn't copy to clipboard"));
    expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
  });

  it("downloads the CA certificate as openavc-ca.crt", async () => {
    served.tls = { enabled: true, mode: "auto", cert: CERT };
    const created = vi.fn(() => "blob:ca");
    const revoked = vi.fn();
    Object.assign(URL, { createObjectURL: created, revokeObjectURL: revoked });
    const clicks: string[] = [];
    // Whether the address was already released by the end of the click's own
    // turn: some browsers start the save after click() returns.
    const releasedInTurn: boolean[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicks.push(`${this.download} ${this.getAttribute("href")} ${document.body.contains(this)}`);
      void Promise.resolve().then(() => releasedInTurn.push(revoked.mock.calls.length > 0));
    });
    await renderLoaded(<SystemSettingsView />);
    fireEvent.click(screen.getByRole("button", { name: /Download CA certificate/ }));
    await waitFor(() => expect(toast.showSuccess).toHaveBeenCalledWith(
      "CA certificate downloaded. Install it on your panel devices to skip the security warning.",
    ));
    expect(clicks).toEqual(["openavc-ca.crt blob:ca true"]);
    expect(releasedInTurn).toEqual([false]);
    await waitFor(() => expect(revoked).toHaveBeenCalledWith("blob:ca"));
    expect(document.querySelectorAll("a[download]").length).toBe(0);
    click.mockRestore();
  });

  it("says when the CA certificate could not be downloaded", async () => {
    served.tls = { enabled: true, mode: "auto", cert: CERT };
    api.downloadCertificate.mockRejectedValueOnce(new Error("gone"));
    await renderLoaded(<SystemSettingsView />);
    fireEvent.click(screen.getByRole("button", { name: /Download CA certificate/ }));
    await waitFor(() => expect(toast.showError).toHaveBeenCalledWith("Could not download CA certificate: gone"));
  });

  it("shows the install steps for the chosen system, and keeps them open across a change of mind", async () => {
    served.tls = { enabled: true, mode: "auto", cert: CERT };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.queryByText(/Double-click the downloaded openavc-ca.crt file\./)).toBeNull();
    fireEvent.change(screen.getByLabelText("Install on"), { target: { value: "windows" } });
    fireEvent.click(screen.getByRole("button", { name: "How to install" }));
    expect(screen.getByText("Double-click the downloaded openavc-ca.crt file.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Install on"), { target: { value: "linux-firefox" } });
    expect(screen.getByText(/Pick openavc-ca.crt and tick "Trust this CA to identify websites"\./)).toBeTruthy();
    // Switching HTTPS off and on again, or to your own certificate and back,
    // leaves the choice and the open steps as they were.
    fireEvent.click(switchBeside("Enable HTTPS"));
    fireEvent.click(switchBeside("Enable HTTPS"));
    fireEvent.click(screen.getByRole("radio", { name: "Use my own certificate" }));
    fireEvent.click(screen.getByRole("radio", { name: "Auto-generate (recommended)" }));
    expect((screen.getByLabelText("Install on") as HTMLSelectElement).value).toBe("linux-firefox");
    expect(screen.getByText(/Pick openavc-ca.crt and tick/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "How to install" }));
    expect(screen.queryByText(/Pick openavc-ca.crt and tick/)).toBeNull();
  });

  it.each([
    ["Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", "ios"],
    ["Mozilla/5.0 (Linux; Android 14)", "android"],
    ["Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)", "macos"],
    ["Mozilla/5.0 (X11; Linux x86_64) Firefox/120.0", "linux-firefox"],
    ["Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0", "linux-chrome"],
    ["Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "windows"],
  ])("starts on the system this browser runs on (%s)", async (ua, os) => {
    served.tls = { enabled: true, mode: "auto", cert: CERT };
    const spy = vi.spyOn(navigator, "userAgent", "get").mockReturnValue(ua);
    await renderLoaded(<SystemSettingsView />);
    expect((screen.getByLabelText("Install on") as HTMLSelectElement).value).toBe(os);
    spy.mockRestore();
  });

  it("offers no install steps while the server runs your own certificate", async () => {
    served.tls = { enabled: true, mode: "provided", cert: CERT };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.queryByLabelText("Install on")).toBeNull();
    expect(screen.queryByRole("button", { name: /Download CA certificate/ })).toBeNull();
    expect(screen.getByText("Verify fingerprint")).toBeTruthy();
  });

  it("says nothing about a certificate while HTTPS is not running", async () => {
    served.tls = { enabled: false, mode: "auto", cert: CERT };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText("The self-signed certificate will be generated on the next restart.")).toBeTruthy();
    expect(screen.queryByText("Verify fingerprint")).toBeNull();
    expect(screen.queryByText("Current certificate")).toBeNull();
  });

  it("asks for nothing to be uploaded", async () => {
    served.tls = { enabled: true, mode: "auto", cert: CERT };
    await renderLoaded(<SystemSettingsView />);
    expect(fileInputs()).toHaveLength(0);
  });
});

describe("Choosing where the certificate comes from", () => {
  beforeEach(() => {
    served.config.tls.enabled = true;
  });

  it("clears the file paths when going back to one made here", async () => {
    served.config.tls = { ...served.config.tls, auto_generate: false, cert_file: "/c.pem", key_file: "/k.pem" };
    await renderLoaded(<SystemSettingsView />);
    fireEvent.click(screen.getByRole("radio", { name: "Auto-generate (recommended)" }));
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.updateSystemConfig).toHaveBeenCalled());
    expect(api.updateSystemConfig.mock.calls[0][0]).toEqual({
      tls: { auto_generate: true, cert_file: "", key_file: "" },
    });
  });

  it("asks for an upload once your own certificate is chosen", async () => {
    await renderLoaded(<SystemSettingsView />);
    fireEvent.click(screen.getByRole("radio", { name: "Use my own certificate" }));
    expect(fileInputs()).toHaveLength(2);
    expect(screen.getByText("Upload a certificate before saving.")).toBeTruthy();
    expect(saveButton()).toBeDisabled();
  });
});

describe("Your own certificate", () => {
  beforeEach(() => {
    served.config.tls = { ...served.config.tls, enabled: true, auto_generate: false };
  });

  it("uploads the two picked files and fills in where they were written", async () => {
    await renderLoaded(<SystemSettingsView />);
    const upload = screen.getByRole("button", { name: "Upload certificate" });
    expect(upload).toBeDisabled();
    const [certInput, keyInput] = fileInputs();
    const cert = pem("room.crt");
    const key = pem("room.key", "k".repeat(150));
    fireEvent.change(certInput, { target: { files: [cert] } });
    expect(screen.getByText("room.crt").parentElement?.textContent).toBe("Selected: room.crt (2.1 KB)");
    expect(upload).toBeDisabled();
    fireEvent.change(keyInput, { target: { files: [key] } });
    expect(screen.getByText("room.key").parentElement?.textContent).toBe("Selected: room.key (0.2 KB)");
    fireEvent.click(upload);
    await waitFor(() => expect(api.uploadTlsCert).toHaveBeenCalledWith(cert, key));
    await waitFor(() => expect(screen.getByText("Certificate uploaded")).toBeTruthy());
    expect(toast.showSuccess).toHaveBeenCalledWith("Certificate uploaded. Save and restart to apply.");
    expect(screen.getByText("CN=room.example")).toBeTruthy();
    expect(screen.getByText("11:22:33")).toBeTruthy();
    expect(screen.getByText("20 days left")).toBeTruthy();
    expect(screen.getByText(/Valid for:/).parentElement?.textContent).toBe("Valid for: room.example");
    expect(fileInputs()).toHaveLength(0);
    expect(screen.queryByText("Upload a certificate before saving.")).toBeNull();
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.updateSystemConfig).toHaveBeenCalled());
    expect(api.updateSystemConfig.mock.calls[0][0]).toEqual({
      tls: { cert_file: "/data/tls/cert.pem", key_file: "/data/tls/key.pem" },
    });
  });

  it("warns when what was uploaded is a CA certificate", async () => {
    api.uploadTlsCert.mockResolvedValueOnce({
      cert_path: "/c", key_path: "/k", fingerprint: "FF", subject: "CN=CA", issuer: "CN=CA",
      expires_at: "2027-01-15T00:00:00Z", days_until_expiry: 400, sans: [], warnings: ["is-ca-cert"],
    });
    await renderLoaded(<SystemSettingsView />);
    const [certInput, keyInput] = fileInputs();
    fireEvent.change(certInput, { target: { files: [pem("a.crt")] } });
    fireEvent.change(keyInput, { target: { files: [pem("a.key")] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload certificate" }));
    await waitFor(() => expect(screen.getByText(/This looks like a CA certificate, not a server certificate\./)).toBeTruthy());
    expect(toast.showSuccess).toHaveBeenCalledWith(
      "Certificate uploaded. Save and restart to apply. Warning: this looks like a CA cert, not a server cert. Browsers may not trust it.",
    );
    expect(screen.getByText(/Valid for:/).parentElement?.textContent).toBe("Valid for: —");
  });

  it("says why an upload was refused and keeps what was picked", async () => {
    api.uploadTlsCert.mockRejectedValueOnce(new Error("The key does not match the certificate."));
    await renderLoaded(<SystemSettingsView />);
    const [certInput, keyInput] = fileInputs();
    fireEvent.change(certInput, { target: { files: [pem("a.crt")] } });
    fireEvent.change(keyInput, { target: { files: [pem("a.key")] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload certificate" }));
    await waitFor(() => expect(toast.showError).toHaveBeenCalledWith("The key does not match the certificate."));
    expect(screen.getByText("a.crt")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Upload certificate" })).not.toBeDisabled();
  });

  it("uploads pasted text as two files", async () => {
    await renderLoaded(<SystemSettingsView />);
    expect(document.querySelectorAll("textarea")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Or paste PEM contents" }));
    const [certText, keyText] = Array.from(document.querySelectorAll("textarea"));
    const use = screen.getByRole("button", { name: "Use pasted certificate" });
    expect(use).toBeDisabled();
    fireEvent.change(certText, { target: { value: "-----BEGIN CERTIFICATE-----\nAAA" } });
    fireEvent.change(keyText, { target: { value: "   " } });
    expect(use).toBeDisabled();
    fireEvent.change(keyText, { target: { value: "-----BEGIN PRIVATE KEY-----\nBBB" } });
    fireEvent.click(use);
    await waitFor(() => expect(api.uploadTlsCert).toHaveBeenCalled());
    const [cert, key] = api.uploadTlsCert.mock.calls[0];
    expect(cert.name).toBe("cert.pem");
    expect(key.name).toBe("key.pem");
    expect(cert.type).toBe("application/x-pem-file");
    expect(await fileText(cert)).toBe("-----BEGIN CERTIFICATE-----\nAAA");
    expect(await fileText(key)).toBe("-----BEGIN PRIVATE KEY-----\nBBB");
    await waitFor(() => expect(screen.getByText("Certificate uploaded")).toBeTruthy());
  });

  it("asks for both files again while only one is set", async () => {
    served.config.tls = { ...served.config.tls, cert_file: "/c.pem", key_file: "" };
    await renderLoaded(<SystemSettingsView />);
    expect(fileInputs()).toHaveLength(2);
    expect(screen.getByText("Upload a certificate before saving.")).toBeTruthy();
  });

  it("forgets the uploaded files' paths on Replace", async () => {
    await renderLoaded(<SystemSettingsView />);
    const [certInput, keyInput] = fileInputs();
    fireEvent.change(certInput, { target: { files: [pem("a.crt")] } });
    fireEvent.change(keyInput, { target: { files: [pem("a.key")] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload certificate" }));
    await waitFor(() => expect(screen.getByText("Certificate uploaded")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Replace certificate" }));
    fireEvent.click(switchBeside("Enable HTTPS"));
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.updateSystemConfig).toHaveBeenCalled());
    expect(api.updateSystemConfig.mock.calls[0][0]).toEqual({
      tls: { cert_file: "", key_file: "", enabled: false },
    });
  });

  it("goes back to the pickers, empty, on Replace", async () => {
    await renderLoaded(<SystemSettingsView />);
    fireEvent.click(screen.getByRole("button", { name: "Or paste PEM contents" }));
    const [certText, keyText] = Array.from(document.querySelectorAll("textarea"));
    fireEvent.change(certText, { target: { value: "C" } });
    fireEvent.change(keyText, { target: { value: "K" } });
    fireEvent.click(screen.getByRole("button", { name: "Use pasted certificate" }));
    await waitFor(() => expect(screen.getByText("Certificate uploaded")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Replace certificate" }));
    expect(screen.queryByText("Certificate uploaded")).toBeNull();
    expect(fileInputs()).toHaveLength(2);
    expect(screen.queryByText(/^Selected:/)).toBeNull();
    // The paste box closed when the upload worked, and comes back empty.
    expect(document.querySelectorAll("textarea")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Or paste PEM contents" }));
    expect(Array.from(document.querySelectorAll("textarea")).map((t) => t.value)).toEqual(["", ""]);
    expect(screen.getByText("Upload a certificate before saving.")).toBeTruthy();
  });

  it("shows the certificate the server runs, and keeps the pickers away while it is set", async () => {
    served.config.tls = { ...served.config.tls, cert_file: "/c.pem", key_file: "/k.pem" };
    served.tls = { enabled: true, mode: "provided", cert: { ...CERT, subject: "CN=hall.example" } };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText("Active certificate")).toBeTruthy();
    expect(screen.getAllByText("CN=hall.example").length).toBe(2);
    expect(fileInputs()).toHaveLength(0);
    expect(screen.queryByLabelText("Install on")).toBeNull();
  });

  it("shows an upload's facts in place of the running certificate's", async () => {
    served.tls = { enabled: true, mode: "provided", cert: { ...CERT, subject: "CN=hall.example" } };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText("Active certificate")).toBeTruthy();
    const [certInput, keyInput] = fileInputs();
    fireEvent.change(certInput, { target: { files: [pem("a.crt")] } });
    fireEvent.change(keyInput, { target: { files: [pem("a.key")] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload certificate" }));
    await waitFor(() => expect(screen.getByText("Certificate uploaded")).toBeTruthy());
    expect(screen.getByText("CN=room.example")).toBeTruthy();
    // The running certificate is still named once, by the status block.
    expect(screen.getAllByText("CN=hall.example")).toHaveLength(1);
    expect(screen.getByText("20 days left")).toBeTruthy();
  });

  it("keeps a picked file and pasted text across a switch to one made here and back", async () => {
    await renderLoaded(<SystemSettingsView />);
    fireEvent.change(fileInputs()[0], { target: { files: [pem("keep.crt")] } });
    fireEvent.click(screen.getByRole("button", { name: "Or paste PEM contents" }));
    fireEvent.change(document.querySelectorAll("textarea")[0], { target: { value: "PASTED" } });
    fireEvent.click(screen.getByRole("radio", { name: "Auto-generate (recommended)" }));
    expect(fileInputs()).toHaveLength(0);
    fireEvent.click(screen.getByRole("radio", { name: "Use my own certificate" }));
    expect(screen.getByText("keep.crt")).toBeTruthy();
    expect((document.querySelectorAll("textarea")[0] as HTMLTextAreaElement).value).toBe("PASTED");
    // And across HTTPS off and on.
    fireEvent.click(switchBeside("Enable HTTPS"));
    fireEvent.click(switchBeside("Enable HTTPS"));
    expect(screen.getByText("keep.crt")).toBeTruthy();
  });

  it("keeps an upload across a switch to one made here and back, ready to save", async () => {
    await renderLoaded(<SystemSettingsView />);
    const [certInput, keyInput] = fileInputs();
    fireEvent.change(certInput, { target: { files: [pem("a.crt")] } });
    fireEvent.change(keyInput, { target: { files: [pem("a.key")] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload certificate" }));
    await waitFor(() => expect(screen.getByText("Certificate uploaded")).toBeTruthy());
    fireEvent.click(screen.getByRole("radio", { name: "Auto-generate (recommended)" }));
    fireEvent.click(screen.getByRole("radio", { name: "Use my own certificate" }));
    expect(screen.getByText("Certificate uploaded")).toBeTruthy();
    // Coming back puts the upload's files back, so Save points HTTPS at them
    // without uploading again.
    expect(screen.queryByText("Upload a certificate before saving.")).toBeNull();
    expect(saveButton()).not.toBeDisabled();
  });
});

describe("The trusted certificate", () => {
  it("is not offered without a cloud pairing", async () => {
    served.tls = { enabled: false, cloud_cert: cloudCert({ paired: false }) };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.queryByText("Trusted certificate")).toBeNull();
  });

  it("stays on screen once switched on, even with the pairing gone", async () => {
    served.config.tls.enabled = true;
    served.config.tls.cloud_cert = true;
    served.tls = { enabled: true, cloud_cert: cloudCert({ paired: false, enabled: true }) };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByRole("button", { name: "Turn off trusted certificate" })).toBeTruthy();
  });

  it("is not offered when the server says nothing about it", async () => {
    served.tls = { enabled: false };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.queryByText("Trusted certificate")).toBeNull();
  });

  it("turns HTTPS on and asks for the restart when HTTPS is off", async () => {
    served.tls = { enabled: false, cloud_cert: cloudCert() };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText(/HTTPS is currently off\. This turns it on \(one restart\)/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Turn on HTTPS & get a trusted certificate" }));
    await waitFor(() => expect(screen.getByText("Restart to apply changes?")).toBeTruthy());
    expect(api.updateSystemConfig).toHaveBeenCalledWith({ tls: { enabled: true, cloud_cert: true } });
    expect(api.enableCloudCert).not.toHaveBeenCalled();
    expect(api.getSystemConfig).toHaveBeenCalledTimes(2);
    expect(toast.showSuccess).toHaveBeenCalledWith(
      "HTTPS enabled. Restart to finish; the trusted certificate installs automatically after the restart.",
    );
  });

  it("asks the cloud for one when HTTPS is on, and watches until it settles", async () => {
    served.config.tls.enabled = true;
    served.tls = { enabled: true, cloud_cert: cloudCert() };
    await renderLoaded(<SystemSettingsView />);
    vi.useFakeTimers();
    served.tls = { enabled: true, cloud_cert: cloudCert({ enabled: true, phase: "issuing" }) };
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Get a trusted certificate" }));
    });
    expect(api.enableCloudCert).toHaveBeenCalled();
    expect(toast.showSuccess).toHaveBeenCalledWith("Requesting a trusted certificate...");
    const asked = api.getTlsStatus.mock.calls.length;
    await act(async () => { vi.advanceTimersByTime(2000); });
    expect(api.getTlsStatus.mock.calls.length).toBe(asked + 1);
    expect(screen.getByText(/Requesting a certificate from the cloud/)).toBeTruthy();
    served.tls = { enabled: true, cloud_cert: cloudCert({ enabled: true, active: true }) };
    await act(async () => { vi.advanceTimersByTime(2000); });
    expect(screen.getByText(/Serving a trusted certificate for/)).toBeTruthy();
    const settled = api.getTlsStatus.mock.calls.length;
    await act(async () => { vi.advanceTimersByTime(10000); });
    expect(api.getTlsStatus.mock.calls.length).toBe(settled);
  });

  it("stops watching after two minutes of no answer", async () => {
    served.config.tls.enabled = true;
    served.tls = { enabled: true, cloud_cert: cloudCert() };
    await renderLoaded(<SystemSettingsView />);
    vi.useFakeTimers();
    api.getTlsStatus.mockRejectedValue(new Error("down"));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Get a trusted certificate" }));
    });
    const before = api.getTlsStatus.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(2000 * 61); });
    const after = api.getTlsStatus.mock.calls.length;
    expect(after - before).toBe(61);
    await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
    expect(api.getTlsStatus.mock.calls.length).toBe(after);
  });

  it("stops watching after two minutes of an issuance that never settles", async () => {
    served.config.tls.enabled = true;
    served.tls = { enabled: true, cloud_cert: cloudCert() };
    await renderLoaded(<SystemSettingsView />);
    vi.useFakeTimers();
    served.tls = { enabled: true, cloud_cert: cloudCert({ enabled: true, phase: "issuing" }) };
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Get a trusted certificate" }));
    });
    const before = api.getTlsStatus.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(2000 * 61); });
    const after = api.getTlsStatus.mock.calls.length;
    expect(after - before).toBe(61);
    await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
    expect(api.getTlsStatus.mock.calls.length).toBe(after);
  });

  it("stops watching when Settings closes", async () => {
    served.config.tls.enabled = true;
    served.tls = { enabled: true, cloud_cert: cloudCert() };
    const { unmount } = await renderLoaded(<SystemSettingsView />);
    vi.useFakeTimers();
    served.tls = { enabled: true, cloud_cert: cloudCert({ enabled: true, phase: "issuing" }) };
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Get a trusted certificate" }));
    });
    unmount();
    const before = api.getTlsStatus.mock.calls.length;
    await act(async () => { vi.advanceTimersByTime(10000); });
    expect(api.getTlsStatus.mock.calls.length).toBe(before);
  });

  it("keeps watching while HTTPS is switched off on screen", async () => {
    served.config.tls.enabled = true;
    served.tls = { enabled: true, cloud_cert: cloudCert() };
    await renderLoaded(<SystemSettingsView />);
    vi.useFakeTimers();
    served.tls = { enabled: true, cloud_cert: cloudCert({ enabled: true, phase: "issuing" }) };
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Get a trusted certificate" }));
    });
    fireEvent.click(switchBeside("Enable HTTPS"));
    const before = api.getTlsStatus.mock.calls.length;
    await act(async () => { vi.advanceTimersByTime(4000); });
    expect(api.getTlsStatus.mock.calls.length).toBe(before + 2);
  });

  it("says the cloud's message when it does not start at once", async () => {
    served.config.tls.enabled = true;
    served.tls = { enabled: true, cloud_cert: cloudCert() };
    api.enableCloudCert.mockResolvedValueOnce({ status: "ok", enabled: true, started: false, message: "Waiting for the cloud." } as never);
    await renderLoaded(<SystemSettingsView />);
    fireEvent.click(screen.getByRole("button", { name: "Get a trusted certificate" }));
    await waitFor(() => expect(toast.showSuccess).toHaveBeenCalledWith("Waiting for the cloud."));
  });

  it("says Enrollment queued when the cloud gives no message", async () => {
    served.config.tls.enabled = true;
    served.tls = { enabled: true, cloud_cert: cloudCert() };
    api.enableCloudCert.mockResolvedValueOnce({ status: "ok", enabled: true, started: false } as never);
    await renderLoaded(<SystemSettingsView />);
    fireEvent.click(screen.getByRole("button", { name: "Get a trusted certificate" }));
    await waitFor(() => expect(toast.showSuccess).toHaveBeenCalledWith("Enrollment queued."));
  });

  it("says why enrollment could not start, and is ready to try again", async () => {
    served.config.tls.enabled = true;
    served.tls = { enabled: true, cloud_cert: cloudCert() };
    api.enableCloudCert.mockRejectedValueOnce(new Error("not paired"));
    await renderLoaded(<SystemSettingsView />);
    const get = screen.getByRole("button", { name: "Get a trusted certificate" });
    fireEvent.click(get);
    expect(screen.getByRole("button", { name: "Working..." })).toBeDisabled();
    await waitFor(() => expect(toast.showError).toHaveBeenCalledWith("Could not start enrollment: not paired"));
    expect(screen.getByRole("button", { name: "Get a trusted certificate" })).not.toBeDisabled();
  });

  it.each([
    [{ phase: "enrolling" }, true, /Requesting a certificate from the cloud\.\.\. this usually takes under a minute\./],
    [{}, false, /Waiting for HTTPS to start \(restart pending\)/],
    [{ last_error: "rate_limited", last_error_detail: "5 today" }, true, /Too many certificate requests today\. An automatic retry is scheduled\./],
    [{ last_error: "weird_code" }, true, /Issuance failed \(weird_code\)\./],
    [{ available: false }, true, /Waiting for the cloud connection\./],
    [{}, true, /Preparing the certificate\.\.\./],
  ])("says where it is once on (%o, HTTPS running %s)", async (over, running, sentence) => {
    served.config.tls.enabled = true;
    served.config.tls.cloud_cert = true;
    served.tls = { enabled: running, cloud_cert: cloudCert({ enabled: true, ...over }) };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText(sentence)).toBeTruthy();
  });

  it("shows the error's detail and tries again now", async () => {
    served.config.tls.enabled = true;
    served.config.tls.cloud_cert = true;
    served.tls = { enabled: true, cloud_cert: cloudCert({ enabled: true, last_error: "timeout", last_error_detail: "no answer in 60 s" }) };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText("no answer in 60 s")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again now" }));
    await waitFor(() => expect(api.enableCloudCert).toHaveBeenCalled());
  });

  it("gives the certified address from the address this page was opened at", async () => {
    served.config.tls.enabled = true;
    served.config.tls.cloud_cert = true;
    served.tls = {
      enabled: true, port: 8443,
      cloud_cert: cloudCert({ enabled: true, active: true, expires_at: "2027-02-01T00:00:00Z", renews_at: "2027-01-01T00:00:00Z" }),
    };
    served.adapters = [{ name: "eth0", ip: "10.20.30.5" }];
    await renderLoaded(<SystemSettingsView />);
    // jsdom opens the page at localhost, so the first adapter's IPv4 is used.
    expect(screen.getByText("https://10-20-30-5.abc123.certs.example.net:8443/")).toBeTruthy();
    expect(screen.getByText("https://10.20.30.5:8443/")).toBeTruthy();
    expect(screen.getByText(/renews automatically around/)).toBeTruthy();
    fireEvent.click(screen.getByTitle("Copy certified address"));
    await waitFor(() => expect(toast.showSuccess).toHaveBeenCalledWith("Copied."));
    expect(clipboard.copyToClipboard).toHaveBeenCalledWith("https://10-20-30-5.abc123.certs.example.net:8443/");
  });

  it("gives the port HTTPS runs on, before the one on screen", async () => {
    served.config.tls = { ...served.config.tls, enabled: true, cloud_cert: true, port: 8443 };
    served.tls = { enabled: true, port: 9443, cloud_cert: cloudCert({ enabled: true, active: true }) };
    served.adapters = [{ name: "eth0", ip: "10.20.30.5" }];
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText("https://10-20-30-5.abc123.certs.example.net:9443/")).toBeTruthy();
    expect(screen.getByText("https://10.20.30.5:9443/")).toBeTruthy();
  });

  it("gives the port on screen when the server does not say its own", async () => {
    served.config.tls = { ...served.config.tls, enabled: true, cloud_cert: true, port: 7443 };
    served.tls = { enabled: true, cloud_cert: cloudCert({ enabled: true, active: true }) };
    served.adapters = [{ name: "eth0", ip: "10.20.30.5" }];
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText("https://10.20.30.5:7443/")).toBeTruthy();
  });

  it("gives no certified address without an IPv4 to put in it", async () => {
    served.config.tls.enabled = true;
    served.config.tls.cloud_cert = true;
    served.tls = { enabled: true, cloud_cert: cloudCert({ enabled: true, active: true }) };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText(/Serving a trusted certificate for/)).toBeTruthy();
    expect(screen.queryByText(/Certified address:/)).toBeNull();
    expect(screen.queryByText(/Manual fallback/)).toBeNull();
  });

  it("turns off, and reads the config again", async () => {
    served.config.tls.enabled = true;
    served.config.tls.cloud_cert = true;
    served.tls = { enabled: true, cloud_cert: cloudCert({ enabled: true, active: true }) };
    await renderLoaded(<SystemSettingsView />);
    fireEvent.click(screen.getByRole("button", { name: "Turn off trusted certificate" }));
    await waitFor(() => expect(toast.showSuccess).toHaveBeenCalledWith("Trusted certificate turned off."));
    expect(api.disableCloudCert).toHaveBeenCalled();
    await waitFor(() => expect(api.getSystemConfig).toHaveBeenCalledTimes(2));
  });

  it("says why it could not turn off", async () => {
    served.config.tls.enabled = true;
    served.config.tls.cloud_cert = true;
    served.tls = { enabled: true, cloud_cert: cloudCert({ enabled: true, active: true }) };
    api.disableCloudCert.mockRejectedValueOnce(new Error("busy"));
    await renderLoaded(<SystemSettingsView />);
    fireEvent.click(screen.getByRole("button", { name: "Turn off trusted certificate" }));
    await waitFor(() => expect(toast.showError).toHaveBeenCalledWith("Could not turn off the trusted certificate: busy"));
  });

  it("draws the callout green while paired and not yet on", async () => {
    served.tls = { enabled: false, cloud_cert: cloudCert() };
    await renderLoaded(<SystemSettingsView />);
    const card = screen.getByText("Trusted certificate").closest("div") as HTMLElement;
    expect(card.style.border).toBe("1px solid rgba(76, 175, 80, 0.45)");
  });
});

describe("The running certificate after a save", () => {
  it("is read again, and no longer shown when that read fails", async () => {
    served.config.tls.enabled = true;
    served.tls = { enabled: true, mode: "auto", cert: CERT };
    await renderLoaded(<SystemSettingsView />);
    expect(screen.getByText("Current certificate")).toBeTruthy();
    api.getTlsStatus.mockRejectedValueOnce(new Error("down"));
    fireEvent.change(screen.getByLabelText("Log level"), { target: { value: "debug" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.getTlsStatus).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText("Current certificate")).toBeNull());
  });
});

describe("The HTTPS port box", () => {
  it("takes a port from 1 to 65535", async () => {
    served.config.tls.enabled = true;
    await renderLoaded(<SystemSettingsView />);
    const box = screen.getByLabelText("HTTPS port") as HTMLInputElement;
    expect(box.type).toBe("number");
    expect(box.min).toBe("1");
    expect(box.max).toBe("65535");
  });
});
