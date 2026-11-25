import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";

// A button's "Ask before running" tick reads the action's confirm the way the
// server does: a blank sentence asks nothing, and a sentence typed starting
// with a space keeps the field open.

import type { DriverActionDef, DriverDefinition } from "../../api/types";
import { ActionsEditor } from "./ActionsEditor";

function draftWith(action: DriverActionDef, wipe: Record<string, unknown> = {}): DriverDefinition {
  return {
    id: "acme_widget",
    name: "Acme Widget",
    manufacturer: "Acme",
    category: "utility",
    transport: "tcp",
    commands: { wipe: { label: "Wipe", send: "WIPE", ...wipe } },
    actions: [action],
  } as unknown as DriverDefinition;
}

let latest: DriverDefinition | null = null;

function Host({ initial }: { initial: DriverDefinition }) {
  const [draft, setDraft] = useState(initial);
  latest = draft;
  return <ActionsEditor draft={draft} onUpdate={(partial) => setDraft((d) => ({ ...d, ...partial }))} />;
}

function openCard(draft: DriverDefinition) {
  render(<Host initial={draft} />);
  fireEvent.click(screen.getByText("wipe_button"));
}

const tick = () => screen.getByLabelText(/Ask before running/) as HTMLInputElement;
const action = () => (latest?.actions ?? [])[0];

describe("a button's Ask before running", () => {
  it("reads a blank sentence as asking nothing", () => {
    openCard(draftWith({ id: "wipe_button", kind: "command", command: "wipe", confirm: "  " }));
    expect(tick().checked).toBe(false);
    expect(screen.queryByText("Confirmation Message (optional)")).toBeNull();
  });

  it("keeps the message field open while a sentence starts with a space", () => {
    openCard(draftWith({ id: "wipe_button", kind: "command", command: "wipe", confirm: true }));
    expect(tick().checked).toBe(true);
    fireEvent.change(screen.getByPlaceholderText("Leave blank for the generic prompt"), {
      target: { value: " " },
    });
    expect(action().confirm).toBe(true);
    expect(screen.getByText("Confirmation Message (optional)")).toBeTruthy();
  });
});

describe("a button for a command that asks first", () => {
  const asking = { confirm: "Erases every preset." };

  it("shows the command's question as ticked, and unticking turns it off for the button", () => {
    openCard(draftWith({ id: "wipe_button", kind: "command", command: "wipe" }, asking));
    expect(tick().checked).toBe(true);
    fireEvent.click(tick());
    expect(action().confirm).toBe(false);
    expect(tick().checked).toBe(false);
    fireEvent.click(tick());
    expect("confirm" in action() && action().confirm !== undefined).toBe(false);
    expect(tick().checked).toBe(true);
  });

  it("goes back to the command's question when the button's own sentence is cleared", () => {
    openCard(draftWith({ id: "wipe_button", kind: "command", command: "wipe", confirm: "Wipes it all." }, asking));
    const field = screen.getByDisplayValue("Wipes it all.");
    expect(field.getAttribute("placeholder")).toBe("Leave blank to ask what the command asks");
    fireEvent.change(field, { target: { value: "" } });
    expect(action().confirm).toBeUndefined();
    expect(tick().checked).toBe(true);
  });
});
