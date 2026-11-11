import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";

// What one command's card in the Driver Builder writes, read through the
// editor itself. A stateful host feeds every write back into the draft the
// way the Driver Editor does, so a value held only by the field (a box ticked
// with nothing to pick yet, a row being typed into) and the focus of the box
// being typed in are seen across the re-render a write causes.

import { CommandBuilder } from "./CommandBuilder";
import type { DriverCommandDef, DriverDefinition } from "../../api/types";

function draftWith(
  commands: Record<string, Partial<DriverCommandDef>>,
  extra: Partial<DriverDefinition> = {},
): DriverDefinition {
  return {
    id: "acme_widget",
    name: "Acme Widget",
    manufacturer: "Acme",
    category: "utility",
    version: "1.0.0",
    transport: "tcp",
    default_config: { host: "", port: 4000 },
    config_schema: {},
    state_variables: {},
    commands,
    responses: [],
    ...extra,
  } as unknown as DriverDefinition;
}

/** The editor over a draft it owns, reporting every draft it is given. */
function Host({ initial, seen }: { initial: DriverDefinition; seen: (d: DriverDefinition) => void }) {
  const [draft, setDraft] = useState(initial);
  return (
    <CommandBuilder
      draft={draft}
      onUpdate={(partial) =>
        setDraft((d) => {
          const next = { ...d, ...partial };
          seen(next);
          return next;
        })
      }
    />
  );
}

function host(initial: DriverDefinition) {
  const seen = vi.fn();
  const view = render(<Host initial={initial} seen={seen} />);
  const commands = (): Record<string, DriverCommandDef> => {
    const calls = seen.mock.calls;
    return calls.length ? calls[calls.length - 1][0].commands : initial.commands;
  };
  return { ...view, seen, commands };
}

const open = (id: string) => fireEvent.click(screen.getByText(id));

/** The control under a field's caption (captions here are not tied to their box). */
function fieldUnder(caption: string): HTMLElement {
  const el = screen.getByText(caption, { selector: "label" }).parentElement!;
  return el.querySelector("input, select, textarea") as HTMLElement;
}

function type(el: HTMLElement, value: string) {
  (el as HTMLInputElement).focus();
  fireEvent.change(el, { target: { value } });
}

