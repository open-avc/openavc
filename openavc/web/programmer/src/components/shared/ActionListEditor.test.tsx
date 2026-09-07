import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ActionTestButton } from "./ActionListEditor";
import { ApiError } from "../../api/errors";

/**
 * The Test button's whole job is explaining a refusal to somebody who does not
 * read JSON. The server already sends the sentence they need; this pins that
 * the button shows that sentence and not the transport envelope around it.
 */

vi.mock("../../api/restClient", () => ({
  sendCommand: vi.fn(),
  executeMacro: vi.fn(),
}));

vi.mock("../../store/toastStore", () => ({
  showError: vi.fn(),
  showSuccess: vi.fn(),
}));

import * as api from "../../api/restClient";
import { showError, showSuccess } from "../../store/toastStore";

const action = {
  action: "device.command",
  device: "dsp1",
  command: "set_fader",
  params: { level: 5 },
};

describe("the Test button on an action", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the server's sentence when the device refuses the command", async () => {
    // Exactly what POST /api/devices/dsp1/command answers when a required
    // param is missing.
    vi.mocked(api.sendCommand).mockRejectedValue(
      new ApiError(400, '{"detail":"\'set_fader\': \'channel\' is required"}'),
    );

    render(<ActionTestButton action={action} />);
    await userEvent.click(screen.getByRole("button", { name: /Test/ }));

    expect(showError).toHaveBeenCalledWith(
      "Test failed: 'set_fader': 'channel' is required",
    );
    // The envelope is the part an integrator cannot act on.
    const shown = vi.mocked(showError).mock.calls[0][0];
    expect(shown).not.toContain("ApiError");
    expect(shown).not.toContain("API 400");
    expect(shown).not.toContain("detail");
  });

  it("still says something useful when the failure is not an API error", async () => {
    vi.mocked(api.sendCommand).mockRejectedValue(new TypeError("Failed to fetch"));

    render(<ActionTestButton action={action} />);
    await userEvent.click(screen.getByRole("button", { name: /Test/ }));

    expect(showError).toHaveBeenCalledWith("Test failed: Failed to fetch");
  });

  it("reports the command as sent when the device takes it", async () => {
    vi.mocked(api.sendCommand).mockResolvedValue({ success: true } as never);

    render(<ActionTestButton action={action} />);
    await userEvent.click(screen.getByRole("button", { name: /Test/ }));

    expect(showSuccess).toHaveBeenCalledWith("Command sent");
    expect(showError).not.toHaveBeenCalled();
  });
});
