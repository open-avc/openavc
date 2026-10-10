import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfigSchemaEditor } from "./ConfigSchemaEditor";
import type { DriverDefinition } from "../../api/types";

// The Driver Builder half of `learned_from`: a config field can be filled in
// from a state variable the device reports. The picker offers the driver's
// own state variables and is not offered on a secret field.

function draft(over: Record<string, unknown> = {}): DriverDefinition {
  return {
    id: "acme_widget",
    name: "Acme Widget",
    transport: "tcp",
    state_variables: { power: { type: "boolean" }, model: { type: "string" } },
    config_schema: { model: { type: "string", label: "Model" } },
    ...over,
  } as unknown as DriverDefinition;
}

describe("ConfigSchemaEditor: Filled In From", () => {
  it("offers the state variables and writes learned_from", async () => {
    const onUpdate = vi.fn();
    render(<ConfigSchemaEditor draft={draft()} onUpdate={onUpdate} />);
    await userEvent.click(screen.getByText("model"));
    const picker = screen.getByLabelText("Filled In From") as HTMLSelectElement;
    expect(Array.from(picker.options).map((o) => o.value)).toEqual(["", "model", "power"]);
    await userEvent.selectOptions(picker, "model");
    const update = onUpdate.mock.calls.at(-1)?.[0] as { config_schema: Record<string, { learned_from?: string }> };
    expect(update.config_schema.model.learned_from).toBe("model");
  });

  it("clearing it removes learned_from", async () => {
    const onUpdate = vi.fn();
    const d = draft({ config_schema: { model: { type: "string", label: "Model", learned_from: "model" } } });
    render(<ConfigSchemaEditor draft={d} onUpdate={onUpdate} />);
    await userEvent.click(screen.getByText("model"));
    await userEvent.selectOptions(screen.getByLabelText("Filled In From"), "");
    const update = onUpdate.mock.calls.at(-1)?.[0] as { config_schema: Record<string, { learned_from?: string }> };
    expect(update.config_schema.model.learned_from).toBeUndefined();
  });

  it("is not offered on a secret field", async () => {
    const d = draft({ config_schema: { pin: { type: "string", label: "PIN", secret: true } } });
    render(<ConfigSchemaEditor draft={d} onUpdate={vi.fn()} />);
    await userEvent.click(screen.getByText("pin"));
    expect(screen.queryByLabelText("Filled In From")).toBeNull();
  });
});
