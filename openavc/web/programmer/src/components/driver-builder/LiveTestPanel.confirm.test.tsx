import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// A command that declares `confirm` asks before Live Test sends it to the
// device: the driver's own sentence, or a plain question for `confirm: true`.
// Cancel sends nothing; Send in the question sends it. A command without one
// sends at once.

const testDriverCommand = vi.fn();
vi.mock("../../api/restClient", () => ({
  checkConnectionConflict: vi.fn(() => Promise.resolve({ conflicts: [] })),
  dryRunDriverCommand: vi.fn(() => new Promise(() => {})),
  pauseDevice: vi.fn(() => Promise.resolve()),
  resumeDevice: vi.fn(() => Promise.resolve()),
  testDriverCommand: (...args: unknown[]) => testDriverCommand(...args),
}));

import { LiveTestPanel } from "./LiveTestPanel";
import { CommandConfirmField } from "./CommandBuilder";
import { commandConfirmMessage } from "../shared/commandParams";
import type { DriverDefinition } from "../../api/types";

const DRAFT = {
  id: "acme_widget",
  name: "Acme Widget",
  manufacturer: "Acme",
  category: "utility",
  version: "1.0.0",
  transport: "tcp",
  delimiter: "\\r",
  default_config: { port: 4000 },
  config_schema: {},
  state_variables: {},
  responses: [],
  commands: {
    factory_reset: {
      label: "Factory Reset",
      send: "RESET ALL\\r",
      params: {},
      confirm: "Erases every preset and returns the unit to DHCP.",
    },
    power_on: { label: "Power On", send: "PWR 1\\r", params: {} },
  },
} as unknown as DriverDefinition;

function sendButton(): HTMLElement {
  return screen.getAllByRole("button", { name: /^Send$/ })[0];
}

beforeEach(() => {
  testDriverCommand.mockReset();
  testDriverCommand.mockResolvedValue({ sent: "x", received: [], state_changes: {} });
});

describe("Live Test asks before a command that declares confirm", () => {
  it("asks with the driver's sentence, and Cancel sends nothing", async () => {
    render(<LiveTestPanel draft={DRAFT} />);
    fireEvent.change(screen.getByPlaceholderText("192.168.1.100"), {
      target: { value: "127.0.0.1" },
    });
    fireEvent.click(sendButton());
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).toContain("Erases every preset and returns the unit to DHCP.");
    expect(testDriverCommand).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(testDriverCommand).not.toHaveBeenCalled();
  });

  it("sends once the person says Send", async () => {
    render(<LiveTestPanel draft={DRAFT} />);
    fireEvent.change(screen.getByPlaceholderText("192.168.1.100"), {
      target: { value: "127.0.0.1" },
    });
    fireEvent.click(sendButton());
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(
      Array.from(dialog.querySelectorAll("button")).find((b) => b.textContent === "Send")!,
    );
    await waitFor(() => expect(testDriverCommand).toHaveBeenCalledTimes(1));
    expect(testDriverCommand.mock.calls[0][1].command_name).toBe("factory_reset");
  });

  it("sends a command without confirm at once", async () => {
    const draft = { ...DRAFT, commands: { power_on: DRAFT.commands.power_on } };
    render(<LiveTestPanel draft={draft as DriverDefinition} />);
    fireEvent.change(screen.getByPlaceholderText("192.168.1.100"), {
      target: { value: "127.0.0.1" },
    });
    fireEvent.click(sendButton());
    await waitFor(() => expect(testDriverCommand).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
});

describe("commandConfirmMessage", () => {
  it("is the driver's sentence, a plain question for true, or nothing", () => {
    expect(commandConfirmMessage({ confirm: "Erases it." }, "Factory Reset")).toBe("Erases it.");
    expect(commandConfirmMessage({ confirm: true }, "Factory Reset")).toBe("Send Factory Reset?");
    expect(commandConfirmMessage({ confirm: false }, "Factory Reset")).toBeNull();
    expect(commandConfirmMessage({ confirm: "  " }, "Factory Reset")).toBeNull();
    expect(commandConfirmMessage({}, "Factory Reset")).toBeNull();
    expect(commandConfirmMessage(undefined, "Factory Reset")).toBeNull();
  });
});

describe("the Driver Builder's Ask before sending field", () => {
  it("writes true, then the sentence, and removes it when unticked", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <CommandConfirmField confirm={undefined} labelStyle={{}} onChange={onChange} />,
    );
    fireEvent.click(screen.getByRole("checkbox", { name: /Ask before sending/ }));
    expect(onChange).toHaveBeenLastCalledWith(true);

    rerender(<CommandConfirmField confirm={true} labelStyle={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText("Leave blank for the generic prompt"), {
      target: { value: "Erases every preset." },
    });
    expect(onChange).toHaveBeenLastCalledWith("Erases every preset.");

    rerender(
      <CommandConfirmField confirm="Erases every preset." labelStyle={{}} onChange={onChange} />,
    );
    expect(screen.getByRole("checkbox", { name: /Ask before sending/ })).toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: /Ask before sending/ }));
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });
});
