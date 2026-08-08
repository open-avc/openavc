import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Shared, hoisted mock state. `state.project` is mutated by tests to simulate a
// project that hydrates AFTER the editor mounts (the §82.6 re-sync case).
const mocks = vi.hoisted(() => ({
  state: {
    project: {
      devices: [] as Array<Record<string, unknown>>,
      connections: {} as Record<string, unknown>,
    },
  },
  updateDevice: vi.fn(async () => ({})),
  syncDeviceConfig: vi.fn(async () => {}),
  irEmit: vi.fn(async () => ({})),
  irImport: vi.fn(async () => ({ pronto: "0000 1111" })),
}));

vi.mock("../../store/projectStore", () => ({
  useProjectStore: (selector: (s: unknown) => unknown) => selector(mocks.state),
  syncDeviceConfig: mocks.syncDeviceConfig,
}));

vi.mock("../../api/restClient", () => ({
  updateDevice: mocks.updateDevice,
  irEmit: mocks.irEmit,
  irImport: mocks.irImport,
}));

vi.mock("../../api/irLearn", () => ({
  IrLearnSession: class {
    start() {}
    stop() {}
    close() {}
  },
}));

vi.mock("./IrDbSearch", () => ({ IrDbSearch: () => null }));

import { IrCodesEditor } from "./IrCodesEditor";

const DEVICE_ID = "ir1";
const VALID_PRONTO = "0000 006D 0000 0022";

function deviceWith(codes: Record<string, unknown> | undefined) {
  return {
    id: DEVICE_ID,
    name: "Living Room TV",
    config: codes ? { ir_codes: codes } : {},
  };
}

function setProject(devices: Array<Record<string, unknown>>) {
  mocks.state.project = { devices, connections: {} };
}

function renderEditor() {
  return render(
    <IrCodesEditor deviceId={DEVICE_ID} connected={false} onSaved={vi.fn()} />,
  );
}

beforeEach(() => {
  mocks.updateDevice.mockClear();
  mocks.syncDeviceConfig.mockClear();
  setProject([deviceWith(undefined)]);
});

describe("IrCodesEditor — silent-drop on save (§82.6a)", () => {
  it("blocks the save and flags a named row that has no captured code", async () => {
    const user = userEvent.setup();
    renderEditor();

    await user.click(screen.getByRole("button", { name: /add code/i }));
    await user.type(screen.getByPlaceholderText("Power On"), "Volume Up");
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    // The row would be dropped by buildIrCodes — the save must be refused, not
    // silently persisted-minus-the-row while the header claims "Saved".
    expect(mocks.updateDevice).not.toHaveBeenCalled();
    expect(screen.getByText(/needs a captured signal/i)).toBeInTheDocument();
    expect(screen.getByText(/needs a code before saving/i)).toBeInTheDocument();
    expect(screen.queryByText(/^\s*Saved\s*$/)).not.toBeInTheDocument();
  });

  it("saves once the row gets a real code, sending it in the payload", async () => {
    const user = userEvent.setup();
    renderEditor();

    await user.click(screen.getByRole("button", { name: /add code/i }));
    await user.type(screen.getByPlaceholderText("Power On"), "Volume Up");

    // Blocked first…
    await user.click(screen.getByRole("button", { name: /^save$/i }));
    expect(mocks.updateDevice).not.toHaveBeenCalled();

    // …paste a Pronto code into the row, then save succeeds with the code.
    await user.click(screen.getByRole("button", { name: /paste pronto/i }));
    await user.type(
      screen.getByPlaceholderText(/Paste Pronto hex/i),
      VALID_PRONTO,
    );
    await user.click(screen.getByRole("button", { name: /^apply$/i }));
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    expect(mocks.updateDevice).toHaveBeenCalledTimes(1);
    const [id, patch] = mocks.updateDevice.mock.calls[0] as [
      string,
      { config: { ir_codes: Record<string, { pronto: string }> } },
    ];
    expect(id).toBe(DEVICE_ID);
    const codes = patch.config.ir_codes;
    expect(Object.keys(codes)).toHaveLength(1);
    expect(Object.values(codes)[0].pronto).toBe(VALID_PRONTO);
  });
});

describe("IrCodesEditor — pre-hydration re-sync (§82.6b)", () => {
  it("adopts saved codes that arrive after the editor mounted empty", async () => {
    // Mount before the project has this device (empty devices list).
    setProject([]);
    const { rerender } = renderEditor();
    expect(screen.getByText(/no codes yet/i)).toBeInTheDocument();

    // Project hydrates: the device (with a saved code) appears in the store.
    setProject([deviceWith({ power: { label: "Power", pronto: VALID_PRONTO, repeat: 1 } })]);
    rerender(
      <IrCodesEditor deviceId={DEVICE_ID} connected={false} onSaved={vi.fn()} />,
    );

    // The editor must re-sync instead of staying stuck on the empty snapshot.
    expect(await screen.findByDisplayValue("Power")).toBeInTheDocument();
  });

  it("does not clobber in-progress edits when the project hydrates late", async () => {
    const user = userEvent.setup();
    setProject([]);
    const { rerender } = renderEditor();

    // The operator starts a row before hydration lands.
    await user.click(screen.getByRole("button", { name: /add code/i }));
    await user.type(screen.getByPlaceholderText("Power On"), "My Row");

    // Now the project hydrates with a different, saved code.
    setProject([deviceWith({ power: { label: "Power", pronto: VALID_PRONTO, repeat: 1 } })]);
    rerender(
      <IrCodesEditor deviceId={DEVICE_ID} connected={false} onSaved={vi.fn()} />,
    );

    // The dirty edit survives; the late hydration is NOT force-loaded over it.
    expect(screen.getByDisplayValue("My Row")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Power")).not.toBeInTheDocument();
  });
});
