import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// The simulator models no capacity for an HTTP device, so the Simulation tab
// does not offer the setting there; TCP, serial, UDP and OSC devices keep it.

import { SimulatorEditor } from "./SimulatorEditor";
import type { DriverDefinition } from "../../api/types";

function draft(transport: string): DriverDefinition {
  return {
    id: "acme_widget",
    name: "Acme Widget",
    manufacturer: "Acme",
    category: "display",
    version: "1.0.0",
    transport,
    default_config: { host: "", port: 5000 },
    config_schema: {},
    state_variables: {},
    commands: {},
    responses: [],
  } as unknown as DriverDefinition;
}

describe("the Simulation tab's Capacity", () => {
  it("is not offered for an HTTP driver", () => {
    render(<SimulatorEditor draft={draft("http")} onUpdate={vi.fn()} />);
    expect(screen.queryByRole("heading", { name: "Capacity" })).toBeNull();
  });

  it.each(["tcp", "serial", "udp", "osc"])("is offered for a %s driver", (transport) => {
    render(<SimulatorEditor draft={draft(transport)} onUpdate={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "Capacity" })).toBeTruthy();
  });
});
