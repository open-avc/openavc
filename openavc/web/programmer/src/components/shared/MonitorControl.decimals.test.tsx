import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// The server refuses a monitor's decimals past 20, below 0 or with a fraction,
// and refuses the whole project save with it. The box brings what was typed
// inside that as it commits, so a save never carries one.

import { MonitorControl } from "./MonitorControl";

const KEY = "device.amp.level";

function renderBox(display_decimals?: number) {
  const onChange = vi.fn();
  render(
    <MonitorControl
      stateKey={KEY}
      declared={{ label: "Level", unit: "dB", type: "number" }}
      monitors={[{ key: KEY, type: "number", display_decimals }]}
      onChange={onChange}
      open
    />,
  );
  const box = screen.getByPlaceholderText("As reported") as HTMLInputElement;
  return { box, onChange };
}

function typeAndLeave(box: HTMLInputElement, text: string) {
  fireEvent.focus(box);
  fireEvent.change(box, { target: { value: text } });
  fireEvent.blur(box);
}

function lastDecimals(onChange: ReturnType<typeof vi.fn>) {
  const [monitors] = onChange.mock.calls.at(-1)!;
  return monitors.find((m: { key: string }) => m.key === KEY).display_decimals;
}

describe("A monitor's Decimals box", () => {
  it("brings a count past 20 down to 20", () => {
    const { box, onChange } = renderBox();
    typeAndLeave(box, "50");
    expect(lastDecimals(onChange)).toBe(20);
  });

  it("takes a whole number in range as typed", () => {
    const { box, onChange } = renderBox();
    typeAndLeave(box, "3");
    expect(lastDecimals(onChange)).toBe(3);
  });

  it("drops a fraction", () => {
    const { box, onChange } = renderBox();
    typeAndLeave(box, "2.5");
    expect(lastDecimals(onChange)).toBe(2);
  });

  it("emptied, goes back to as reported", () => {
    const { box, onChange } = renderBox(2);
    typeAndLeave(box, "");
    expect(lastDecimals(onChange)).toBeNull();
  });
});
