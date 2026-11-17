import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// A raw serial Send in Live Test opens the port at the line settings the
// driver's own connection uses, so the request carries the draft (its
// default_config holds them) and the panel's Driver Config overrides.

const testDriverCommand = vi.fn();
vi.mock("../../api/restClient", () => ({
  checkConnectionConflict: vi.fn(() => Promise.resolve({ conflicts: [] })),
  dryRunDriverCommand: vi.fn(() => new Promise(() => {})),
  pauseDevice: vi.fn(() => Promise.resolve()),
  resumeDevice: vi.fn(() => Promise.resolve()),
  testDriverCommand: (...args: unknown[]) => testDriverCommand(...args),
}));

import { LiveTestPanel } from "./LiveTestPanel";
import type { DriverDraft } from "../../store/driverBuilderStore.helpers";

function draft(): DriverDraft {
  return {
    id: "acme_widget",
    name: "Acme Widget",
    manufacturer: "Acme",
    category: "utility",
    version: "1.0.0",
    transport: "serial",
    delimiter: "\\r",
    default_config: { port: "/dev/ttyACME0", baudrate: 19200, parity: "E" },
    config_schema: { zone: { type: "string", label: "Zone" } },
    state_variables: {},
    responses: [],
    commands: {},
  } as unknown as DriverDraft;
}

beforeEach(() => {
  testDriverCommand.mockReset();
  testDriverCommand.mockResolvedValue({ sent: "x", received: [], state_changes: {} });
});

describe("Live Test's raw serial Send", () => {
  it("carries the draft and the panel's overrides, so the port opens as the driver's does", async () => {
    const d = draft();
    render(<LiveTestPanel draft={d} />);
    fireEvent.change(screen.getByPlaceholderText("zone"), { target: { value: "2" } });
    fireEvent.change(screen.getByPlaceholderText("%1POWR ?\\r"), {
      target: { value: "PWR?" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: /^Send$/ })[0]);
    await waitFor(() => expect(testDriverCommand).toHaveBeenCalledTimes(1));

    const sent = testDriverCommand.mock.calls[0][1] as Record<string, unknown>;
    expect(sent).toMatchObject({
      transport: "serial",
      port: "/dev/ttyACME0",
      command_string: "PWR?",
      definition: d,
      config_overrides: { zone: "2" },
    });
    expect(sent.command_name).toBeUndefined();
  });
});
