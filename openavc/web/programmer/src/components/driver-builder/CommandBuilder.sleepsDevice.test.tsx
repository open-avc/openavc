import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// The Driver Builder half of a command's sleeps_device (the runtime half is
// core/device_manager.py and core/device_sleep.py). What has to hold: the
// field writes the wake command's id and removes the key when unticked, it
// offers only the commands that run while the device is offline, it says what
// to do when there is none or the picked one is missing a setting, and
// renaming the wake command keeps the reference pointing at it.

import { CommandBuilder } from "./CommandBuilder";
import type { DriverDefinition } from "../../api/types";

function draftWith(commands: DriverDefinition["commands"]): DriverDefinition {
  return {
    id: "acme_display",
    name: "Acme Display",
    manufacturer: "Acme",
    category: "display",
    version: "1.0.0",
    transport: "tcp",
    default_config: { host: "", port: 5000 },
    config_schema: { host: { type: "string", label: "Host" } },
    state_variables: { power: { type: "boolean", label: "Power" } },
    commands,
    responses: [],
  } as unknown as DriverDefinition;
}

const WAKE = {
  label: "Power On",
  available_offline: true,
  restarts_device_for: 30,
  udp: { magic_packet: "mac_address" },
  params: {},
};

function openCommand(id: string) {
  fireEvent.click(screen.getByText(id));
}

describe("Puts the device to sleep", () => {
  it("ticking it names the first command that runs while offline", () => {
    const onUpdate = vi.fn();
    const draft = draftWith({
      power_off: { label: "Power Off", send: "PWR OFF\r", params: {} },
      mute: { label: "Mute", send: "MUTE\r", params: {} },
      power_on: WAKE,
    });
    render(<CommandBuilder draft={draft} onUpdate={onUpdate} />);
    openCommand("power_off");
    fireEvent.click(screen.getByLabelText(/Puts the device to sleep/));
    expect(onUpdate.mock.calls[0][0].commands.power_off.sleeps_device).toBe("power_on");
  });

  it("lists only the other commands that run while the device is offline", () => {
    const draft = draftWith({
      power_off: { label: "Power Off", send: "PWR OFF\r", params: {}, sleeps_device: "power_on" },
      mute: { label: "Mute", send: "MUTE\r", params: {} },
      power_on: WAKE,
    });
    render(<CommandBuilder draft={draft} onUpdate={vi.fn()} />);
    openCommand("power_off");
    const select = screen.getByLabelText("Woken by") as HTMLSelectElement;
    expect(select.value).toBe("power_on");
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["power_on"]);
    expect(Array.from(select.options).map((o) => o.text)).toEqual(["Power On (power_on)"]);
  });

  it("unticking it removes the key", () => {
    const onUpdate = vi.fn();
    const draft = draftWith({
      power_off: { label: "Power Off", send: "PWR OFF\r", params: {}, sleeps_device: "power_on" },
      power_on: WAKE,
    });
    render(<CommandBuilder draft={draft} onUpdate={onUpdate} />);
    openCommand("power_off");
    fireEvent.click(screen.getByLabelText(/Puts the device to sleep/));
    const written = onUpdate.mock.calls[0][0].commands.power_off;
    expect("sleeps_device" in written).toBe(false);
  });

  it("says what the wake command needs when no command can wake it", () => {
    const onUpdate = vi.fn();
    const draft = draftWith({
      power_off: { label: "Power Off", send: "PWR OFF\r", params: {} },
      power_on: { label: "Power On", send: "PWR ON\r", params: {} },
    });
    render(<CommandBuilder draft={draft} onUpdate={onUpdate} />);
    openCommand("power_off");
    fireEvent.click(screen.getByLabelText(/Puts the device to sleep/));
    // Nothing written: an empty sleeps_device is not a value.
    expect(onUpdate).not.toHaveBeenCalled();
    expect(screen.getByText(/No command can wake it yet/)).toBeTruthy();
  });

  it("says what a picked wake command is missing", () => {
    const draft = draftWith({
      power_off: { label: "Power Off", send: "PWR OFF\r", params: {}, sleeps_device: "power_on" },
      power_on: { ...WAKE, restarts_device_for: undefined },
    });
    render(<CommandBuilder draft={draft} onUpdate={vi.fn()} />);
    openCommand("power_off");
    expect(
      screen.getByText("Power On also needs \"Restarts the device for\" set."),
    ).toBeTruthy();
  });

  it("keeps showing a wake command that no longer runs offline", () => {
    const draft = draftWith({
      power_off: { label: "Power Off", send: "PWR OFF\r", params: {}, sleeps_device: "power_on" },
      power_on: { label: "Power On", send: "PWR ON\r", params: {}, restarts_device_for: 30 },
    });
    render(<CommandBuilder draft={draft} onUpdate={vi.fn()} />);
    openCommand("power_off");
    const select = screen.getByLabelText("Woken by") as HTMLSelectElement;
    expect(select.value).toBe("power_on");
    expect(
      screen.getByText(
        "Power On also needs \"Runs while the device is offline\" ticked.",
      ),
    ).toBeTruthy();
  });

  it("does not offer the command as its own wake command", () => {
    const draft = draftWith({
      power_on: { ...WAKE, sleeps_device: undefined },
    });
    render(<CommandBuilder draft={draft} onUpdate={vi.fn()} />);
    openCommand("power_on");
    fireEvent.click(screen.getByLabelText(/Puts the device to sleep/));
    const select = screen.getByLabelText("Woken by") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual([""]);
  });

  it("renaming the wake command keeps the sleeping command pointing at it", () => {
    const onUpdate = vi.fn();
    const draft = draftWith({
      power_off: { label: "Power Off", send: "PWR OFF\r", params: {}, sleeps_device: "power_on" },
      power_on: WAKE,
    });
    render(<CommandBuilder draft={draft} onUpdate={onUpdate} />);
    openCommand("power_on");
    const idInput = screen.getByDisplayValue("power_on");
    fireEvent.change(idInput, { target: { value: "wake" } });
    fireEvent.blur(idInput);
    const commands = onUpdate.mock.calls.at(-1)![0].commands;
    expect(Object.keys(commands)).toEqual(["power_off", "wake"]);
    expect(commands.power_off.sleeps_device).toBe("wake");
  });
});
