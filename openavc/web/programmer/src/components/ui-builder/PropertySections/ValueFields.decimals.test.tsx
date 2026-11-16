import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// The project takes a control's decimals from 0 to 20 and refuses the whole
// save over one past that. The Shown decimals box brings what was typed
// inside the same range as it commits, so a save never carries one, and
// leaves a count already inside it alone.

import type { UIElement } from "../../../api/types";
import { ShownDecimalsRow } from "./ValueFields";

function renderBox(display_decimals?: number) {
  const onChange = vi.fn();
  const element = { id: "level", type: "slider", display_decimals } as UIElement;
  render(<ShownDecimalsRow element={element} onChange={onChange} placeholder="1" />);
  const box = screen.getByPlaceholderText("1") as HTMLInputElement;
  return { box, onChange };
}

function typeAndLeave(box: HTMLInputElement, text: string) {
  fireEvent.focus(box);
  fireEvent.change(box, { target: { value: text } });
  fireEvent.blur(box);
}

describe("Shown decimals", () => {
  it("brings a count past 20 down to 20", () => {
    const { box, onChange } = renderBox();
    typeAndLeave(box, "50");
    expect(onChange).toHaveBeenLastCalledWith({ display_decimals: 20 });
  });

  it("takes a count the old box's arrows stopped short of", () => {
    const { box, onChange } = renderBox();
    typeAndLeave(box, "12");
    expect(onChange).toHaveBeenLastCalledWith({ display_decimals: 12 });
  });

  it("tabbed through, leaves a saved count alone", () => {
    const { box, onChange } = renderBox(12);
    fireEvent.focus(box);
    fireEvent.blur(box);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("brings a negative count up to none", () => {
    const { box, onChange } = renderBox();
    typeAndLeave(box, "-3");
    expect(onChange).toHaveBeenLastCalledWith({ display_decimals: 0 });
  });

  it("takes a count in range, live as it is typed", () => {
    const { box, onChange } = renderBox();
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "2" } });
    expect(onChange).toHaveBeenLastCalledWith({ display_decimals: 2 });
  });

  it("emptied, unsets it", () => {
    const { box, onChange } = renderBox(2);
    typeAndLeave(box, "");
    expect(onChange).toHaveBeenLastCalledWith({ display_decimals: undefined });
  });
});
