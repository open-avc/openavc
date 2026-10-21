import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { CapacityInfo } from "../store/api";

vi.mock("../store/api", () => ({
  setCapacity: vi.fn(() => Promise.resolve()),
}));

import { CapacityPanel } from "./CapacityPanel";
import { setCapacity } from "../store/api";

function capacity(overrides: Partial<CapacityInfo> = {}): CapacityInfo {
  return {
    enabled: false,
    rx_buffer: 128,
    process_ms: 20,
    on_overflow: "discard",
    source: "simulator",
    declared: null,
    bytes_lost: 0,
    kind: "stream",
    ...overrides,
  };
}

function open() {
  fireEvent.click(screen.getByText(/^Capacity \(/));
}

beforeEach(() => vi.mocked(setCapacity).mockClear());

describe("the Capacity section", () => {
  it("says it is off, and switches on", () => {
    render(<CapacityPanel deviceId="amp1" capacity={capacity()} />);
    expect(screen.getByText("Capacity (off)")).toBeTruthy();
    open();
    fireEvent.click(screen.getByLabelText("Limit what this device can take in"));
    expect(setCapacity).toHaveBeenCalledWith("amp1", { enabled: true });
  });

  it("shows the bytes lost while it is on", () => {
    render(<CapacityPanel deviceId="amp1" capacity={capacity({ enabled: true, bytes_lost: 48 })} />);
    expect(screen.getByText("Capacity (on, 48 bytes lost)")).toBeTruthy();
  });

  it("sends a number when the field is left, not on every key", () => {
    render(<CapacityPanel deviceId="amp1" capacity={capacity()} />);
    open();
    const field = screen.getByLabelText(/Receive buffer/) as HTMLInputElement;
    fireEvent.change(field, { target: { value: "64" } });
    expect(setCapacity).not.toHaveBeenCalled();
    fireEvent.blur(field);
    expect(setCapacity).toHaveBeenCalledWith("amp1", { rx_buffer: 64 });
  });

  it("labels the simulator's own numbers as not the device's", () => {
    render(<CapacityPanel deviceId="amp1" capacity={capacity()} />);
    open();
    expect(screen.getByText("The simulator's own numbers, not this device's.")).toBeTruthy();
  });

  it("offers the driver's numbers back once they are changed", () => {
    const declared = { rx_buffer: 64, process_ms: 10, on_overflow: "silence" as const };
    render(
      <CapacityPanel
        deviceId="amp1"
        capacity={capacity({ source: "edited", declared, rx_buffer: 256 })}
      />,
    );
    open();
    fireEvent.click(screen.getByText("Use the driver's numbers"));
    expect(setCapacity).toHaveBeenCalledWith("amp1", declared);
  });

  it("says when a datagram device stays silent", () => {
    render(<CapacityPanel deviceId="amp1" capacity={capacity({ kind: "datagram" })} />);
    open();
    expect(screen.getByText("Stop answering until Capacity is switched off")).toBeTruthy();
  });

  it("shows the simulator's reason when a change is refused", async () => {
    vi.mocked(setCapacity).mockRejectedValueOnce(
      new Error("capacity.rx_buffer must be a whole number of bytes from 1 to 1048576"),
    );
    render(<CapacityPanel deviceId="amp1" capacity={capacity()} />);
    open();
    const field = screen.getByLabelText(/Receive buffer/) as HTMLInputElement;
    fireEvent.change(field, { target: { value: "0" } });
    fireEvent.blur(field);
    await waitFor(() =>
      expect(screen.getByText(/must be a whole number of bytes/)).toBeTruthy(),
    );
  });
});
