import { describe, it, expect } from "vitest";

import { openPortLabel, projectDeviceAtAddress } from "./discoveryViewHelpers";
import type { ProjectConfig } from "../api/types";

// A port's label names the device's own driver when that driver claims the
// port; otherwise the scan's shared label, which never picks one of several
// drivers at random.
describe("openPortLabel", () => {
  const labels = { 17300: "Acme", 17200: "Acme Widget Pro", 23: "Telnet" };
  const portDrivers = { "17300": ["acme_widget", "acme_widget_pro"], "17200": ["acme_widget_pro"] };
  const name = (id: string) => ({ acme_widget_pro: "Acme Widget Pro" } as Record<string, string>)[id];

  it("names the identified driver on a port it claims", () => {
    expect(openPortLabel(17300, labels, portDrivers, "acme_widget_pro", name)).toBe("Acme Widget Pro");
  });

  it("keeps the shared label for an unidentified device", () => {
    expect(openPortLabel(17300, labels, portDrivers, undefined, name)).toBe("Acme");
  });

  it("keeps the shared label on a port the identified driver does not claim", () => {
    expect(openPortLabel(23, labels, portDrivers, "acme_widget_pro", name)).toBe("Telnet");
  });

  it("says unknown for a port nothing names", () => {
    expect(openPortLabel(4999, labels, portDrivers, undefined, name)).toBe("unknown");
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
