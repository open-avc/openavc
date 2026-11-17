import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// An OSC reply mapping reads the message argument its `arg` names, the first
// one when it names none; a `group` on it is not read. The editor names the
// argument the driver reads, and a mapping added after it takes the next one.

import { ResponseBuilder } from "./ResponseBuilder";
import type { DriverResponseDef } from "../../api/types";
import type { DriverDraft } from "../../store/driverBuilderStore.helpers";

function draft(mappings: unknown[]): DriverDraft {
  return {
    id: "acme_mixer",
    name: "Acme Mixer",
    transport: "osc",
    state_variables: {
      level: { type: "number", label: "Level" },
      mute: { type: "boolean", label: "Mute" },
    },
    commands: {},
    responses: [{ address: "/ch/1/level", mappings }],
    polling: {},
  } as unknown as DriverDraft;
}

describe("An OSC reply mapping's argument", () => {
  it("is the first one when the mapping names none", () => {
    render(<ResponseBuilder draft={draft([{ state: "level" }])} onUpdate={vi.fn()} />);
    expect(screen.queryByText(/undefined/)).toBeNull();
    expect(screen.getByText("Arg 0 →")).toBeTruthy();
  });

  it("is never read from a capture group", () => {
    render(
      <ResponseBuilder draft={draft([{ group: 2, state: "level" }])} onUpdate={vi.fn()} />,
    );
    expect(screen.getByText("Arg 0 →")).toBeTruthy();
    expect(screen.queryByText("Arg 2 →")).toBeNull();
  });

  it("of an added mapping is the one after the highest read", () => {
    const onUpdate = vi.fn();
    render(
      <ResponseBuilder
        draft={draft([{ state: "level" }, { group: 3, state: "mute" }])}
        onUpdate={onUpdate}
      />,
    );
    fireEvent.click(screen.getByText("+ Add Mapping"));
    const responses = onUpdate.mock.calls.at(-1)?.[0].responses as DriverResponseDef[];
    expect((responses[0].mappings ?? []).at(-1)?.arg).toBe(1);
  });
});
