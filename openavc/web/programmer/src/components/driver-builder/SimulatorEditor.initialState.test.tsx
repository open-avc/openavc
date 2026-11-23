import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// A value typed into the Simulation tab's Initial State is saved as the
// variable's type, so the simulator starts a number at a number. A float was
// saved as the text typed, where a number was saved as a number.

import { SimulatorEditor } from "./SimulatorEditor";
import type { DriverDraft } from "../../store/driverBuilderStore.helpers";

function draft(type: string): DriverDraft {
  return {
    id: "acme_widget",
    name: "Acme Widget",
    manufacturer: "Acme",
    category: "display",
    version: "1.0.0",
    transport: "tcp",
    default_config: { host: "", port: 5000 },
    config_schema: {},
    state_variables: { gain: { type, label: "Gain" } },
    commands: {},
    responses: [],
  } as unknown as DriverDraft;
}

function typeInitialValue(type: string, typed: string): unknown {
  const onUpdate = vi.fn();
  render(<SimulatorEditor draft={draft(type)} onUpdate={onUpdate} />);
  const row = screen.getByTitle("gain").parentElement!;
  fireEvent.change(row.querySelector("input")!, { target: { value: typed } });
  const saved = onUpdate.mock.calls.at(-1)![0] as { simulator: { initial_state: Record<string, unknown> } };
  return saved.simulator.initial_state.gain;
}

describe("the Simulation tab's Initial State", () => {
  it.each(["number", "float"])("saves a %s as a real number", (type) => {
    expect(typeInitialValue(type, "1.5")).toBe(1.5);
  });

  it("saves an integer as a whole number", () => {
    expect(typeInitialValue("integer", "7")).toBe(7);
  });

  it("saves a boolean as true or false", () => {
    expect(typeInitialValue("boolean", "true")).toBe(true);
  });

  it("keeps text as typed", () => {
    expect(typeInitialValue("string", "1.5")).toBe("1.5");
  });
});
