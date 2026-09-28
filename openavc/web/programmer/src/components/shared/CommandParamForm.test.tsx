import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CommandParamForm } from "./CommandParamForm";
import { coerceCommandParams, commandParamsBlocked, seedCommandParams } from "./commandParams";
import type { ParamPickers } from "./ParamInput";
import type { ChildEntityEntry } from "../../api/types";

const PARAMS = {
  zone: { type: "child_id", child_type: "zone", required: true, label: "Zone" },
  preset: { type: "string", options_state: "presets", label: "Preset" },
  level: { type: "integer", min: 0, max: 100, label: "Level", default: 20 },
  mute: { type: "boolean", label: "Mute" },
  input: { type: "enum", values: ["hdmi1", "hdmi2"], label: "Input", required: true },
} as const;

function zone(local_id: number, display_name: string): ChildEntityEntry {
  return {
    local_id, local_id_padded: String(local_id), label: "", display_name, config: {},
    registered: true, state: {},
  };
}

describe("what a command form starts with, sends, and refuses", () => {
  it("starts at the driver's defaults and nothing else", () => {
    expect(seedCommandParams(PARAMS)).toEqual({
      zone: "", preset: "", level: "20", mute: "", input: "",
    });
  });

  it("starts the device page's way when asked: first option, flags off", () => {
    expect(seedCommandParams(PARAMS, { firstOption: true })).toEqual({
      zone: "", preset: "", level: "", mute: "false", input: "hdmi1",
    });
  });

  it("sends each value as its declared type and leaves the empty ones out", () => {
    expect(coerceCommandParams(PARAMS, {
      zone: "2", preset: "", level: "40", mute: "true", input: "hdmi2", extra: "x",
    })).toEqual({ zone: "2", level: 40, mute: true, input: "hdmi2", extra: "x" });
    expect(coerceCommandParams(PARAMS, { level: "loud" })).toEqual({});
  });

  it("will not send while a value is wrong or a required one is empty", () => {
    const ok = { zone: "2", input: "hdmi1", level: "40" };
    expect(commandParamsBlocked(PARAMS, ok)).toBe(false);
    expect(commandParamsBlocked(PARAMS, { ...ok, level: "150" })).toBe(true);
    expect(commandParamsBlocked(PARAMS, { ...ok, input: "" })).toBe(true);
  });
});

describe("a command form's pickers", () => {
  it("reads children and a state-fed list from wherever it is told", async () => {
    const pickers: ParamPickers = {
      loadChildren: vi.fn(async () => [zone(2, "Lobby")]),
      stateValue: (key) => (key === "presets" ? '["Morning", "Evening"]' : undefined),
    };
    render(
      <CommandParamForm
        params={PARAMS}
        values={{ zone: "", preset: "", level: "20", mute: "", input: "" }}
        onChange={vi.fn()}
        pickers={pickers}
      />,
    );
    // The child picker lists what the source registered, by name.
    await waitFor(() => expect(screen.getByRole("option", { name: "Lobby (2)" })).toBeTruthy());
    expect(pickers.loadChildren).toHaveBeenCalledWith("zone");
    // The preset field offers the list the device published.
    fireEvent.focus(screen.getByPlaceholderText("preset"));
    expect([...document.querySelectorAll("li")].map((li) => li.textContent)).toEqual([
      "Morning", "Evening",
    ]);
  });

  it("says a flag nobody set holds nothing", () => {
    render(
      <CommandParamForm
        params={{ mute: { type: "boolean", label: "Mute" }, on: { type: "boolean", required: true } }}
        values={{ mute: "", on: "" }}
        onChange={vi.fn()}
      />,
    );
    const [optional, required] = screen.getAllByRole("combobox") as HTMLSelectElement[];
    expect(optional.options[optional.selectedIndex].text).toBe("(none)");
    expect(required.options[required.selectedIndex].text).toBe("Select...");
    expect(required.options[required.selectedIndex].disabled).toBe(true);
  });

  it("labels each field the device page's way when laid out inline", () => {
    const onChange = vi.fn();
    render(
      <CommandParamForm
        layout="inline"
        params={{ level: { type: "integer", label: "Level" } }}
        values={{ level: "" }}
        onChange={onChange}
      />,
    );
    expect(screen.getByText("level")).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText("level"), { target: { value: "7" } });
    expect(onChange).toHaveBeenCalledWith("level", "7");
  });
});
