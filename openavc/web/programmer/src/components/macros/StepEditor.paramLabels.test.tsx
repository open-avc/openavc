import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

/**
 * A device command step names each field the way every other command form
 * does: its label, else its key made readable. The single-device editor once
 * showed the raw key even when the driver declared a label, and the group
 * editor showed nothing for a blank label.
 */

const mocks = vi.hoisted(() => ({
  projectState: {
    project: {
      devices: [{ id: "amp_1", name: "Amp", driver: "acme_amp" }],
      device_groups: [{ id: "amps", name: "Amps", device_ids: ["amp_1"] }],
    },
  },
  getDevice: vi.fn(async () => ({
    id: "amp_1",
    commands: {
      set_gain: {
        label: "Set Gain",
        params: {
          zone: { type: "integer", label: "Zone", required: true },
          gain_db: { type: "number", label: "" },
        },
      },
    },
  })),
}));

vi.mock("../../api/restClient", () => ({
  getDevice: mocks.getDevice,
  listPlugins: vi.fn(async () => []),
}));

vi.mock("../../store/projectStore", () => {
  const state = mocks.projectState;
  return {
    useProjectStore: Object.assign(
      (selector: (s: unknown) => unknown) => selector(state),
      { getState: () => state },
    ),
  };
});

vi.mock("../../store/connectionStore", () => {
  const state = { liveState: {}, connected: true };
  return {
    useConnectionStore: Object.assign(
      (selector: (s: unknown) => unknown) => selector(state),
      { getState: () => state },
    ),
  };
});

import { StepEditor } from "./StepEditor";
import type { MacroStep } from "../../api/types";

function renderStep(step: MacroStep) {
  render(<StepEditor step={step} macros={[]} currentMacroId="m1" onChange={vi.fn()} />);
}

describe("a device command step's field names", () => {
  it("uses the label, else the key made readable, for one device", async () => {
    renderStep({ action: "device.command", device: "amp_1", command: "set_gain" });
    expect(await screen.findByText("Zone")).toBeTruthy();
    expect(screen.getByText("Gain (dB)")).toBeTruthy();
    expect(screen.queryByText("zone")).toBeNull();
    expect(screen.queryByText("gain_db")).toBeNull();
  });

  it("uses the label, else the key made readable, for a group", async () => {
    renderStep({ action: "group.command", group: "amps", command: "set_gain" });
    expect(await screen.findByText("Zone")).toBeTruthy();
    expect(screen.getByText("Gain (dB)")).toBeTruthy();
  });
});
