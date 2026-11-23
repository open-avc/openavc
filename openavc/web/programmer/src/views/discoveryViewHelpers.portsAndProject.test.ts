import { describe, it, expect } from "vitest";

import { openPortsText, projectDeviceAtAddress } from "./discoveryViewHelpers";
import type { ProjectConfig } from "../api/types";

// The Open Ports line draws what the server sends: each port, the name it
// gave the port, and the project device that holds it.
describe("openPortsText", () => {
  it("names each port the server named, and leaves an unnamed one bare", () => {
    expect(openPortsText([
      { port: 23, label: "Telnet", in_use_by: null },
      { port: 8000, label: null, in_use_by: null },
    ])).toBe("23 (Telnet), 8000");
  });

  it("marks a held port in use by its device", () => {
    expect(openPortsText([
      { port: 17300, label: "Acme Widget", in_use_by: "Lobby Widget" },
      { port: 4100, label: null, in_use_by: "Lobby Widget" },
    ])).toBe("17300 (Acme Widget, in use by Lobby Widget), 4100 (in use by Lobby Widget)");
  });

  it("says none were found when there are none", () => {
    expect(openPortsText([])).toBe("None detected");
  });
});

// "+ Add to Project" was remembered only by the card that was clicked, so a
// reload offered it again for a device already in the project.
describe("projectDeviceAtAddress", () => {
  const project = {
    devices: [
      { id: "acme_widget_10_0_0_5", driver: "acme_widget", name: "Acme WX-100", config: {} },
      { id: "acme_old", driver: "acme_widget", name: "Old Widget", config: { host: "10.0.0.9" } },
      { id: "acme_named", driver: "acme_widget", name: "Named Widget", config: {} },
    ],
    connections: {
      acme_widget_10_0_0_5: { host: "10.0.0.5", port: 4000 },
      acme_named: { host: "Widget-Lobby.local" },
    },
  } as unknown as ProjectConfig;

  it("finds the device that connects to the scanned address", () => {
    expect(projectDeviceAtAddress(project, "10.0.0.5", null)).toBe("acme_widget_10_0_0_5");
    expect(projectDeviceAtAddress(project, "10.0.0.9", null)).toBe("acme_old");
  });

  it("finds one added by the hostname the scan read", () => {
    expect(projectDeviceAtAddress(project, "10.0.0.7", "widget-lobby.local")).toBe("acme_named");
  });

  it("finds nothing for an address no device uses", () => {
    expect(projectDeviceAtAddress(project, "10.0.0.6", null)).toBeNull();
    expect(projectDeviceAtAddress(null, "10.0.0.5", null)).toBeNull();
  });
});
