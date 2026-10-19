import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * Product hardware's Wait for completion is set on its card, the one place a
 * programmer can change anything about it. The connection comes from the
 * product, so a save sends the model back unchanged beside the wait.
 */

const mocks = vi.hoisted(() => ({
  updateDevice: vi.fn(async () => ({ status: "updated", device_id: "local" })),
  load: vi.fn(async () => true),
  config: { integration: "acme_amplifier" } as Record<string, unknown>,
}));

vi.mock("../../api/restClient", () => ({ updateDevice: mocks.updateDevice }));

vi.mock("../../store/projectStore", () => {
  const state = () => ({
    project: { devices: [{ id: "local", config: mocks.config }] },
    load: mocks.load,
  });
  const useProjectStore = (selector: (s: unknown) => unknown) => selector(state());
  useProjectStore.getState = state;
  return { useProjectStore };
});

import { InstalledEquipment } from "./InstalledEquipment";

const WAIT = { default: 5, max: 600 };

function renderCard() {
  render(<InstalledEquipment deviceId="local" name="Acme Amplifier" completionWait={WAIT} />);
  return screen.getByRole("spinbutton", { name: "Wait for completion (seconds)" });
}

describe("Product hardware card, Wait for completion", () => {
  beforeEach(() => {
    mocks.updateDevice.mockClear();
    mocks.config = { integration: "acme_amplifier" };
  });

  it("shows the default and saves a wait with the model", async () => {
    const input = renderCard();
    expect(input).toHaveAttribute("placeholder", "5");
    expect(screen.getByText(/Leave it empty for 5 seconds/)).toBeInTheDocument();
    await userEvent.type(input, "12.5");
    await userEvent.click(screen.getByRole("button", { name: "Save wait" }));
    expect(mocks.updateDevice).toHaveBeenCalledWith("local", {
      config: { integration: "acme_amplifier", completion_wait: 12.5 },
    });
    expect(await screen.findByText("Wait saved.")).toBeInTheDocument();
  });

  it("opens on the saved wait, and an emptied field goes back to the default", async () => {
    mocks.config = { integration: "acme_amplifier", completion_wait: 30 };
    const input = renderCard();
    expect(input).toHaveValue(30);
    await userEvent.clear(input);
    await userEvent.click(screen.getByRole("button", { name: "Save wait" }));
    expect(mocks.updateDevice).toHaveBeenCalledWith("local", {
      config: { integration: "acme_amplifier" },
    });
  });

  it("refuses a wait past the limit without saving", async () => {
    const input = renderCard();
    await userEvent.type(input, "700");
    await userEvent.click(screen.getByRole("button", { name: "Save wait" }));
    expect(mocks.updateDevice).not.toHaveBeenCalled();
    expect(input).toBeInvalid();
  });
});
