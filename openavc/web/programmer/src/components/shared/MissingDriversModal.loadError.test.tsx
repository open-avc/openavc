import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// A driver whose file is installed but did not load is not "not installed":
// the prompt says so, with the server's reason under the driver's line.

vi.mock("../../api/deviceClient", () => ({ installMissingDrivers: vi.fn() }));

import { MissingDriversModal } from "./MissingDriversModal";

const notLoaded = {
  driver_id: "acme_widget",
  device_ids: ["lobby"],
  community_match: null,
  load_error: "State variable 'power': unknown type 'bogus'",
};

const notInstalled = { driver_id: "acme_gadget", device_ids: ["hall", "foyer"], community_match: null };

describe("Missing drivers: a driver that is installed but did not load", () => {
  it("says it did not load, with why, beside a driver that is not installed", () => {
    render(<MissingDriversModal missing={[notLoaded, notInstalled]} onClose={() => {}} onInstalled={() => {}} />);
    expect(
      screen.getByText(/This project uses 2 drivers that aren't installed or did not load/),
    ).toBeTruthy();
    expect(
      screen.getByText("Installed but did not load: State variable 'power': unknown type 'bogus'"),
    ).toBeTruthy();
    expect(screen.getAllByText(/Installed but did not load/)).toHaveLength(1);
  });

  it("keeps its sentence when every driver is simply not installed", () => {
    render(<MissingDriversModal missing={[notInstalled]} onClose={() => {}} onInstalled={() => {}} />);
    expect(screen.getByText(/This project uses 1 driver that isn't installed,/)).toBeTruthy();
    expect(screen.queryByText(/did not load/)).toBeNull();
  });
});
