import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// Only a TCP device has a second connection to turn away, so the Simulation
// tab offers Connections there and nowhere else.

import { SimulatorEditor } from "./SimulatorEditor";
import type { DriverDefinition } from "../../api/types";

function draft(transport: string, simulator?: Record<string, unknown>): DriverDefinition {
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
    ...(simulator ? { simulator } : {}),
  } as unknown as DriverDefinition;
}

describe("the Simulation tab's Connections", () => {
  it.each(["serial", "udp", "osc", "http"])("is not offered for a %s driver", (transport) => {
    render(<SimulatorEditor draft={draft(transport)} onUpdate={vi.fn()} />);
    expect(screen.queryByRole("heading", { name: "Connections" })).toBeNull();
  });

  it("writes the choice for a TCP driver", () => {
    const onUpdate = vi.fn();
    render(<SimulatorEditor draft={draft("tcp")} onUpdate={onUpdate} />);
    fireEvent.change(screen.getByLabelText("When another connection arrives"), {
      target: { value: "newest" },
    });
    expect(onUpdate).toHaveBeenCalledWith({ simulator: { single_client: "newest" } });
  });

  it("leaves the key out to answer every connection", () => {
    const onUpdate = vi.fn();
    render(
      <SimulatorEditor draft={draft("tcp", { single_client: "reject" })} onUpdate={onUpdate} />,
    );
    const select = screen.getByLabelText("When another connection arrives") as HTMLSelectElement;
    expect(select.value).toBe("reject");
    fireEvent.change(select, { target: { value: "" } });
    expect(onUpdate).toHaveBeenCalledWith({ simulator: { single_client: undefined } });
  });
});
