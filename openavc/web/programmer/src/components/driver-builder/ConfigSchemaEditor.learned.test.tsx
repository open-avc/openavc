import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfigSchemaEditor } from "./ConfigSchemaEditor";
import type { DriverDefinition } from "../../api/types";

// The Driver Builder half of `learned_from`: a config field can be filled in
// from a state variable the device reports. The picker offers the driver's
// own state variables and is not offered on a secret field or a connection
// field. A number field filled in from the device takes the range a reported
// value has to fall inside.

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
  it("is not offered on a connection field", async () => {
    const d = draft({ config_schema: { username: { type: "string", label: "Username" } } });
    render(<ConfigSchemaEditor draft={d} onUpdate={vi.fn()} />);
    await userEvent.click(screen.getByText("username"));
    expect(screen.queryByLabelText("Filled In From")).toBeNull();
  });

  it("asks a number field filled in from the device for its range", async () => {
    const onUpdate = vi.fn();
    const d = draft({
      state_variables: { trim_reported: { type: "number" } },
      config_schema: {
        gain: { type: "float", label: "Gain" },
        trim: { type: "float", label: "Trim", learned_from: "trim_reported", max: 9.5 },
      },
    });
    render(<ConfigSchemaEditor draft={d} onUpdate={onUpdate} />);
    await userEvent.click(screen.getByText("gain"));
    expect(screen.queryByLabelText("Min (optional)")).toBeNull();
    await userEvent.click(screen.getByText("trim"));
    fireEvent.change(screen.getByLabelText("Min (optional)"), { target: { value: "0.5" } });
    let update = onUpdate.mock.calls.at(-1)?.[0] as { config_schema: Record<string, { min?: number; max?: number }> };
    expect(update.config_schema.trim.min).toBe(0.5);
    fireEvent.change(screen.getByLabelText("Max (optional)"), { target: { value: "" } });
    update = onUpdate.mock.calls.at(-1)?.[0] as { config_schema: Record<string, { min?: number; max?: number }> };
    expect(update.config_schema.trim.max).toBeUndefined();
  });
});
