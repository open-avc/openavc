import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SliderControl } from "./SliderControl";
import type { SliderControlDef } from "../../store/api";

// The reported symptom: drag a slider and it does not move, then a couple of
// seconds later it jumps in steps. The cause was that the thumb's position
// came only from server state, so it could not move until a write had gone out
// and the change come back. These pin the fix from the user's side.

const CONTROL: SliderControlDef = {
  type: "slider",
  key: "ac_line_voltage",
  label: "AC Line Voltage",
  min: 0,
  max: 300,
  step: 1,
  unit: "V",
};

function setup(stateValue: unknown = 120) {
  const onStateChange = vi.fn();
  const utils = render(
    <SliderControl
      control={CONTROL}
      state={{ [CONTROL.key]: stateValue }}
      onStateChange={onStateChange}
    />,
  );
  const slider = screen.getByLabelText("AC Line Voltage") as HTMLInputElement;
  const readout = screen.getByLabelText("AC Line Voltage value") as HTMLInputElement;
  return { onStateChange, slider, readout, ...utils };
}

describe("slider under the hand", () => {
  it("moves while dragging, before the device has confirmed anything", () => {
    const { slider, onStateChange } = setup(120);

    fireEvent.change(slider, { target: { value: "200" } });

    // Server state is still 120 — no reply has arrived — and the thumb has
    // moved anyway. This is the whole bug: it used to snap back to 120.
    expect(slider.value).toBe("200");
    expect(onStateChange).toHaveBeenCalledWith("ac_line_voltage", 200);
  });

  it("goes back to reporting the device once released", () => {
    const { slider, rerender, onStateChange } = setup(120);
    fireEvent.change(slider, { target: { value: "200" } });
    fireEvent.pointerUp(slider);

    // The device clamped to 180 rather than echoing 200 — which a simulated
    // device really does. The slider must follow the device, not sulk on the
    // value it sent. Holding the draft until the server agreed would stick here
    // forever.
    rerender(
      <SliderControl
        control={CONTROL}
        state={{ [CONTROL.key]: 180 }}
        onStateChange={onStateChange}
      />,
    );
    expect((screen.getByLabelText("AC Line Voltage") as HTMLInputElement).value).toBe("180");
  });

  it("follows the device when nobody is touching it", () => {
    const { onStateChange, rerender } = setup(120);
    rerender(
      <SliderControl
        control={CONTROL}
        state={{ [CONTROL.key]: 240 }}
        onStateChange={onStateChange}
      />,
    );
    expect((screen.getByLabelText("AC Line Voltage") as HTMLInputElement).value).toBe("240");
  });
});

describe("typing an exact value", () => {
  it("accepts a value the step cannot land on", () => {
    const tone: SliderControlDef = {
      type: "slider", key: "tone", label: "Tone", min: 20, max: 20000, step: 50,
    };
    const onStateChange = vi.fn();
    render(<SliderControl control={tone} state={{ tone: 1000 }} onStateChange={onStateChange} />);

    const readout = screen.getByLabelText("Tone value") as HTMLInputElement;
    fireEvent.change(readout, { target: { value: "440" } });
    fireEvent.blur(readout);

    // 440 is not a multiple of the 50 Hz drag step, and it still gets through.
    expect(onStateChange).toHaveBeenCalledWith("tone", 440);
  });

  it("clamps a typed value to the declared range", () => {
    const { readout, onStateChange } = setup(120);
    fireEvent.change(readout, { target: { value: "9999" } });
    fireEvent.blur(readout);
    expect(onStateChange).toHaveBeenCalledWith("ac_line_voltage", 300);
  });

  it("ignores nonsense rather than writing NaN to the device", () => {
    const { readout, onStateChange } = setup(120);
    fireEvent.change(readout, { target: { value: "abc" } });
    fireEvent.blur(readout);
    expect(onStateChange).not.toHaveBeenCalled();
  });

  it("abandons the edit on Escape", () => {
    const { readout, onStateChange } = setup(120);
    fireEvent.change(readout, { target: { value: "55" } });
    fireEvent.keyDown(readout, { key: "Escape" });
    expect(onStateChange).not.toHaveBeenCalled();
    expect(readout.value).toBe("120");
  });
});
