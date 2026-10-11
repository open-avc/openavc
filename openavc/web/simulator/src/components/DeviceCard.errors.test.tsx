import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { DeviceInfo } from "../store/api";

vi.mock("../store/api", () => ({
  setDeviceState: vi.fn(() => Promise.resolve()),
  toggleError: vi.fn(() => Promise.resolve()),
}));

import { DeviceCard } from "./DeviceCard";
import { toggleError } from "../store/api";

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

function device(available_errors: DeviceInfo["available_errors"]): DeviceInfo {
  return {
    device_id: "amp1",
    device_name: "Test Amp",
    real_host: "192.0.2.10",
    real_port: 4321,
    driver_id: "acme_amp",
    name: "Acme Amp",
    category: "generic",
    transport: "tcp",
    port: 19700,
    running: true,
    state: {},
    active_errors: [],
    available_errors,
    push_state: false,
  } as DeviceInfo;
}

function openErrors() {
  fireEvent.click(screen.getByText(/^Errors \(/));
}

describe("the Errors section", () => {
  it("is there for a device whose driver declares no error modes", () => {
    render(
      <DeviceCard
        device={device({
          communication_timeout: { description: "Device stops responding", builtin: true },
        })}
      />,
    );
    openErrors();
    fireEvent.click(screen.getByLabelText("Device stops responding"));
    expect(toggleError).toHaveBeenCalledWith("amp1", "communication_timeout", true);
  });

  it("lists the driver's own modes first and the built-in one last", () => {
    render(
      <DeviceCard
        device={device({
          communication_timeout: { description: "Device stops responding", builtin: true },
          amp_fault: { description: "Amplifier fault" },
          lamp_warning: { description: "Thermal warning" },
        })}
      />,
    );
    openErrors();
    const labels = screen.getAllByRole("checkbox").map(
      (box) => box.closest("label")?.textContent,
    );
    expect(labels).toEqual(["Amplifier fault", "Thermal warning", "Device stops responding"]);
  });
});
