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
    render(<NumericInput value={25} min={0.1} onCommit={onCommit} />);
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    expect(input.value).toBe("");
    expect(onCommit).not.toHaveBeenCalled();
  });

  it("typing a replacement value after clearing live-commits it", async () => {
    const onCommit = vi.fn();
    render(<NumericInput value={25} min={0.1} onCommit={onCommit} />);
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, "40");
    // "4" then "40" are both valid and in range — live preview follows.
    expect(onCommit).toHaveBeenLastCalledWith(40);
    expect(input.value).toBe("40");
  });

  it("an emptied required field reverts on blur instead of committing a clamp", async () => {
    const onCommit = vi.fn();
    render(<NumericInput value={25} min={0.1} onCommit={onCommit} />);
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    fireEvent.blur(input);
    expect(onCommit).not.toHaveBeenCalled();
    expect(input.value).toBe("25");
  });

  it("an emptied allowEmpty field commits undefined (unset) on blur", async () => {
    const onCommit = vi.fn();
    render(<NumericInput value={25} allowEmpty onCommit={onCommit} placeholder="31.25" />);
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledExactlyOnceWith(undefined);
  });

  it("out-of-range input is tolerated while typing and clamped on blur", async () => {
    const onCommit = vi.fn();
    render(<NumericInput value={25} min={0.1} onCommit={onCommit} />);
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
    render(<NumericInput value={12} integer min={1} max={48} onCommit={onCommit} />);
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
    render(<NumericInput value={25} min={0.1} onCommit={onCommit} />);
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.keyboard("{Escape}");
    fireEvent.blur(input);
    expect(onCommit).not.toHaveBeenCalled();
    expect(input.value).toBe("25");
  });

  it("integer fields truncate toward zero on commit", async () => {
    const onCommit = vi.fn();
    render(<NumericInput value={8} integer min={1} onCommit={onCommit} />);
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, "2.7");
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenLastCalledWith(2);
  });
});
