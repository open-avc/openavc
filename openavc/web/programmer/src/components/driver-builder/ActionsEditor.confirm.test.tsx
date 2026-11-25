import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";

// A button's "Ask before running" tick reads the action's confirm the way the
// server does: a blank sentence asks nothing, and a sentence typed starting
// with a space keeps the field open.

import type { DriverActionDef, DriverDefinition } from "../../api/types";
import { ActionsEditor } from "./ActionsEditor";

function draftWith(action: DriverActionDef): DriverDefinition {
  return {
    id: "acme_widget",
    name: "Acme Widget",
    manufacturer: "Acme",
    category: "utility",
    transport: "tcp",
    commands: { wipe: { label: "Wipe", send: "WIPE" } },
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