describe("the command list", () => {
  it("says there are none, and Add Command seeds a byte-stream command and opens it", () => {
    const h = host(draftWith({}));
    expect(screen.getByText(/No commands defined yet/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Add Command" }));
    expect(h.commands()).toEqual({ command_1: { label: "New Command", send: "", params: {} } });
    expect((fieldUnder("Command ID") as HTMLInputElement).value).toBe("command_1");
    expect(screen.queryByText(/No commands defined yet/)).toBeNull();
  });

  it("seeds an HTTP command with GET / and an OSC command with an address", () => {
    const http = host(draftWith({}, { transport: "http" }));
    fireEvent.click(screen.getByRole("button", { name: "Add Command" }));
    expect(http.commands().command_1).toEqual({
      label: "New Command", send: "", method: "GET", path: "/", params: {},
    });
    http.unmount();
    const osc = host(draftWith({}, { transport: "osc" }));
    fireEvent.click(screen.getByRole("button", { name: "Add Command" }));
    expect(osc.commands().command_1).toEqual({
      label: "New Command", send: "", address: "/", args: [], params: {},
    });
  });

  it("numbers a new command past the ones taken", () => {
    const h = host(draftWith({ command_2: { label: "A", send: "" } }));
    fireEvent.click(screen.getByRole("button", { name: "Add Command" }));
    expect(Object.keys(h.commands())).toEqual(["command_2", "command_3"]);
    fireEvent.click(screen.getByRole("button", { name: "Add Command" }));
    expect(Object.keys(h.commands())).toEqual(["command_2", "command_3", "command_4"]);
  });

  it("opens and closes a card from its header, which shows the label", () => {
    host(draftWith({ power_on: { label: "Power On", send: "PWR1\r" } }));
    expect(screen.getByText("Power On")).toBeTruthy();
    expect(screen.queryByText("Command ID")).toBeNull();
    open("power_on");
    expect(screen.getByText("Command ID")).toBeTruthy();
    open("power_on");
    expect(screen.queryByText("Command ID")).toBeNull();
  });

  it("removes a command from its header without opening it, and an open card stays open", () => {
    const h = host(draftWith({
      a: { label: "A", send: "A\r" },
      b: { label: "B", send: "B\r" },
    }));
    open("a");
    const header = screen.getByText("b").closest("button")!;
    fireEvent.click(within(header).getByRole("button"));
    expect(Object.keys(h.commands())).toEqual(["a"]);
    expect((fieldUnder("Command ID") as HTMLInputElement).value).toBe("a");
  });

  it("closes nothing when the open command is removed, and the list is empty again", () => {
    const h = host(draftWith({ a: { label: "A", send: "A\r" } }));
    open("a");
    fireEvent.click(within(screen.getByText("a").closest("button")!).getByRole("button"));
    expect(h.commands()).toEqual({});
    expect(screen.queryByText("Command ID")).toBeNull();
  });
});

describe("a removed command coming back", () => {
  it("comes back closed (an undo, or a parent that kept it)", () => {
    const d = draftWith({ a: { label: "A", send: "A\r" } });
    const onUpdate = vi.fn();
    render(<CommandBuilder draft={d} onUpdate={onUpdate} />);
    open("a");
    fireEvent.click(within(screen.getByText("a").closest("button")!).getByRole("button"));
    expect(onUpdate.mock.calls[0][0].commands).toEqual({});
    expect(screen.queryByText("Command ID")).toBeNull();
  });
});

describe("renaming a command", () => {
  const commands = {
    first: { label: "First", send: "F\r" },
    wake: { label: "Wake", send: "W\r", available_offline: true },
    sleep: { label: "Sleep", send: "S\r", sleeps_device: "wake", read_back: "wake" },
    both: { label: "Both", send: "B\r", read_back: ["first", "wake", "QRY\r"] },
    other: { label: "Other", send: "O\r", read_back: ["first"] },
  };

  function rename(from: string, to: string) {
    open(from);
    const box = fieldUnder("Command ID");
    type(box, to);
    fireEvent.blur(box);
  }

  it("keeps its place, stays open, and carries every command that names it", () => {
    const h = host(draftWith(commands));
    rename("wake", "wake_up");
    const next = h.commands();
    expect(Object.keys(next)).toEqual(["first", "wake_up", "sleep", "both", "other"]);
    expect(next.wake_up).toEqual(commands.wake);
    expect(next.sleep.sleeps_device).toBe("wake_up");
    expect(next.sleep.read_back).toBe("wake_up");
    expect(next.both.read_back).toEqual(["first", "wake_up", "QRY\r"]);
    expect(next.other).toBe(h.seen.mock.calls[0][0].commands.other);
    expect((fieldUnder("Command ID") as HTMLInputElement).value).toBe("wake_up");
  });

  it("refuses an empty id and one already taken, and writes nothing", () => {
    const h = host(draftWith(commands));
    rename("first", "");
    expect(screen.getByText("ID can't be empty.")).toBeTruthy();
    type(fieldUnder("Command ID"), "wake");
    fireEvent.blur(fieldUnder("Command ID"));
    expect(screen.getByText('"wake" already exists.')).toBeTruthy();
    expect(h.seen).not.toHaveBeenCalled();
  });

  it("an id typed back to itself writes nothing", () => {
    const h = host(draftWith(commands));
    rename("first", "first");
    expect(h.seen).not.toHaveBeenCalled();
  });
});

describe("the fields every command has", () => {
  it("writes the label and the help text, keeping focus in the box", () => {
    const h = host(draftWith({ a: { label: "A", send: "" } }));
    open("a");
    const label = fieldUnder("Display Label");
    type(label, "Power On");
    expect(h.commands().a.label).toBe("Power On");
    expect(document.activeElement).toBe(fieldUnder("Display Label"));
    const help = fieldUnder("Help Text");
    type(help, "Turns it on");
    expect((h.commands().a as { help?: string }).help).toBe("Turns it on");
    expect(document.activeElement).toBe(fieldUnder("Help Text"));
  });

  it("ticks and unticks Runs while the device is offline, removing the key", () => {
    const h = host(draftWith({ a: { label: "A", send: "" } }));
    open("a");
    fireEvent.click(screen.getByLabelText(/Runs while the device is offline/));
    expect(h.commands().a.available_offline).toBe(true);
    fireEvent.click(screen.getByLabelText(/Runs while the device is offline/));
    expect("available_offline" in h.commands().a).toBe(false);
  });

  it("reads Restarts the device for as whole seconds up to 600, and removes it when not positive", () => {
    const h = host(draftWith({ a: { label: "A", send: "" } }));
    open("a");
    const box = screen.getByLabelText(/Restarts the device for/);
    type(box, "30");
    expect(h.commands().a.restarts_device_for).toBe(30);
    expect(document.activeElement).toBe(screen.getByLabelText(/Restarts the device for/));
    type(screen.getByLabelText(/Restarts the device for/), "2.6");
    expect(h.commands().a.restarts_device_for).toBe(3);
    type(screen.getByLabelText(/Restarts the device for/), "900");
    expect(h.commands().a.restarts_device_for).toBe(600);
    type(screen.getByLabelText(/Restarts the device for/), "1e3");
    expect(h.commands().a.restarts_device_for).toBe(600);
    type(screen.getByLabelText(/Restarts the device for/), "0");
    expect("restarts_device_for" in h.commands().a).toBe(false);
    type(screen.getByLabelText(/Restarts the device for/), "45");
    type(screen.getByLabelText(/Restarts the device for/), "-5");
    expect("restarts_device_for" in h.commands().a).toBe(false);
    type(screen.getByLabelText(/Restarts the device for/), "45");
    type(screen.getByLabelText(/Restarts the device for/), "");
    expect("restarts_device_for" in h.commands().a).toBe(false);
  });

  it("writes an Ask before sending message, and a cleared message is a plain true", () => {
    const h = host(draftWith({ a: { label: "A", send: "", confirm: true } }));
    open("a");
    const box = fieldUnder("Confirmation Message (optional)");
    type(box, "Erase everything?");
    expect(h.commands().a.confirm).toBe("Erase everything?");
    expect(document.activeElement).toBe(fieldUnder("Confirmation Message (optional)"));
    type(fieldUnder("Confirmation Message (optional)"), "");
    expect(h.commands().a.confirm).toBe(true);
    fireEvent.click(screen.getByLabelText(/Ask before sending/));
    expect("confirm" in h.commands().a).toBe(false);
    expect(screen.queryByText("Confirmation Message (optional)")).toBeNull();
  });
});

describe("how a byte-stream command is sent", () => {
  it("writes the command string, keeping focus", () => {
    const h = host(draftWith({ a: { label: "A", send: "" } }));
    open("a");
    type(fieldUnder("Command String"), "PWR {on}\\r");
    expect(h.commands().a.send).toBe("PWR {on}\\r");
    expect(document.activeElement).toBe(fieldUnder("Command String"));
  });

  it("offers Send raw only when the driver frames its commands", () => {
    host(draftWith({ a: { label: "A", send: "" } }));
    open("a");
    expect(screen.queryByLabelText(/Send raw/)).toBeNull();
  });

  it("Send raw writes raw and removes it when unticked", () => {
    const h = host(draftWith({ a: { label: "A", send: "" } }, { command_suffix: "\r" } as Partial<DriverDefinition>));
    open("a");
    fireEvent.click(screen.getByLabelText(/Send raw/));
    expect(h.commands().a.raw).toBe(true);
    fireEvent.click(screen.getByLabelText(/Send raw/));
    expect("raw" in h.commands().a).toBe(false);
  });

  it("a prefix alone offers it too", () => {
    host(draftWith({ a: { label: "A", send: "" } }, { command_prefix: "#" } as Partial<DriverDefinition>));
    open("a");
    expect(screen.getByLabelText(/Send raw/)).toBeTruthy();
  });
});

describe("how an OSC command is sent", () => {
  it("writes the address and its arguments", () => {
    const h = host(draftWith({ a: { label: "A", send: "", address: "/", args: [] } }, { transport: "osc" }));
    open("a");
    type(fieldUnder("OSC Address"), "/ch/{ch}/mix");
    expect(h.commands().a.address).toBe("/ch/{ch}/mix");
    expect(document.activeElement).toBe(fieldUnder("OSC Address"));
    fireEvent.click(screen.getByRole("button", { name: "+ Add Argument" }));
    expect(h.commands().a.args).toHaveLength(1);
    expect(screen.queryByText("Command String")).toBeNull();
  });
});

describe("how an HTTP command is sent", () => {
  function httpCard(cmd: Partial<DriverCommandDef>) {
    const h = host(draftWith({ a: { label: "A", send: "", ...cmd } }, { transport: "http" }));
    open("a");
    return h;
  }

  it("shows the method in capitals and writes method, path and body", () => {
    const h = httpCard({ method: "post", path: "/" });
    expect((fieldUnder("Method") as HTMLSelectElement).value).toBe("POST");
    fireEvent.change(fieldUnder("Method"), { target: { value: "PUT" } });
    expect(h.commands().a.method).toBe("PUT");
    type(fieldUnder("Path"), "/api/{id}");
    expect(h.commands().a.path).toBe("/api/{id}");
    expect(document.activeElement).toBe(fieldUnder("Path"));
    type(fieldUnder("Body"), '{"on": true}');
    expect(h.commands().a.body).toBe('{"on": true}');
    expect(document.activeElement).toBe(fieldUnder("Body"));
  });

  it("a command with no method shows GET", () => {
    httpCard({ path: "/" });
    expect((fieldUnder("Method") as HTMLSelectElement).value).toBe("GET");
  });

  it("adds, renames, fills and removes a header, removing the key when none are left", () => {
    const h = httpCard({ path: "/" });
    const headers = screen.getByText("Headers", { selector: "label" }).parentElement!;
    fireEvent.click(within(headers).getByRole("button", { name: "+ Add" }));
    expect(h.commands().a.headers).toEqual({ key1: "" });
    fireEvent.click(within(headers).getByRole("button", { name: "+ Add" }));
    expect(h.commands().a.headers).toEqual({ key1: "", key2: "" });
    const [k1] = within(headers).getAllByPlaceholderText("Header-Name");
    type(k1, "Content-Type");
    expect(Object.keys(h.commands().a.headers!)).toEqual(["Content-Type", "key2"]);
    expect(document.activeElement).toBe(within(headers).getAllByPlaceholderText("Header-Name")[0]);
    const [v1] = within(headers).getAllByPlaceholderText("e.g. text/xml");
    type(v1, "text/xml");
    expect(h.commands().a.headers).toEqual({ "Content-Type": "text/xml", key2: "" });
    expect(document.activeElement).toBe(within(headers).getAllByPlaceholderText("e.g. text/xml")[0]);
    // A key cleared drops its row.
    type(within(headers).getAllByPlaceholderText("Header-Name")[1], "");
    expect(h.commands().a.headers).toEqual({ "Content-Type": "text/xml" });
    const remove = within(headers).getAllByRole("button").filter((b) => b.textContent !== "+ Add");
    fireEvent.click(remove[0]);
    expect("headers" in h.commands().a).toBe(false);
  });

  it("writes query parameters the same way", () => {
    const h = httpCard({ path: "/" });
    const query = screen.getByText("Query Parameters", { selector: "label" }).parentElement!;
    fireEvent.click(within(query).getByRole("button", { name: "+ Add" }));
    expect(h.commands().a.query_params).toEqual({ key1: "" });
    type(within(query).getByPlaceholderText("value"), "{level}");
    expect(h.commands().a.query_params).toEqual({ key1: "{level}" });
    fireEvent.click(within(query).getAllByRole("button").filter((b) => b.textContent !== "+ Add")[0]);
    expect("query_params" in h.commands().a).toBe(false);
  });
});

describe("Send over UDP", () => {
  it("ticking it drops every other way of sending and keeps the params; unticking puts a send string back", () => {
    const params = { level: { type: "integer" } };
    const h = host(draftWith(
      { a: { label: "A", send: "", method: "POST", path: "/x", body: "b", params } },
      { transport: "http" },
    ));
    open("a");
    fireEvent.click(screen.getByLabelText(/Send over UDP instead/));
    expect(h.commands().a).toEqual({ label: "A", udp: { payload: "" }, params });
    expect(screen.queryByLabelText(/Reads its value back/)).toBeNull();
    fireEvent.click(screen.getByLabelText(/Send over UDP instead/));
    expect(h.commands().a).toEqual({ label: "A", params, send: "" });
    expect(screen.getByLabelText(/Reads its value back/)).toBeTruthy();
  });

  it("writes the message, host, port and broadcast, removing what is cleared", () => {
    const h = host(draftWith({ a: { label: "A", udp: { payload: "" } } }));
    open("a");
    type(screen.getByLabelText("UDP message"), "{var}:{value}");
    expect(h.commands().a.udp).toEqual({ payload: "{var}:{value}" });
    expect(document.activeElement).toBe(screen.getByLabelText("UDP message"));
    type(screen.getByLabelText("UDP host"), "10.0.0.9");
    expect(h.commands().a.udp?.host).toBe("10.0.0.9");
    expect(document.activeElement).toBe(screen.getByLabelText("UDP host"));
    type(screen.getByLabelText("UDP host"), "");
    expect("host" in h.commands().a.udp!).toBe(false);
    type(screen.getByLabelText("UDP port"), "5000");
    expect(h.commands().a.udp?.port).toBe(5000);
    type(screen.getByLabelText("UDP port"), "");
    expect("port" in h.commands().a.udp!).toBe(false);
    fireEvent.click(screen.getByLabelText(/Broadcast to the whole network/));
    expect(h.commands().a.udp?.broadcast).toBe(true);
    fireEvent.click(screen.getByLabelText(/Broadcast to the whole network/));
    expect("broadcast" in h.commands().a.udp!).toBe(false);
  });

  it("a magic packet goes back to a message with the MAC removed and an empty payload", () => {
    const h = host(draftWith(
      { a: { label: "A", udp: { magic_packet: "mac", port: 9 } } },
      { config_schema: { mac: { type: "string", label: "MAC" } } } as Partial<DriverDefinition>,
    ));
    open("a");
    expect(screen.getByLabelText("A Wake-on-LAN magic packet")).toHaveProperty("checked", true);
    fireEvent.click(screen.getByLabelText("A message"));
    expect(h.commands().a.udp).toEqual({ port: 9, payload: "" });
  });

  it("picks the MAC field, and shows one the driver no longer declares", () => {
    const h = host(draftWith(
      { a: { label: "A", udp: { magic_packet: "old_mac" } } },
      { config_schema: { mac: { type: "string", label: "MAC" }, alt_mac: { type: "string", label: "Alt" } } } as Partial<DriverDefinition>,
    ));
    open("a");
    const select = screen.getByLabelText("MAC address from") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
      "old_mac", "mac (config field)", "alt_mac (config field)",
    ]);
    fireEvent.change(select, { target: { value: "alt_mac" } });
    expect(h.commands().a.udp).toEqual({ magic_packet: "alt_mac" });
    const after = screen.getByLabelText("MAC address from") as HTMLSelectElement;
    expect(Array.from(after.options).map((o) => o.value)).toEqual(["mac", "alt_mac"]);
  });

  it("a magic packet with no field picked says so, and the port hint names 9", () => {
    host(draftWith({ a: { label: "A", udp: { magic_packet: "" } } }));
    open("a");
    const select = screen.getByLabelText("MAC address from") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(["Pick a field"]);
    expect(screen.getByText(/Wake-on-LAN listens on 9/)).toBeTruthy();
    expect((screen.getByLabelText("UDP port") as HTMLInputElement).placeholder).toBe("9");
  });
});

