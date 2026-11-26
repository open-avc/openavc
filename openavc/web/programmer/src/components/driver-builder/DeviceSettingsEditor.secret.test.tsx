import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// The Driver Builder half of a device setting marked secret (the runtime half
// is core/pending_settings.py: never queued, masked wherever it is shown).
// Marking one drops its default, since a login never has one; a setting named
// password is a secret whatever it declares, so its box stays ticked.

import { DeviceSettingsEditor } from "./DeviceSettingsEditor";
import type { DriverDefinition } from "../../api/types";

function draft(key: string): DriverDefinition {
  return {
    id: "acme_lock",
    name: "Acme Lock",
    manufacturer: "Acme",
    category: "utility",
    version: "1.0.0",
    transport: "tcp",
    state_variables: {},
    device_settings: {
      [key]: { type: "string", label: "Admin PIN", default: "0000", write: { send: "PIN {value}\\r" } },
    },
  };
}

function open(key: string) {
  const onUpdate = vi.fn();
  render(<DeviceSettingsEditor draft={draft(key)} onUpdate={onUpdate} />);
  fireEvent.click(screen.getByTestId(`device-setting-${key}`));
  return { onUpdate, box: screen.getByRole("checkbox", { name: "Secret" }) as HTMLInputElement };
}

describe("a device setting marked secret in the Driver Builder", () => {
  it("is marked, and its default dropped", () => {
    const { onUpdate, box } = open("admin_pin");
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    const written = onUpdate.mock.calls.at(-1)?.[0].device_settings.admin_pin;
    expect(written.secret).toBe(true);
    expect(written.default).toBeUndefined();
  });

  it("is always one when named password", () => {
    const { box } = open("password");
    expect(box.checked).toBe(true);
    expect(box.disabled).toBe(true);
  });
});
