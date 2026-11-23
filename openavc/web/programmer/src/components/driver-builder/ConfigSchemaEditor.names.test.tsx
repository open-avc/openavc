import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfigSchemaEditor } from "./ConfigSchemaEditor";
import type { DriverDefinition } from "../../api/types";

// A config field's ID is what a {placeholder} names, so it keeps to the names
// a placeholder can carry: letters, digits and underscores, not starting with
// a digit. The driver check refuses any other, so the editor never makes one.

function draft(): DriverDefinition {
  return {
    id: "acme_widget",
    name: "Acme Widget",
    transport: "tcp",
    default_config: { zone: 1 },
    config_schema: { zone: { type: "integer", label: "Zone" } },
  } as unknown as DriverDefinition;
}

async function renameTo(typed: string) {
  const onUpdate = vi.fn();
  render(<ConfigSchemaEditor draft={draft()} onUpdate={onUpdate} />);
  await userEvent.click(screen.getByText("zone"));
  const id = screen.getByDisplayValue("zone");
  await userEvent.clear(id);
  await userEvent.type(id, `${typed}{Enter}`);
  const update = onUpdate.mock.calls.at(-1)?.[0] as { config_schema: Record<string, unknown> };
  return Object.keys(update.config_schema);
}

describe("ConfigSchemaEditor: a field ID a placeholder can name", () => {
  it("drops a leading digit as it is typed", async () => {
    expect(await renameTo("2nd_zone")).toEqual(["nd_zone"]);
  });

  it("keeps a digit after the first character", async () => {
    expect(await renameTo("zone2")).toEqual(["zone2"]);
  });
});