describe("Puts the device to sleep, held across a re-render", () => {
  it("stays ticked with nothing to pick, then names the waker picked", () => {
    const h = host(draftWith({
      sleep: { label: "Sleep", send: "S\r" },
      wake: { label: "Wake", send: "W\r", available_offline: true, restarts_device_for: 20 },
      wake2: { label: "", send: "W2\r", available_offline: true },
    }));
    open("sleep");
    fireEvent.click(screen.getByLabelText(/Puts the device to sleep/));
    expect(h.commands().sleep.sleeps_device).toBe("wake");
    const select = screen.getByLabelText("Woken by") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(["Wake (wake)", "wake2"]);
    fireEvent.change(select, { target: { value: "wake2" } });
    expect(h.commands().sleep.sleeps_device).toBe("wake2");
    expect(screen.getByText(/wake2 also needs "Restarts the device for" set\./)).toBeTruthy();
  });

  it("ticked with no waker keeps the box ticked and the warning up after a re-render", () => {
    const h = host(draftWith({
      sleep: { label: "Sleep", send: "S\r" },
      other: { label: "Other", send: "O\r" },
    }));
    open("sleep");
    fireEvent.click(screen.getByLabelText(/Puts the device to sleep/));
    expect(h.seen).not.toHaveBeenCalled();
    // An unrelated edit re-renders the card.
    type(fieldUnder("Display Label"), "Sleep now");
    expect(screen.getByLabelText(/Puts the device to sleep/)).toHaveProperty("checked", true);
    expect(screen.getByText(/No command can wake it yet/)).toBeTruthy();
    const select = screen.getByLabelText("Woken by") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(["Pick a command"]);
  });

  it("a wake command that is not in the driver is said, by its id", () => {
    host(draftWith({ sleep: { label: "Sleep", send: "S\r", sleeps_device: "gone" } }));
    open("sleep");
    expect(screen.getByText('"gone" is not a command in this driver.')).toBeTruthy();
    const select = screen.getByLabelText("Woken by") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(["gone"]);
  });
});

