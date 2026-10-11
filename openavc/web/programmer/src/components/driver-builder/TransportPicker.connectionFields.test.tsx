import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// A driver declares the connection fields the Add Device form asks for: an
// IP address and port on the network, the port on a serial line. The Builder
// keeps those out of its Configuration Fields list, so it has to write them
// itself. What shipped broken: a new TCP driver started with none, and a
// device added with it had no IP address or port to fill in.

import { TransportPicker } from "./TransportPicker";
import { connectionFieldsFor, switchConnectionFields } from "./transportPickerHelpers";
import { useDriverBuilderStore } from "../../store/driverBuilderStore";
import type { DriverDefinition } from "../../api/types";

const NETWORK = {
  host: { type: "string", required: true, label: "IP Address" },
  port: { type: "integer", required: true, label: "Port" },
};

describe("a new driver", () => {
  it("starts with the IP address and port its TCP transport needs", () => {
    useDriverBuilderStore.getState().newDriver();
    const draft = useDriverBuilderStore.getState().draft;
    expect(draft.transport).toBe("tcp");
    expect(draft.config_schema).toEqual(NETWORK);
  });
});

describe("switching the transport", () => {
  it("rewrites the connection fields the Builder wrote for the old one", () => {
    expect(switchConnectionFields(connectionFieldsFor("tcp"), "tcp", "serial")).toEqual({
      port: { type: "string", required: true, label: "Serial Port" },
    });
    expect(switchConnectionFields(connectionFieldsFor("serial"), "serial", "udp")).toEqual(NETWORK);
    expect(switchConnectionFields(connectionFieldsFor("tcp"), "tcp", "bridge")).toEqual({});
    // http has a port to fall back on, tcp does not.
    expect(switchConnectionFields(connectionFieldsFor("http"), "http", "tcp")).toEqual(NETWORK);
  });

  it("keeps a connection field the author changed, and every other field", () => {
    const schema = {
      device_id: { type: "integer", label: "Display ID" },
      host: { type: "string", required: true, label: "IP Address / Hostname" },
      port: { type: "integer", required: true, label: "Port" },
    };
    const next = switchConnectionFields(schema, "tcp", "bridge");
    expect(next).toEqual({
      host: schema.host,
      device_id: schema.device_id,
    });
  });

  it("adds the fields a driver from an older Builder never had, address first", () => {
    const schema = { device_id: { type: "integer", label: "Display ID" } };
    const next = switchConnectionFields(schema, "serial", "tcp");
    expect(Object.keys(next)).toEqual(["host", "port", "device_id"]);
  });

  it("happens when the Transport Type changes", () => {
    const onUpdate = vi.fn();
    const draft = {
      id: "acme_widget",
      name: "Acme Widget",
      transport: "tcp",
      delimiter: "\r",
      default_config: {},
      config_schema: connectionFieldsFor("tcp"),
      state_variables: {},
      commands: {},
      responses: [],
    } as unknown as DriverDefinition;
    render(<TransportPicker draft={draft} onUpdate={onUpdate} />);
    fireEvent.change(screen.getByDisplayValue("TCP"), { target: { value: "serial" } });
    expect(onUpdate).toHaveBeenCalledTimes(1);
    expect(onUpdate.mock.calls[0][0].config_schema).toEqual({
      port: { type: "string", required: true, label: "Serial Port" },
    });
  });
});
