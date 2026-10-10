import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfigSchemaEditor } from "./ConfigSchemaEditor";
import type { DriverDefinition } from "../../api/types";

// An enum config field's choices may carry labels ({value, label}), as the
// runtime and the Add Device form allow. The Builder shows the labels and keeps
// them through an edit; it used to render the objects as text and crash the
// view the moment such a field was opened.

const LABELLED = [
  { value: "2", label: "2 channels" },
  { value: "4", label: "4 channels" },
  "8",
];

function draft(): DriverDefinition {
  return {
    id: "acme_widget",
    name: "Acme Widget",
    transport: "tcp",
    default_config: { channels: "4" },
    config_schema: {
      channels: { type: "enum", label: "Channels", values: LABELLED },
    },
  } as unknown as DriverDefinition;
}

function lastSchema(onUpdate: ReturnType<typeof vi.fn>) {
  const update = onUpdate.mock.calls.at(-1)?.[0] as {
    config_schema: Record<string, { values?: unknown[] }>;
  };
  return update.config_schema;
}

describe("ConfigSchemaEditor: labelled choices", () => {
  it("opens the field and shows each label in the Default Value list", async () => {
    render(<ConfigSchemaEditor draft={draft()} onUpdate={vi.fn()} />);
    await userEvent.click(screen.getByText("channels"));
    const defaults = screen
      .getAllByDisplayValue("4 channels")
      .find((el) => el.tagName === "SELECT") as HTMLSelectElement;
    expect(Array.from(defaults.options).map((o) => [o.value, o.text])).toEqual([
      ["", "(none)"],
      ["2", "2 channels"],
      ["4", "4 channels"],
      ["8", "8"],
    ]);
  });

  it("lists value and label rows, and keeps the labels when a row is edited", async () => {
    const onUpdate = vi.fn();
    render(<ConfigSchemaEditor draft={draft()} onUpdate={onUpdate} />);
    await userEvent.click(screen.getByText("channels"));
    const label = screen
      .getAllByDisplayValue("2 channels")
      .find((el) => el.tagName === "INPUT")!;
    await userEvent.clear(label);
    await userEvent.type(label, "Two");
    expect(lastSchema(onUpdate).channels.values).toEqual([
      { value: "2", label: "Two" },
      { value: "4", label: "4 channels" },
      "8",
    ]);
  });

  it("adds a choice with no label as a bare value", async () => {
    const onUpdate = vi.fn();
    render(<ConfigSchemaEditor draft={draft()} onUpdate={onUpdate} />);
    await userEvent.click(screen.getByText("channels"));
    await userEvent.click(screen.getByRole("button", { name: /Add value/ }));
    const valueInputs = within(screen.getByText("Allowed Values").parentElement!)
      .getAllByPlaceholderText("e.g. 0f");
    await userEvent.type(valueInputs.at(-1)!, "16");
    expect(lastSchema(onUpdate).channels.values?.at(-1)).toBe("16");
  });
});
