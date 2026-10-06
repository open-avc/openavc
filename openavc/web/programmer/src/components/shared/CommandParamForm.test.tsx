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
    fireEvent.focus(screen.getByPlaceholderText("Preset"));
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

  it("names each field by its label, else its key made readable, in every layout", () => {
    const params = {
      level: { type: "integer", label: "Level" },
      input_id: { type: "string" },
      gain_db: { type: "number", label: "  " },
    } as const;
    for (const layout of ["inline", "stacked", "grid"] as const) {
      const onChange = vi.fn();
      const { unmount } = render(
        <CommandParamForm
          layout={layout}
          params={params}
          values={{ level: "", input_id: "", gain_db: "" }}
          onChange={onChange}
        />,
      );
      expect(screen.getByText("Level")).toBeTruthy();
      expect(screen.getByText("Input ID")).toBeTruthy();
      expect(screen.getByText("Gain (dB)")).toBeTruthy();
      expect(screen.queryByText("level")).toBeNull();
      expect(screen.queryByText("input_id")).toBeNull();
      if (layout !== "grid") {
        fireEvent.change(screen.getByPlaceholderText("Level"), { target: { value: "7" } });
        expect(onChange).toHaveBeenCalledWith("level", "7");
      }
      unmount();
    }
  });

  it("names the field a cascade waits for the way that field is named", () => {
    render(
      <CommandParamForm
        params={{
          block_id: { type: "child_id", child_type: "block" },
          control: { type: "string", options_from: { param: "block_id", source: "child_schema" } },
        }}
        values={{ block_id: "", control: "" }}
        onChange={vi.fn()}
        pickers={{ loadChildren: vi.fn(async () => []) }}
      />,
    );
    expect(screen.getByText("Pick Block ID first to list its controls.")).toBeTruthy();
  });

  it("keeps the key a script sends on the device page's field name", () => {
    render(
      <CommandParamForm
        layout="inline"
        params={{ input_id: { type: "string" } }}
        values={{ input_id: "" }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText("Input ID").getAttribute("title")).toBe("input_id");
  });
});
