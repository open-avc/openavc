import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// A YAML command may leave out `params:` altogether (`power_on: {label, send}`),
// and thirty catalog drivers do. Opening such a command in the Driver Builder
// must show its card, not crash the Devices view.

import { CommandBuilder } from "./CommandBuilder";
import type { DriverDefinition } from "../../api/types";

const DRAFT = {
  id: "acme_widget",
  name: "Acme Widget",
  manufacturer: "Acme",
  category: "utility",
  version: "1.0.0",
  transport: "tcp",
  default_config: { host: "", port: 4000 },
  config_schema: {},
  state_variables: { power: { type: "boolean", label: "Power" } },
  commands: { power_on: { label: "Power On", send: "PWR 1\r" } },
  responses: [],
} as unknown as DriverDefinition;

describe("a command with no params", () => {
  it("opens, and adding a parameter starts it from nothing", () => {
    const onUpdate = vi.fn();
    render(<CommandBuilder draft={DRAFT} onUpdate={onUpdate} />);
    fireEvent.click(screen.getByText("power_on"));
    expect(screen.getByDisplayValue("Power On")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "+ Add Parameter" }));
    const commands = onUpdate.mock.calls.at(-1)?.[0].commands;
    expect(Object.keys(commands.power_on.params)).toHaveLength(1);
  });
});
