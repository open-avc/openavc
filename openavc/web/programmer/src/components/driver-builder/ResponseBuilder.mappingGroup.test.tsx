import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// A regex rule's mapping may leave out its capture group: the driver then
// reads the whole text the pattern matched. The editor says so instead of
// naming an undefined capture, and a mapping added after it takes the next
// capture group rather than a number it cannot work out.

import { ResponseBuilder } from "./ResponseBuilder";
import type { DriverDefinition, DriverResponseDef } from "../../api/types";

function draft(): DriverDefinition {
  return {
    id: "acme_widget",
    name: "Acme Widget",
    transport: "tcp",
    state_variables: {
      power: { type: "string", label: "Power" },
      input: { type: "string", label: "Input" },
    },
    commands: {},
    responses: [{ match: "PWR=\\w+", mappings: [{ state: "power" }] }],
    polling: {},
  } as unknown as DriverDefinition;
}

describe("A mapping with no capture group", () => {
  it("is shown as reading the whole match", () => {
    render(<ResponseBuilder draft={draft()} onUpdate={vi.fn()} />);
    expect(screen.queryByText(/undefined/)).toBeNull();
    const label = screen.getByText("Whole match →");
    expect(label.getAttribute("title")).toBe(
      "The whole text the regex pattern matched",
    );
  });

  it("is followed by capture group 1 when a mapping is added", () => {
    const onUpdate = vi.fn();
    render(<ResponseBuilder draft={draft()} onUpdate={onUpdate} />);
    fireEvent.click(screen.getByText("+ Add Mapping"));
    const responses = onUpdate.mock.calls.at(-1)?.[0].responses as DriverResponseDef[];
    const mappings = responses[0].mappings ?? [];
    expect(mappings.map((m) => m.group)).toEqual([undefined, 1]);
  });
});
