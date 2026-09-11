import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NumericInput } from "./NumericInput";

// The regression these pin down: the old commit-per-keystroke fields turned a
// cleared Width box into a live 0.1%-wide element (`Number("") || 0` then
// `Math.max(0.1, 0)`), so you could never clear-and-retype. A numeric field
// must tolerate being empty mid-edit and clamp once, on commit.

describe("NumericInput", () => {
  it("clearing the field commits nothing and stays empty while focused", async () => {
    const onCommit = vi.fn();
    render(<NumericInput owner="w" value={25} min={0.1} onCommit={onCommit} />);
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    expect(input.value).toBe("");
    expect(onCommit).not.toHaveBeenCalled();
  });

  it("typing a replacement value after clearing live-commits it", async () => {
    const onCommit = vi.fn();
    render(<NumericInput owner="w" value={25} min={0.1} onCommit={onCommit} />);
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, "40");
    // "4" then "40" are both valid and in range — live preview follows.
    expect(onCommit).toHaveBeenLastCalledWith(40);
    expect(input.value).toBe("40");
  });

  it("an emptied required field reverts on blur instead of committing a clamp", async () => {
    const onCommit = vi.fn();
    render(<NumericInput owner="w" value={25} min={0.1} onCommit={onCommit} />);
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    fireEvent.blur(input);
    expect(onCommit).not.toHaveBeenCalled();
    expect(input.value).toBe("25");
  });

  it("an emptied allowEmpty field commits undefined (unset) on blur", async () => {
    const onCommit = vi.fn();
    render(<NumericInput owner="w" value={25} allowEmpty onCommit={onCommit} placeholder="31.25" />);
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledExactlyOnceWith(undefined);
  });

  it("out-of-range input is tolerated while typing and clamped on blur", async () => {
    const onCommit = vi.fn();
    render(<NumericInput owner="w" value={25} min={0.1} onCommit={onCommit} />);
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, "0.05");
    // Below the floor: nothing live-committed, nothing fought.
    expect(onCommit).not.toHaveBeenCalled();
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledExactlyOnceWith(0.1);
  });

  it("Enter clamps into range, and the following blur does not double-commit", async () => {
    const onCommit = vi.fn();
    render(<NumericInput owner="w" value={12} integer min={1} max={48} onCommit={onCommit} />);
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, "60{Enter}");
    fireEvent.blur(input);
    // "6" live-commits on the way (valid), then Enter clamps 60 → 48 once.
    expect(onCommit).toHaveBeenCalledTimes(2);
    expect(onCommit).toHaveBeenNthCalledWith(1, 6);
    expect(onCommit).toHaveBeenLastCalledWith(48);
  });

  it("Escape reverts the draft without committing", async () => {
    const onCommit = vi.fn();
    render(<NumericInput owner="w" value={25} min={0.1} onCommit={onCommit} />);
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.keyboard("{Escape}");
    fireEvent.blur(input);
    expect(onCommit).not.toHaveBeenCalled();
    expect(input.value).toBe("25");
  });

  // A property panel re-renders these fields in place when the selection
  // changes -- same input, new subject, no remount -- so a draft left behind by
  // Tab or a half-typed number would blur into the writer of whatever arrived.
  // Typing 68 into a light's Y and then clicking the container behind it grew
  // the container by a third of a page, autosaved, with nothing said.

  it("a selection change drops the carried draft instead of writing it onto what arrived", async () => {
    const onLed = vi.fn();
    const onGroup = vi.fn();
    const { rerender } = render(
      <NumericInput owner="led_1/w" value={47.6191} min={0.1} onCommit={onLed} />,
    );
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    // Tab out of Y lands here holding the light's own width, untouched.
    await userEvent.click(input);
    expect(input.value).toBe("47.6191");
    // The canvas click selects the container: same field, different subject.
    rerender(<NumericInput owner="group_1/w" value={35} min={0.1} onCommit={onGroup} />);
    // Visibly the container's number now, which is the only warning there is.
    expect(input.value).toBe("35");
    fireEvent.blur(input);
    expect(onGroup).not.toHaveBeenCalled();
    expect(onLed).not.toHaveBeenCalled();
  });

  it("a number typed for one subject is not clamped onto the next one", async () => {
    const onLed = vi.fn();
    const onGroup = vi.fn();
    const { rerender } = render(
      <NumericInput owner="led_1/w" value={47.6191} min={0.1} onCommit={onLed} />,
    );
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    // Under the floor, so nothing live-commits and the draft is all there is.
    await userEvent.type(input, "0.05");
    expect(onLed).not.toHaveBeenCalled();
    rerender(<NumericInput owner="group_1/w" value={35} min={0.1} onCommit={onGroup} />);
    fireEvent.blur(input);
    // Without the rule this is the blur that writes 0.1 onto the container.
    expect(onGroup).not.toHaveBeenCalled();
  });

  it("typing after the subject changed edits the new subject", async () => {
    const onLed = vi.fn();
    const onGroup = vi.fn();
    const { rerender } = render(
      <NumericInput owner="led_1/w" value={47.6191} min={0.1} onCommit={onLed} />,
    );
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.click(input);
    rerender(<NumericInput owner="group_1/w" value={35} min={0.1} onCommit={onGroup} />);
    await userEvent.clear(input);
    await userEvent.type(input, "40");
    expect(onLed).not.toHaveBeenCalled();
    expect(onGroup).toHaveBeenLastCalledWith(40);
  });

  it("the same subject coming back with a corrected value still commits the draft", async () => {
    const onCommit = vi.fn();
    const { rerender } = render(
      <NumericInput owner="body/font_size" value={13} min={0} max={64} onCommit={onCommit} />,
    );
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, "17");
    // The theme editor stores rem and reads px back, so the value returns
    // slightly changed. Same subject, so this is still the author's own edit
    // and dropping it here would be the opposite bug.
    rerender(
      <NumericInput owner="body/font_size" value={16.99} min={0} max={64} onCommit={onCommit} />,
    );
    expect(input.value).toBe("17");
    onCommit.mockClear();
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledExactlyOnceWith(17);
  });

  it("integer fields truncate toward zero on commit", async () => {
    const onCommit = vi.fn();
    render(<NumericInput owner="w" value={8} integer min={1} onCommit={onCommit} />);
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, "2.7");
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenLastCalledWith(2);
  });
});
