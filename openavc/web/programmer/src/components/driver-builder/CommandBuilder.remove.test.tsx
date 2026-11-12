import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

// Removing a command on the Commands tab never leaves an entry naming it
// behind: the runtime sends a poll or start-up line that names no declared
// command as the transport's raw line, so a line left on a removed id would
// go to the device as that text. A command nothing names goes at once; one
// still named asks first, saying each entry that goes with it.

import { CommandBuilder } from "./CommandBuilder";
import { draftCommandRemoved } from "./commandEdits";
import type { DriverDefinition } from "../../api/types";

function draft(extra: Partial<DriverDefinition> = {}): DriverDefinition {
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
    commands: {
      get_power: { label: "Get Power", send: "PWR?\r", params: {} },
      power_off: { label: "Power Off", send: "PWR0\r", params: {} },
    },
    responses: [],
    polling: { queries: [] },
    ...extra,
  } as unknown as DriverDefinition;
}

describe("What a removal takes with it, one section at a time", () => {
  it("nothing when nothing else names the command", () => {
    const { uses, update } = draftCommandRemoved(draft(), "get_power");
    expect(uses).toEqual([]);
    expect(update).toEqual({ commands: { power_off: draft().commands.power_off } });
  });

  it("a one-click button and an action that sends it", () => {
    const { uses, update } = draftCommandRemoved(
      draft({
        quick_actions: ["get_power", "power_off"],
        actions: [
          { id: "refresh", label: "Refresh", command: "get_power" },
          { id: "get_power", label: "Check Power" },
          { id: "off", label: "Off", command: "power_off" },
        ],
      } as Partial<DriverDefinition>),
      "get_power",
    );
    expect(uses).toEqual([
      "Actions: its one-click button",
      'Actions: "Refresh"',
      'Actions: "Check Power"',
    ]);
    expect(update.quick_actions).toEqual(["power_off"]);
    expect(update.actions).toEqual([{ id: "off", label: "Off", command: "power_off" }]);
  });

  it("a start-up line and a poll query, in either form", () => {
    const { uses, update } = draftCommandRemoved(
      draft({
        on_connect: ["get_power", "HELLO\r"],
        polling: { interval: 5, queries: [{ send: "get_power", when: "poll_power" }, "STATUS?\r"] },
      } as Partial<DriverDefinition>),
      "get_power",
    );
    expect(uses).toEqual(["Connect Sequence: a line that sends it", "Polling: a query that sends it"]);
    expect(update.on_connect).toEqual(["HELLO\r"]);
    expect(update.polling).toEqual({ interval: 5, queries: ["STATUS?\r"] });
  });

  it("a response rule's follow-up query, the rule kept", () => {
    const { uses, update } = draftCommandRemoved(
      draft({
        responses: [
          { match: "OK", set: {} },
          { match: "PWR(\\d)", set: { power: "$1" }, then_query: ["get_power", "INPUT?\r"] },
        ],
      } as Partial<DriverDefinition>),
      "get_power",
    );
    expect(uses).toEqual(["Responses: Response Rule 2, Then Query"]);
    expect(update.responses?.[1]).toEqual({ match: "PWR(\\d)", set: { power: "$1" }, then_query: ["INPUT?\r"] });
  });

  it("the routing command, the block's and a plane's", () => {
    const { uses, update } = draftCommandRemoved(
      draft({
        routing: {
          command: "get_power",
          planes: [
            { label: "Video", route_property: "video_source", command: "get_power" },
            { route_property: "audio_source", command: "power_off" },
          ],
        },
      } as Partial<DriverDefinition>),
      "get_power",
    );
    expect(uses).toEqual(["Routing: its command", 'Routing: the "Video" plane\'s command']);
    expect(update.routing).toEqual({
      planes: [
        { label: "Video", route_property: "video_source" },
        { route_property: "audio_source", command: "power_off" },
      ],
    });
  });

  it("a push register command, in any form, and the unregister command", () => {
    const { uses, update } = draftCommandRemoved(
      draft({
        push: {
          type: "tcp_listener",
          port: 0,
          register: ["get_power", { command: "get_power", when: "meters" }, "power_off"],
          unregister: "get_power",
        },
      } as Partial<DriverDefinition>),
      "get_power",
    );
    expect(uses).toEqual([
      "Push Notifications: a register command",
      "Push Notifications: a register command",
      "Push Notifications: Unregister Command",
    ]);
    expect(update.push).toEqual({ type: "tcp_listener", port: 0, register: ["power_off"] });
  });

  it("a single register command takes the field with it", () => {
    const { update } = draftCommandRemoved(
      draft({ push: { type: "tcp_listener", port: 0, register: "get_power" } } as Partial<DriverDefinition>),
      "get_power",
    );
    expect(update.push).toEqual({ type: "tcp_listener", port: 0 });
  });

  it("another command's wake command and read-back", () => {
    const d = draft();
    d.commands.power_off = { ...d.commands.power_off, sleeps_device: "get_power", read_back: "get_power" };
    d.commands.set_input = { label: "Set Input", send: "IN1\r", params: {}, read_back: ["get_power", "INPUT?\r"] };
    const { uses, update } = draftCommandRemoved(d, "get_power");
    expect(uses).toEqual([
      'Commands: "power_off", Woken by',
      'Commands: "power_off", Read back with',
      'Commands: "set_input", Read back with',
    ]);
    expect(update.commands).toEqual({
      power_off: { label: "Power Off", send: "PWR0\r", params: {} },
      set_input: { label: "Set Input", send: "IN1\r", params: {}, read_back: ["INPUT?\r"] },
    });
  });
});

describe("The remove button on the Commands tab", () => {
  function removeThroughTheBuilder(d: DriverDefinition, name: string) {
    const onUpdate = vi.fn();
    render(<CommandBuilder draft={d} onUpdate={onUpdate} />);
    const row = Object.keys(d.commands).indexOf(name);
    fireEvent.click(screen.getAllByRole("button", { name: "Remove command" })[row]);
    return onUpdate;
  }

  it("removes a command nothing names at once", () => {
    const onUpdate = removeThroughTheBuilder(draft(), "get_power");
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(onUpdate).toHaveBeenCalledWith({ commands: { power_off: draft().commands.power_off } });
  });

  it("asks first, naming each entry that still uses it", () => {
    const onUpdate = removeThroughTheBuilder(
      draft({ polling: { queries: ["get_power"] } } as Partial<DriverDefinition>),
      "get_power",
    );
    const dialog = screen.getByRole("alertdialog");
    expect(within(dialog).getByText('Remove "get_power"?')).toBeTruthy();
    expect(within(dialog).getByText("Polling: a query that sends it")).toBeTruthy();
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it("Cancel leaves the driver as it was", () => {
    const onUpdate = removeThroughTheBuilder(
      draft({ polling: { queries: ["get_power"] } } as Partial<DriverDefinition>),
      "get_power",
    );
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it("Remove takes the command and every entry naming it", () => {
    const onUpdate = removeThroughTheBuilder(
      draft({ polling: { queries: ["get_power", "STATUS?\r"] } } as Partial<DriverDefinition>),
      "get_power",
    );
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove" }));
    expect(onUpdate).toHaveBeenCalledWith({
      polling: { queries: ["STATUS?\r"] },
      commands: { power_off: draft().commands.power_off },
    });
  });
});
