import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// The Driver Builder half of a command's udp: block (the runtime half is
// configurable.py). What has to hold: the section is reachable from any
// transport, ticking it swaps the send string for the datagram fields and
// drops the fields the runtime would ignore, the two forms are exclusive, and
// the MAC picker offers every field a MAC can live in with state first, which
// is the order the runtime reads them in.

import { CommandBuilder, macFieldOptions } from "./CommandBuilder";
import type { DriverDefinition } from "../../api/types";

function draftWith(commands: DriverDefinition["commands"]): DriverDefinition {
  return {
    id: "acme_widget",
    name: "Acme Widget",
    manufacturer: "Acme",
    category: "display",
    version: "1.0.0",
    transport: "tcp",
    default_config: { host: "", port: 5000, mac_address: "", udp_port: 5100 },
    config_schema: {
      host: { type: "string", label: "Host" },
      mac_address: { type: "string", label: "MAC Address" },
      udp_port: { type: "integer", label: "UDP Port" },
    },
    state_variables: {
      power: { type: "boolean", label: "Power" },
      mac_address: { type: "string", label: "MAC Address" },
    },
    commands,
    responses: [],
  } as unknown as DriverDefinition;
}

describe("macFieldOptions", () => {
  it("lists state variables first, then config fields, each once", () => {
    const options = macFieldOptions(draftWith({}));
    expect(options.map((o) => o.value)).toEqual([
      "power",
      "mac_address",
      "host",
      "udp_port",
      "port",
    ]);
    expect(options[1].label).toBe("mac_address (state variable)");
    expect(options[3].label).toBe("udp_port (config field)");
  });
});

describe("Send over UDP", () => {
  it("ticking it swaps the send string for the datagram fields and drops the send", () => {
    const onUpdate = vi.fn();
    const draft = draftWith({
      wake: { label: "Wake", send: "PWR ON\r", params: {} },
    });
    render(<CommandBuilder draft={draft} onUpdate={onUpdate} />);
    fireEvent.click(screen.getByText("wake"));
    expect(screen.getByText("Command String")).toBeTruthy();

    fireEvent.click(
      screen.getByLabelText(/Send over UDP instead of the device connection/),
    );
    expect(onUpdate).toHaveBeenCalledTimes(1);
    const written = onUpdate.mock.calls[0][0].commands.wake;
    expect(written.udp).toEqual({ payload: "" });
    expect("send" in written).toBe(false);
    expect(written.params).toEqual({});
  });

  it("shows the datagram fields for a udp command on a TCP driver", () => {
    const draft = draftWith({
      say: {
        label: "Say",
        udp: { port: "{udp_port}", payload: "hello {name}\\r" },
        params: { name: { type: "string", required: true } },
      },
    });
    render(<CommandBuilder draft={draft} onUpdate={vi.fn()} />);
    fireEvent.click(screen.getByText("say"));
    expect(screen.queryByText("Command String")).toBeNull();
    expect((screen.getByLabelText("UDP message") as HTMLInputElement).value).toBe(
      "hello {name}\\r",
    );
    expect((screen.getByLabelText("UDP port") as HTMLInputElement).value).toBe(
      "{udp_port}",
    );
    expect(screen.getByLabelText(/Broadcast to the whole network/)).toBeTruthy();
  });

  it("switching to a magic packet drops the payload and picks a MAC field", () => {
    const onUpdate = vi.fn();
    const draft = draftWith({
      wake: {
        label: "Wake",
        udp: { port: 9, payload: "X", broadcast: true },
        params: {},
      },
    });
    render(<CommandBuilder draft={draft} onUpdate={onUpdate} />);
    fireEvent.click(screen.getByText("wake"));
    fireEvent.click(screen.getByLabelText("A Wake-on-LAN magic packet"));
    const written = onUpdate.mock.calls[0][0].commands.wake.udp;
    expect(written).toEqual({ port: 9, magic_packet: "power" });
  });

  it("a magic-packet command offers the MAC picker and no broadcast box", () => {
    const onUpdate = vi.fn();
    const draft = draftWith({
      power_on: {
        label: "Power On",
        available_offline: true,
        udp: { magic_packet: "mac_address" },
        params: {},
      },
    });
    render(<CommandBuilder draft={draft} onUpdate={onUpdate} />);
    fireEvent.click(screen.getByText("power_on"));
    const picker = screen.getByLabelText("MAC address from") as HTMLSelectElement;
    expect(picker.value).toBe("mac_address");
    expect(screen.queryByLabelText(/Broadcast to the whole network/)).toBeNull();
    expect(screen.queryByLabelText("UDP message")).toBeNull();

    fireEvent.change(picker, { target: { value: "host" } });
    expect(onUpdate.mock.calls[0][0].commands.power_on.udp).toEqual({
      magic_packet: "host",
    });
  });

  it("a port typed as digits is written as a number, a placeholder as text", () => {
    const onUpdate = vi.fn();
    const draft = draftWith({
      say: { label: "Say", udp: { payload: "X" }, params: {} },
    });
    render(<CommandBuilder draft={draft} onUpdate={onUpdate} />);
    fireEvent.click(screen.getByText("say"));
    const port = screen.getByLabelText("UDP port");
    fireEvent.change(port, { target: { value: "5100" } });
    expect(onUpdate.mock.calls[0][0].commands.say.udp.port).toBe(5100);
    fireEvent.change(port, { target: { value: "{udp_port}" } });
    expect(onUpdate.mock.calls[1][0].commands.say.udp.port).toBe("{udp_port}");
  });
});
