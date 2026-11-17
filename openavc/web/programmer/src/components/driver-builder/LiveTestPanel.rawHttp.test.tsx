import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// A raw HTTP request in Live Test goes where the driver's own connection
// goes: HTTPS when the driver turns HTTPS on, whatever the port, with the
// certificate checked unless the driver turns that off.

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
    transport: "http",
    delimiter: "\\r",
    default_config: defaultConfig,
    config_schema: {},
    state_variables: {},
    responses: [],
    commands: {},
  } as unknown as DriverDraft;
}

async function sendRaw(): Promise<Record<string, unknown>> {
  fireEvent.change(screen.getByPlaceholderText("192.168.1.100"), {
    target: { value: "192.0.2.50" },
  });
  fireEvent.change(screen.getByPlaceholderText("GET /api/status"), {
    target: { value: "GET /api/status" },
  });
  fireEvent.click(screen.getAllByRole("button", { name: /^Send$/ })[0]);
  await waitFor(() => expect(testDriverCommand).toHaveBeenCalledTimes(1));
  return testDriverCommand.mock.calls[0][1] as Record<string, unknown>;
}

beforeEach(() => {
  testDriverCommand.mockReset();
  testDriverCommand.mockResolvedValue({ sent: "x", received: [], state_changes: {} });
});

describe("Live Test's raw HTTP request", () => {
  it("is HTTPS on the driver's own port when the driver turns HTTPS on", async () => {
    render(<LiveTestPanel draft={draft({ port: 8443, ssl: true, verify_ssl: false })} />);
    expect(screen.getByRole("checkbox", { name: /Verify TLS certificate/ })).not.toBeChecked();
    const sent = await sendRaw();
    expect(sent).toMatchObject({ port: 8443, ssl: true, verify_ssl: false });
  });

  it("goes to 443 when the driver turns HTTPS on and names no port", async () => {
    render(<LiveTestPanel draft={draft({ ssl: true })} />);
    expect(screen.getByDisplayValue("443")).toBeInTheDocument();
    const sent = await sendRaw();
    expect(sent).toMatchObject({ port: 443, ssl: true });
  });

  it("goes to 80 when the driver names no port and leaves HTTPS off", async () => {
    render(<LiveTestPanel draft={draft({})} />);
    expect(screen.getByDisplayValue("80")).toBeInTheDocument();
    const sent = await sendRaw();
    expect(sent).toMatchObject({ port: 80, ssl: false });
  });

  it("is plain HTTP with the certificate checked when the driver sets neither", async () => {
    render(<LiveTestPanel draft={draft({ port: 443 })} />);
    expect(screen.getByRole("checkbox", { name: /Verify TLS certificate/ })).toBeChecked();
    const sent = await sendRaw();
    expect(sent).toMatchObject({ port: 443, ssl: false, verify_ssl: true });
  });
});
