import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// What shipped broken: the Test harness's enum picker displayed a value the
// form did not hold. A native <select> always shows SOME option, and a
// required enum was deliberately given no empty one — so a parameter that had
// never been chosen ("" — every required enum declaring no default) matched
// nothing and the browser fell back to options[0]. The control read "Mic",
// specific and plausible, while the wire preview directly beneath it said
// 'source' is required. The harness disagreed with itself, on the one screen
// whose whole job is explaining why a command will not send.
//
// It was never the falsy-zero bug: the enum below starts at wire value "0" on
// purpose, and a picker whose first option is "1" behaved identically.
//
// The harness now draws its parameters with the shared CommandParamForm, the
// same picker as every other command form. Its trigger reads the placeholder
// whenever the value matches no option, so the guarantee is the picker's own;
// these tests hold it to it here.

import { CommandPreview, seedParamValues } from "./LiveTestPanel";
import type { DriverCommandDef } from "../../api/types";

/** A required enum with NO default, whose first option's wire value is "0" —
 *  the exact shape that reproduced it. */
const SET_SOURCE: DriverCommandDef = {
  label: "Set Input Source",
  params: {
    channel: { type: "integer", required: true, label: "Input" },
    source: {
      type: "enum",
      required: true,
      label: "Source",
      values: [
        { value: "0", label: "Mic" },
        { value: "1", label: "Line" },
      ],
    },
  },
} as DriverCommandDef;

/** The same enum, not required — where "(none)" is a real choice. */
const OPTIONAL_SOURCE: DriverCommandDef = {
  label: "Set Input Source",
  params: {
    source: {
      type: "enum",
      required: false,
      label: "Source",
      values: [
        { value: "0", label: "Mic" },
        { value: "1", label: "Line" },
      ],
    },
  },
} as DriverCommandDef;

function renderForm(
  command: DriverCommandDef,
  paramValues: Record<string, string>,
  previewError: string | null = null,
) {
  return render(
    <CommandPreview
      command={command}
      paramValues={paramValues}
      shapeMismatch={null}
      preview={null}
      previewError={previewError}
      onParamChange={vi.fn()}
    />,
  );
}

/** The Source picker's trigger: the one button reading a Source value. */
function sourceTrigger(text: RegExp): HTMLElement {
  return screen.getByRole("button", { name: text });
}

/** Open the Source picker and read its rows. */
function sourceRows(trigger: HTMLElement): string[] {
  fireEvent.click(trigger);
  return screen.getAllByRole("option").map((o) => o.textContent ?? "");
}

describe("the Test harness's enum picker", () => {
  it("does not display a real option for a required enum nobody has chosen", () => {
    // The finding, stated as an assertion: the control must not read "Mic".
    renderForm(SET_SOURCE, { channel: "1", source: "" });
    expect(sourceTrigger(/Select\.\.\./)).toBeTruthy();
    expect(screen.queryByText("Mic")).toBeNull();
  });

  it("cannot contradict the refusal printed underneath it", () => {
    // The whole defect in one assertion: with the parameter unset, the wire
    // preview says so, and the picker must not simultaneously show a value.
    renderForm(SET_SOURCE, { channel: "1", source: "" }, "'set_input_source': 'source' is required");
    expect(screen.getByText(/'source' is required/)).toBeTruthy();
    expect(sourceTrigger(/Select\.\.\./)).toBeTruthy();
  });

  it("still offers every real option, and nothing that empties a required one", () => {
    // A placeholder that swallowed an option, or a row that could be chosen
    // as a way to empty a required param, would be a different bug.
    renderForm(SET_SOURCE, { channel: "1", source: "" });
    expect(sourceRows(sourceTrigger(/Select\.\.\./))).toEqual(["Mic0", "Line1"]);
  });

  it("shows the value once one is chosen", () => {
    // Including wire value "0", which is the falsy one and must still count as
    // chosen — a placeholder that reappeared here would be the zero bug.
    renderForm(SET_SOURCE, { channel: "1", source: "0" });
    expect(sourceTrigger(/Mic/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Select\.\.\./ })).toBeNull();
  });

  it("leaves an optional enum its (none) row, which is a real choice", () => {
    // "(none)" means send nothing and that is valid; the placeholder means
    // you have not chosen yet. They must not be confused for one another.
    renderForm(OPTIONAL_SOURCE, { source: "" });
    const rows = sourceRows(sourceTrigger(/\(none\)/));
    expect(rows[0]).toBe("(none)");
    expect(rows).not.toContain("Select...");
  });
});

describe("what the form starts with", () => {
  it("leaves a required enum with no default EMPTY rather than picking for you", () => {
    // The rejected alternative fix. Seeding options[0] would make the picker
    // and the preview agree — by inventing a choice, on a harness that opens a
    // socket to real hardware and sends. Refusing is the correct disagreement.
    expect(seedParamValues(SET_SOURCE)).toEqual({ channel: "", source: "" });
  });

  it("still starts a param at its declared default", () => {
    const withDefault = {
      label: "Set Mode",
      params: {
        mode: {
          type: "enum",
          required: true,
          label: "Mode",
          default: "1",
          values: [
            { value: "0", label: "Mic" },
            { value: "1", label: "Line" },
          ],
        },
      },
    } as unknown as DriverCommandDef;
    expect(seedParamValues(withDefault)).toEqual({ mode: "1" });
  });

  it("a defaulted enum therefore shows its default, not the placeholder", () => {
    renderForm(SET_SOURCE, { channel: "1", source: "1" });
    expect(sourceTrigger(/Line/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Select\.\.\./ })).toBeNull();
  });
});
