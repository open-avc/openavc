import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// The Driver Builder half of a command's read_back (the runtime half is
// ConfigurableDriver._read_back). What has to hold: on a byte-stream command
// the field writes the queries typed (one as a string, more as a list) and
// removes the key when unticked; on an OSC or HTTP command ticking it reads
// the command itself back (true) until queries are chosen instead; and a
// command renamed while another reads back with it keeps the reference.

import { CommandBuilder, readBackValue } from "./CommandBuilder";
import type { DriverDefinition } from "../../api/types";

function draftWith(
  commands: DriverDefinition["commands"],
  transport = "tcp",
): DriverDefinition {
  return {
    id: "acme_display",
    name: "Acme Display",
    manufacturer: "Acme",
    category: "display",
    version: "1.0.0",
    transport,
    default_config: { host: "", port: 5000 },
    config_schema: { host: { type: "string", label: "Host" } },
    state_variables: { volume: { type: "integer", label: "Volume" } },
    commands,
    responses: [],
  } as unknown as DriverDefinition;
}

function openCommand(id: string) {
  fireEvent.click(screen.getByText(id));
}

function lastCommands(onUpdate: ReturnType<typeof vi.fn>) {
  const calls = onUpdate.mock.calls;
  return calls[calls.length - 1][0].commands;
}

describe("Reads its value back", () => {
  it("a byte-stream command writes the query typed", () => {
    const onUpdate = vi.fn();
    const draft = draftWith({
      set_volume: { label: "Set Volume", send: "SV{level}\r", params: {} },
    });
    render(<CommandBuilder draft={draft} onUpdate={onUpdate} />);
    openCommand("set_volume");
    fireEvent.click(screen.getByLabelText(/Reads its value back/));
    // Ticked with nothing typed yet carries no value.
    expect(onUpdate).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Read-back query 1"), {
      target: { value: "GV\\r" },
    });
    expect(lastCommands(onUpdate).set_volume.read_back).toBe("GV\\r");
  });

  it("two queries are a list, and unticking removes the key", () => {
    const onUpdate = vi.fn();
    const draft = draftWith({
      set_volume: {
        label: "Set Volume",
        send: "SV{level}\r",
        params: {},
        // As the Builder carries it: escape text, which the runtime decodes.
        read_back: ["GV\\r", "GM\\r"],
      },
    });
    render(<CommandBuilder draft={draft} onUpdate={onUpdate} />);
    openCommand("set_volume");
    expect((screen.getByLabelText("Read-back query 2") as HTMLInputElement).value).toBe(
      "GM\\r",
    );
    fireEvent.click(screen.getByLabelText(/Reads its value back/));
    expect("read_back" in lastCommands(onUpdate).set_volume).toBe(false);
  });

  it("an OSC command reads its own address back when ticked", () => {
    const onUpdate = vi.fn();
    const draft = draftWith(
      {
        set_fader: {
          label: "Set Fader",
          send: "",
          address: "/ch/01/fader",
          args: [],
          params: {},
        },
      },
      "osc",
    );
    render(<CommandBuilder draft={draft} onUpdate={onUpdate} />);
    openCommand("set_fader");
    fireEvent.click(screen.getByLabelText(/Reads its value back/));
    expect(lastCommands(onUpdate).set_fader.read_back).toBe(true);
  });

  it("an HTTP command offers a GET of its own path or queries", () => {
    const draft = draftWith(
      {
        set_tally: {
          label: "Set Tally",
          send: "",
          method: "POST",
          path: "/api/tally",
          params: {},
          read_back: true,
        },
      },
      "http",
    );
    render(<CommandBuilder draft={draft} onUpdate={vi.fn()} />);
    openCommand("set_tally");
    const select = screen.getByLabelText("Read back with") as HTMLSelectElement;
    expect(select.value).toBe("own");
    expect(Array.from(select.options).map((o) => o.text)).toEqual([
      "A GET of this command's own path",
      "The queries below",
    ]);
    expect(screen.queryByLabelText("Read-back query 1")).toBeNull();
  });

  it("renaming a command another reads back with follows it", () => {
    const onUpdate = vi.fn();
    const draft = draftWith({
      start_record: {
        label: "Start Record",
        send: "REC\r",
        params: {},
        read_back: "record_status",
      },
      record_status: { label: "Record Status", send: "REC?\r", params: {} },
    });
    render(<CommandBuilder draft={draft} onUpdate={onUpdate} />);
    openCommand("record_status");
    const idInput = screen.getByDisplayValue("record_status");
    fireEvent.change(idInput, { target: { value: "get_record" } });
    fireEvent.blur(idInput);
    const commands = lastCommands(onUpdate);
    expect(commands.start_record.read_back).toBe("get_record");
    expect("get_record" in commands).toBe(true);
  });

  it("blank rows are not queries", () => {
    expect(readBackValue(["", "  "])).toBeUndefined();
    expect(readBackValue(["A\r", ""])).toBe("A\r");
    expect(readBackValue(["A\r", "B\r"])).toEqual(["A\r", "B\r"]);
  });
});
