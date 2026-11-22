import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import type { ChildEntityEntry, DriverParamDef } from "../../api/types";
import { useConnectionStore } from "../../store/connectionStore";

// A child picker on a project device reads its children from the server,
// which settles what each one is called. Devices report their names a moment
// after the list is fetched, and an integrator renames them, so the picker
// has to follow what live state says about the roster while it stays open.

const listChildEntitiesByType = vi.fn();
vi.mock("../../api/restClient", () => ({
  listChildEntitiesByType: (...args: unknown[]) => listChildEntitiesByType(...args),
}));

import { ParamInput } from "./ParamInput";

const DEF: Partial<DriverParamDef> = { type: "child_id", child_type: "decoder", label: "Decoder" };
const PREFIX = "device.acme1.decoder.";

let roster: ChildEntityEntry[] = [];

function decoder(id: number, displayName: string): ChildEntityEntry {
  const padded = String(id).padStart(3, "0");
  return {
    local_id: id, local_id_padded: padded, label: "", display_name: displayName,
    config: {}, registered: true, state: { name: displayName },
  };
}

function answer() {
  return Promise.resolve({
    device_id: "acme1",
    child_type: "decoder",
    schema: { id_format: { kind: "integer" }, state_variables: {}, label_field: "name" },
    children: roster.map((c) => ({ ...c })),
  });
}

function Picker() {
  const [value, setValue] = useState("1");
  return <ParamInput def={DEF} value={value} onChange={setValue} deviceId="acme1" />;
}

function optionTexts(): string[] {
  return Array.from(screen.getByRole("combobox").querySelectorAll("option")).map(
    (o) => o.textContent ?? "",
  );
}

function setLive(changes: Record<string, unknown>) {
  act(() => {
    useConnectionStore.setState((s) => ({ liveState: { ...s.liveState, ...changes } }));
  });
}

beforeEach(() => {
  listChildEntitiesByType.mockImplementation(answer);
  useConnectionStore.setState({ liveState: {} });
});

afterEach(() => {
  listChildEntitiesByType.mockReset();
  useConnectionStore.setState({ liveState: {} });
});

describe("a child picker that stays open while the device names its children", () => {
  it("shows a name the device reports after the picker opened", async () => {
    roster = [decoder(1, "")];
    setLive({ [`${PREFIX}001.online`]: true, [`${PREFIX}001.name`]: "" });
    render(<Picker />);
    await waitFor(() => expect(optionTexts()).toContain("decoder 1"));

    roster = [decoder(1, "Main Display")];
    setLive({ [`${PREFIX}001.name`]: "Main Display" });

    await waitFor(() => expect(optionTexts()).toContain("Main Display (1)"), { timeout: 2000 });
    expect(screen.getByRole("combobox")).toHaveValue("1");
  });

  it("follows a rename on the device, keeping the chosen child", async () => {
    roster = [decoder(1, "Decoder 001"), decoder(2, "Decoder 002")];
    setLive({
      [`${PREFIX}001.online`]: true, [`${PREFIX}001.name`]: "Decoder 001",
      [`${PREFIX}002.online`]: true, [`${PREFIX}002.name`]: "Decoder 002",
    });
    render(<Picker />);
    await waitFor(() => expect(optionTexts()).toContain("Decoder 001 (1)"));

    roster = [decoder(1, "Main Display"), decoder(2, "Decoder 002")];
    setLive({ [`${PREFIX}001.name`]: "Main Display" });

    await waitFor(() => expect(optionTexts()).toContain("Main Display (1)"), { timeout: 2000 });
    expect(optionTexts()).not.toContain("Decoder 001 (1)");
    expect(screen.getByRole("combobox")).toHaveValue("1");
  });

  it("follows a label saved for the child, which the server mirrors into its label", async () => {
    roster = [decoder(1, "Decoder 001")];
    setLive({ [`${PREFIX}001.online`]: true, [`${PREFIX}001.label`]: "Decoder 1" });
    render(<Picker />);
    await waitFor(() => expect(optionTexts()).toContain("Decoder 001 (1)"));

    roster = [{ ...decoder(1, "Lectern Screen"), label: "Lectern Screen" }];
    setLive({ [`${PREFIX}001.label`]: "Lectern Screen" });

    await waitFor(() => expect(optionTexts()).toContain("Lectern Screen (1)"), { timeout: 2000 });
  });

  it("lists a child that registers after the picker opened", async () => {
    roster = [decoder(1, "Decoder 001")];
    setLive({ [`${PREFIX}001.online`]: true, [`${PREFIX}001.name`]: "Decoder 001" });
    render(<Picker />);
    await waitFor(() => expect(optionTexts()).toContain("Decoder 001 (1)"));

    roster = [decoder(1, "Decoder 001"), decoder(2, "Ceiling Display")];
    setLive({ [`${PREFIX}002.online`]: true, [`${PREFIX}002.name`]: "Ceiling Display" });

    await waitFor(() => expect(optionTexts()).toContain("Ceiling Display (2)"), { timeout: 2000 });
  });

  it("does not ask again for a reading that is not a name or the roster", async () => {
    roster = [decoder(1, "Decoder 001")];
    setLive({
      [`${PREFIX}001.online`]: true, [`${PREFIX}001.name`]: "Decoder 001",
      [`${PREFIX}001.source`]: 1,
    });
    render(<Picker />);
    await waitFor(() => expect(optionTexts()).toContain("Decoder 001 (1)"));
    const asked = listChildEntitiesByType.mock.calls.length;

    setLive({ [`${PREFIX}001.source`]: 2, "device.other.decoder.001.name": "Elsewhere" });
    await new Promise((r) => setTimeout(r, 700));

    expect(listChildEntitiesByType.mock.calls.length).toBe(asked);
  });
});
