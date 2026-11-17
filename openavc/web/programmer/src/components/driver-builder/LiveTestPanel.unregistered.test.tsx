import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// A reply naming a channel or zone beyond the test's roster is listed under
// the result, as a reply no rule matched is, in the server's own sentence:
// the author sees the reply arrive and nothing change, and this says why.

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

const DRAFT = {
  id: "acme_matrix",
  name: "Acme Matrix",
  manufacturer: "Acme",
  category: "switcher",
  version: "1.0.0",
  transport: "tcp",
  delimiter: "\\r",
  default_config: { port: 4000 },
  config_schema: {},
  state_variables: {},
  responses: [],
  commands: { query: { label: "Query Levels", send: "Q\\r", params: {} } },
} as unknown as DriverDraft;

const SENTENCE = "Status for a channel or zone that is not set up on this device: channel 9.";

beforeEach(() => {
  testDriverCommand.mockReset();
  testDriverCommand.mockResolvedValue({
    success: true,
    sent: "Q\\r",
    received: ["CH9 5"],
    state_changes: {},
    error: null,
    contract_errors: [],
    unmatched: [],
    unregistered: [SENTENCE],
  });
});

describe("Live Test lists a channel beyond the test's roster", () => {
  it("shows the server's sentence under the result", async () => {
    render(<LiveTestPanel draft={DRAFT} />);
    fireEvent.change(screen.getByPlaceholderText("192.168.1.100"), {
      target: { value: "127.0.0.1" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: /^Send$/ })[0]);
    expect(await screen.findByText(SENTENCE)).toBeTruthy();
  });

  it("shows nothing for it when the server sends no list (a raw send)", async () => {
    testDriverCommand.mockResolvedValue({ sent: "x", received: [], state_changes: {} });
    render(<LiveTestPanel draft={DRAFT} />);
    fireEvent.change(screen.getByPlaceholderText("192.168.1.100"), {
      target: { value: "127.0.0.1" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: /^Send$/ })[0]);
    expect(await screen.findByText("Query Levels", { selector: "span" })).toBeTruthy();
    expect(screen.queryByText(/not set up on this device/)).toBeNull();
  });
});
