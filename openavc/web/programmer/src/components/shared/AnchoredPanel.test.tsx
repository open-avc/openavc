import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAnchoredPanel } from "./AnchoredPanel";

function Picker() {
  const panel = useAnchoredPanel<HTMLButtonElement>();
  return (
    <div>
      <div data-testid="elsewhere" />
      <div ref={panel.containerRef}>
        <button ref={panel.triggerRef} type="button" onClick={panel.toggle}>
          Open
        </button>
        {panel.open && <div role="listbox">options</div>}
      </div>
    </div>
  );
}

function triggerAt(top: number) {
  return vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    () => ({ top, left: 20, bottom: top + 30, right: 220, width: 200, height: 30, x: 20, y: top, toJSON: () => ({}) }),
  );
}

describe("useAnchoredPanel and scrolling", () => {
  afterEach(() => vi.restoreAllMocks());

  it("stays open for a scroll that did not move its trigger", () => {
    triggerAt(100);
    render(<Picker />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    // A list elsewhere scrolling itself, or the late event of the scroll that
    // brought the trigger into view before the click.
    act(() => {
      fireEvent.scroll(screen.getByTestId("elsewhere"));
    });
    expect(screen.getByRole("listbox")).toBeTruthy();
  });

  it("closes when a scroll carried its trigger away", () => {
    const rect = triggerAt(100);
    render(<Picker />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    rect.mockRestore();
    triggerAt(40);
    act(() => {
      fireEvent.scroll(screen.getByTestId("elsewhere"));
    });
    expect(screen.queryByRole("listbox")).toBeNull();
  });
});
