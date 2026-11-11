import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// A command renamed on the Commands tab carries every reference to it in the
// rest of the draft: the runtime sends a poll or start-up line that names a
// command as that command and anything else as raw text, so a line left on
// the old id would go to the device as the old id's text.

import { CommandBuilder } from "./CommandBuilder";
import { draftCommandRenamed } from "./commandEdits";
import type { DriverDefinition } from "../../api/types";

function draft(extra: Partial<DriverDefinition>): DriverDefinition {
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
      set_input: { label: "Set Input", send: "IN{input}\r", params: {} },
    },
    responses: [],
    polling: { queries: [] },
    ...extra,
  } as unknown as DriverDefinition;
}

function renameThroughTheBuilder(d: DriverDefinition, from: string, to: string) {
  const onUpdate = vi.fn();
  render(<CommandBuilder draft={d} onUpdate={onUpdate} />);
  fireEvent.click(screen.getByText(from));
  const idInput = screen.getByDisplayValue(from);
  fireEvent.change(idInput, { target: { value: to } });
  fireEvent.blur(idInput);
  const calls = onUpdate.mock.calls;
  return calls[calls.length - 1][0] as Partial<DriverDefinition>;
}

describe("Renaming a command carries every reference to it", () => {
  it("a poll line, in either form, follows it", () => {
    const update = renameThroughTheBuilder(
      draft({
        polling: {
          queries: ["get_power", { send: "get_power", when: "poll_power" }, "STATUS?\r"],
        },
      }),
      "get_power",
      "power_query",
    );
    expect(update.polling?.queries).toEqual([
      "power_query",
      { send: "power_query", when: "poll_power" },
      "STATUS?\r",
    ]);
  });

  it("a start-up line follows it", () => {
    const update = renameThroughTheBuilder(
      draft({ on_connect: ["get_power", "HELLO\r"] }),
      "get_power",
      "power_query",
    );
    expect(update.on_connect).toEqual(["power_query", "HELLO\r"]);
  });

  it("a response rule's follow-up query follows it", () => {
    const update = renameThroughTheBuilder(
      draft({
        responses: [
          { match: "^CHANGED$", then_query: ["get_power"] },
          { match: "^OTHER$", set: { power: "true" } },
        ],
      } as unknown as Partial<DriverDefinition>),
      "get_power",
      "power_query",
    );
    expect(update.responses).toEqual([
      { match: "^CHANGED$", then_query: ["power_query"] },
      { match: "^OTHER$", set: { power: "true" } },
    ]);
  });

  it("an action's command, and an action whose id names it, follow it", () => {
    const update = renameThroughTheBuilder(
      draft({
        actions: [
          { id: "refresh", kind: "command", command: "get_power" },
          { id: "get_power", label: "Check power" },
          { id: "manual", kind: "link", url: "http://{host}/" },
        ],
        quick_actions: ["get_power", "set_input"],
      } as unknown as Partial<DriverDefinition>),
      "get_power",
      "power_query",
    );
    expect(update.actions).toEqual([
      { id: "refresh", kind: "command", command: "power_query" },
      { id: "get_power", label: "Check power", command: "power_query" },
      { id: "manual", kind: "link", url: "http://{host}/" },
    ]);
    expect(update.quick_actions).toEqual(["power_query", "set_input"]);
  });

  it("the routing command, the block's and a plane's, follows it", () => {
    const update = renameThroughTheBuilder(
      draft({
        routing: {
          command: "set_input",
          planes: [{ route_property: "input" }, { route_property: "audio", command: "set_input" }],
        },
      } as unknown as Partial<DriverDefinition>),
      "set_input",
      "route",
    );
    expect(update.routing).toEqual({
      command: "route",
      planes: [{ route_property: "input" }, { route_property: "audio", command: "route" }],
    });
  });

  it("a push registration follows it, in every form it takes", () => {
    const listed = renameThroughTheBuilder(
      draft({
        push: {
          type: "tcp_listener",
          register: ["get_power", { command: "get_power", when: "subscribe" }, "set_input"],
          unregister: "get_power",
        },
      } as unknown as Partial<DriverDefinition>),
      "get_power",
      "power_query",
    );
    expect(listed.push).toEqual({
      type: "tcp_listener",
      register: ["power_query", { command: "power_query", when: "subscribe" }, "set_input"],
      unregister: "power_query",
    });
  });

  it("a section that names it nowhere is not written", () => {
    const d = draft({ on_connect: ["HELLO\r"], push: { type: "sse", register: "set_input" } } as unknown as Partial<DriverDefinition>);
    const result = draftCommandRenamed(d, "get_power", "power_query");
    expect(Object.keys(result.update ?? {})).toEqual(["commands"]);
  });

  it("a single push registration follows it", () => {
    const d = draft({ push: { type: "sse", register: "get_power" } } as unknown as Partial<DriverDefinition>);
    const result = draftCommandRenamed(d, "get_power", "power_query");
    expect(result.update?.push).toEqual({ type: "sse", register: "power_query" });
  });

  it("a refused rename writes nothing", () => {
    const d = draft({});
    expect(draftCommandRenamed(d, "get_power", "set_input")).toEqual({
      ok: false,
      reason: '"set_input" already exists.',
    });
    expect(draftCommandRenamed(d, "get_power", "get_power")).toEqual({ ok: true });
  });
});