describe("Reads its value back, held across a re-render", () => {
  it("adds and removes query rows, and the last row removed leaves one blank row", () => {
    const h = host(draftWith({ a: { label: "A", send: "SV", read_back: ["GV", "GM"] } }));
    open("a");
    expect((screen.getByLabelText("Read-back query 2") as HTMLInputElement).value).toBe("GM");
    fireEvent.click(screen.getByRole("button", { name: "+ Add a query" }));
    expect(h.seen).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Read-back query 3")).toBeTruthy();
    type(screen.getByLabelText("Read-back query 3"), "GX");
    expect(h.commands().a.read_back).toEqual(["GV", "GM", "GX"]);
    expect(document.activeElement).toBe(screen.getByLabelText("Read-back query 3"));
    fireEvent.click(screen.getByLabelText("Remove read-back query 1"));
    expect(h.commands().a.read_back).toEqual(["GM", "GX"]);
    fireEvent.click(screen.getByLabelText("Remove read-back query 1"));
    expect(h.commands().a.read_back).toBe("GX");
    fireEvent.click(screen.getByLabelText("Remove read-back query 1"));
    expect("read_back" in h.commands().a).toBe(false);
    expect((screen.getByLabelText("Read-back query 1") as HTMLInputElement).value).toBe("");
    expect(screen.getByLabelText(/Reads its value back/)).toHaveProperty("checked", true);
  });

  it("a blank row being typed survives the write of the row above", () => {
    const h = host(draftWith({ a: { label: "A", send: "SV", read_back: "GV" } }));
    open("a");
    fireEvent.click(screen.getByRole("button", { name: "+ Add a query" }));
    type(screen.getByLabelText("Read-back query 1"), "GV2");
    expect(h.commands().a.read_back).toBe("GV2");
    expect(screen.getByLabelText("Read-back query 2")).toBeTruthy();
  });

  it("follows a value replaced from outside", () => {
    const d = draftWith({ a: { label: "A", send: "SV\r", read_back: "GV\r" } });
    const { rerender } = render(<CommandBuilder draft={d} onUpdate={vi.fn()} />);
    open("a");
    rerender(<CommandBuilder draft={draftWith({ a: { label: "A", send: "SV\r", read_back: ["Q1", "Q2"] } })} onUpdate={vi.fn()} />);
    expect((screen.getByLabelText("Read-back query 2") as HTMLInputElement).value).toBe("Q2");
    rerender(<CommandBuilder draft={draftWith({ a: { label: "A", send: "SV\r" } })} onUpdate={vi.fn()} />);
    expect((screen.getByLabelText("Read-back query 1") as HTMLInputElement).value).toBe("");
  });

  it("on an OSC command, choosing queries writes the rows, and none removes the key", () => {
    const h = host(draftWith({ a: { label: "A", address: "/x", args: [], read_back: true } }, { transport: "osc" }));
    open("a");
    expect(screen.queryByLabelText("Read-back query 1")).toBeNull();
    fireEvent.change(screen.getByLabelText("Read back with"), { target: { value: "queries" } });
    expect("read_back" in h.commands().a).toBe(false);
    fireEvent.click(screen.getByLabelText(/Reads its value back/));
    fireEvent.click(screen.getByLabelText(/Reads its value back/));
    expect(h.commands().a.read_back).toBe(true);
    expect(screen.getByRole("option", { name: "This command's own address, with no arguments" })).toBeTruthy();
    expect((screen.getByLabelText("Read back with") as HTMLSelectElement).value).toBe("own");
  });

  it("a command on an OSC driver with no address yet is offered its own address", () => {
    const h = host(draftWith({ a: { label: "A", send: "" } }, { transport: "osc" }));
    open("a");
    fireEvent.click(screen.getByLabelText(/Reads its value back/));
    expect(h.commands().a.read_back).toBe(true);
    expect((screen.getByLabelText("Read back with") as HTMLSelectElement).value).toBe("own");
  });

  it("an HTTP command reading back with queries is captioned once", () => {
    host(draftWith({ a: { label: "A", send: "", path: "/x", read_back: "/status" } }, { transport: "http" }));
    open("a");
    expect(screen.getAllByText("Read back with", { selector: "label" })).toHaveLength(1);
    expect((screen.getByLabelText("Read-back query 1") as HTMLInputElement).value).toBe("/status");
  });

  it("names the other commands as suggestions, never this one", () => {
    const { container } = render(<CommandBuilder draft={draftWith({
      a: { label: "A", send: "SV\r", read_back: "b" },
      b: { label: "B", send: "GV\r" },
      c: { label: "C", send: "GC\r" },
    })} onUpdate={vi.fn()} />);
    open("a");
    const list = container.querySelector("datalist#read-back-commands-a")!;
    expect(Array.from(list.querySelectorAll("option")).map((o) => o.getAttribute("value"))).toEqual(["b", "c"]);
  });
});

describe("the send form follows the transport", () => {
  it("a command with an address on a TCP driver still shows the command string", () => {
    host(draftWith({ a: { label: "A", send: "X", address: "/x" } }));
    open("a");
    expect(fieldUnder("Command String")).toBeTruthy();
    expect(screen.queryByText("OSC Address")).toBeNull();
    // ...while its read-back is offered the OSC form.
    fireEvent.click(screen.getByLabelText(/Reads its value back/));
    expect(screen.getByLabelText("Read back with")).toBeTruthy();
  });
});
