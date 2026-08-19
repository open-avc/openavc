import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

// What shipped broken: the panel read config_schema, commands and
// state_variables off the driver and nothing else, so a driver whose real
// control surface lives on a child roster looked like a box with eight
// metadata strings. The API had been sending child_entity_types and
// device_settings the whole time. Half the shipped catalog declares one or
// both, so this pins that the panel reads what the driver list ships.

import { DriverDetailPanel } from "./InstalledDriversView";
import type { DriverInfo } from "../../api/types";

const DRIVER: DriverInfo = {
  id: "acme_amp",
  name: "Acme Amp",
  manufacturer: "Acme",
  category: "audio",
  commands: {
    set_level: { label: "Set Output Level" },
  },
  config_schema: {
    host: { type: "string", label: "Host", required: true },
  },
  state_variables: {
    firmware_version: { type: "string", label: "Firmware Version" },
  },
  device_settings: {
    device_name: { type: "string", label: "Device Name", help: "" },
  },
  child_entity_types: {
    channel: {
      label: "Channel",
      label_plural: "Channels",
      id_format: { type: "integer", min: 1, max: 8 },
      state_variables: {
        fader: { type: "number", label: "Output Level (dB)", unit: "dB", control: true },
        mute: { type: "boolean", label: "Mute", control: true },
      },
    },
  },
};

function renderPanel(driver: DriverInfo) {
  return render(
    <DriverDetailPanel
      driver={driver}
      installed={null}
      isPython={false}
      isBuiltin={false}
      canUninstall={false}
      devicesUsingDriver={[]}
      confirmUninstall={false}
      uninstalling={false}
      uninstallError={null}
      canOpenInBuilder={false}
      onRequestUninstall={() => {}}
      onConfirmUninstall={() => {}}
      onCancelUninstall={() => {}}
      onDismissUninstallError={() => {}}
    />,
  );
}

describe("Driver detail: the surface a driver actually declares", () => {
  it("lists the values that live on a child roster, not just the device", () => {
    renderPanel(DRIVER);

    // The device-level half still renders.
    expect(screen.getByText("Firmware Version")).toBeTruthy();

    // The half that was missing: per-channel controls, under the roster's name.
    expect(screen.getByText("Channels")).toBeTruthy();
    expect(screen.getByText("Output Level (dB)")).toBeTruthy();
    expect(screen.getByText("Mute")).toBeTruthy();
  });

  it("lists writable device settings", () => {
    renderPanel(DRIVER);
    expect(screen.getByText("Device Settings")).toBeTruthy();
    expect(screen.getByText("Device Name")).toBeTruthy();
  });

  it("says a dynamic roster is read from the hardware rather than listing nothing", () => {
    // A dynamic type declares no controls up front — they are discovered at
    // connect. Listing its two platform-managed keys would read as "this
    // driver exposes Online and Label", which is worse than saying nothing.
    renderPanel({
      ...DRIVER,
      child_entity_types: {
        component: {
          label: "Component",
          label_plural: "Components",
          id_format: { type: "string" },
          dynamic: true,
          state_variables: {
            online: { type: "boolean", label: "Online" },
          },
        },
      },
    });

    expect(screen.getByText("Components")).toBeTruthy();
    expect(screen.getByText("Read from the device when it connects.")).toBeTruthy();
    expect(screen.queryByText("Online")).toBeNull();
  });

  it("omits the new sections entirely for a driver that declares neither", () => {
    renderPanel({
      ...DRIVER,
      device_settings: undefined,
      child_entity_types: undefined,
    });

    expect(screen.queryByText("Device Settings")).toBeNull();
    expect(screen.queryByText("Per-Channel Values")).toBeNull();
    expect(screen.getByText("Firmware Version")).toBeTruthy();
  });
});
