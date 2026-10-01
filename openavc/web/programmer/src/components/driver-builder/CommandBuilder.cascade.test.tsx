import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// The Driver Builder half of a cascade's narrowing (the runtime half is the
// picker in components/shared/paramOptions.ts): ticking a type or typing a
// unit writes the list the picker reads, clearing them removes the key, and
// re-pointing the cascade at another child_id param keeps them.

import { CommandBuilder } from "./CommandBuilder";
import type { DriverDefinition } from "../../api/types";

function draft(optionsFrom: Record<string, unknown>): DriverDefinition {
  return {
    id: "acme_dsp",
    name: "Acme DSP",
    manufacturer: "Acme",
    category: "audio",
    version: "1.0.0",
    transport: "tcp",
    default_config: { host: "", port: 5000 },
    config_schema: {},
    state_variables: {},
    child_entity_types: {
      block: {
        id_format: { type: "integer", min: 1, max: 8 },
        state_variables: { mute: { type: "boolean", control: true } },
      },
    },
    commands: {
      toggle: {
        label: "Toggle",
        send: "T {block} {control}\r",
        params: {
          block: { type: "child_id", child_type: "block" },
          other: { type: "child_id", child_type: "block" },
          control: { type: "string", options_from: optionsFrom },
        },
      },
    },
    responses: [],
  } as unknown as DriverDefinition;
}

function written(onUpdate: ReturnType<typeof vi.fn>) {
  const call = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0];
  return call.commands.toggle.params.control.options_from;
}

function open(d: DriverDefinition, onUpdate: ReturnType<typeof vi.fn>) {
  render(<CommandBuilder draft={d} onUpdate={onUpdate} />);
  fireEvent.click(screen.getByText("toggle"));
}

describe("narrowing a cascade in the Driver Builder", () => {
  it("ticking Boolean writes types", () => {
    const onUpdate = vi.fn();
    open(draft({ param: "block", source: "child_schema" }), onUpdate);
    fireEvent.click(screen.getByTestId("param-options-from-type-control-boolean"));
    expect(written(onUpdate)).toEqual({ param: "block", source: "child_schema", types: ["boolean"] });
  });

  it("typing units writes the parsed list, and clearing them removes the key", () => {
    const onUpdate = vi.fn();
    open(draft({ param: "block", source: "child_schema", types: ["number"] }), onUpdate);
    const box = screen.getByTestId("param-options-from-units-control");
    fireEvent.change(box, { target: { value: "dB, %" } });
    expect(written(onUpdate)).toEqual({
      param: "block", source: "child_schema", types: ["number"], units: ["dB", "%"],
    });
    fireEvent.change(box, { target: { value: "" } });
    expect(written(onUpdate)).toEqual({ param: "block", source: "child_schema", types: ["number"] });
  });

  it("keeps the narrowing when the cascade moves to another child_id param", () => {
    const onUpdate = vi.fn();
    open(draft({ param: "block", source: "child_schema", types: ["boolean"] }), onUpdate);
    fireEvent.change(screen.getByTestId("param-options-from-control"), { target: { value: "other" } });
    expect(written(onUpdate)).toEqual({ param: "other", source: "child_schema", types: ["boolean"] });
  });
});
