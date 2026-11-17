import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// A raw TCP Send in Live Test talks TLS when the driver's own connection does
// (`ssl: true`), with the same certificate opt-out a raw HTTPS request has.

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

function draft(defaultConfig: Record<string, unknown>): DriverDraft {
  return {
    id: "acme_widget",
    name: "Acme Widget",
    manufacturer: "Acme",
    category: "utility",
    version: "1.0.0",
    transport: "tcp",
    delimiter: "\\r",
    default_config: { port: 4352, ...defaultConfig },
    config_schema: {},
    state_variables: {},
    responses: [],
    commands: {},
  } as unknown as DriverDraft;
}

beforeEach(() => {
  testDriverCommand.mockReset();
  testDriverCommand.mockResolvedValue({ sent: "x", received: [], state_changes: {} });
});

describe("Live Test's raw TCP Send", () => {
  it("is TLS, with the certificate opt-out, when the driver turns ssl on", async () => {
    render(<LiveTestPanel draft={draft({ ssl: true, verify_ssl: false })} />);
    expect(screen.getByRole("checkbox", { name: /Verify TLS certificate/ })).not.toBeChecked();
    fireEvent.change(screen.getByPlaceholderText("192.168.1.100"), {
      target: { value: "192.0.2.50" },
    });
    fireEvent.change(screen.getByPlaceholderText("%1POWR ?\\r"), {
      target: { value: "%1POWR ?" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: /^Send$/ })[0]);
    await waitFor(() => expect(testDriverCommand).toHaveBeenCalledTimes(1));
    expect(testDriverCommand.mock.calls[0][1]).toMatchObject({
      transport: "tcp",
      port: 4352,
      ssl: true,
      verify_ssl: false,
    });
  });

  it("offers no certificate box for plain TCP", () => {
    render(<LiveTestPanel draft={draft({})} />);
    expect(screen.queryByRole("checkbox", { name: /Verify TLS certificate/ })).toBeNull();
  });
});
