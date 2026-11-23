import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { useConnectionStore } from "../../../store/connectionStore";
import type { SurfaceLayout } from "./types";

const mocks = vi.hoisted(() => ({ emit: vi.fn() }));

vi.mock("../../../api/restClient", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../api/restClient")>()),
  emitContextAction: mocks.emit,
}));

import { RoutingMatrix } from "./RoutingMatrix";

// A plugin that publishes each source's name under its column key and, per
// destination, the name of the source routed to it, blank when none.
const LAYOUT: SurfaceLayout = {
  type: "matrix",
  rows_state_pattern: "plugin.acme_router.rx.*",
  columns_state_pattern: "plugin.acme_router.tx.*",
  route_state_pattern: "plugin.acme_router.source.{row}",
  route_match: "exact",
};

const STATE = {
  "plugin.acme_router.tx.stagebox.1": "Stagebox / 01",
  "plugin.acme_router.tx.laptop.1": "Laptop / 01",
  "plugin.acme_router.rx.amp.1": "Amp / L",
  "plugin.acme_router.rx.amp.2": "Amp / R",
  // Named by the device in its own case, with a space after it.
  "plugin.acme_router.source.amp.1": "STAGEBOX / 01 ",
  "plugin.acme_router.source.amp.2": "",
};

function draw(layout: SurfaceLayout = LAYOUT, state: Record<string, unknown> = STATE) {
  act(() => { useConnectionStore.setState({ liveState: state }); });
  render(<RoutingMatrix layout={layout} pluginId="acme_router" config={{}} />);
}

const escaped = (text: string) => text.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
// A crosspoint by the names its row and column are drawn with.
const cell = (row: string, col: string) =>
  screen.getByRole("button", { name: new RegExp(`^${escaped(row)} → ${escaped(col)}: `) });

beforeEach(() => {
  mocks.emit.mockReset();
  mocks.emit.mockResolvedValue({ status: "emitted" });
});

describe("a destination's route, read from its own key", () => {
  it("draws the crosspoint its source names as routed, and a click on it unroutes it", async () => {
    draw();
    expect(cell("Amp / L", "Stagebox / 01")).toHaveAccessibleName("Amp / L → Stagebox / 01: Routed");
    await act(async () => { fireEvent.click(cell("Amp / L", "Stagebox / 01")); });
    expect(mocks.emit).toHaveBeenCalledWith("acme_router", "unroute", { row: "amp.1", col: "stagebox.1" });
  });

  it("draws a source sharing only a number as another source, and a click on it routes it", async () => {
    draw();
    expect(cell("Amp / L", "Laptop / 01")).toHaveAccessibleName("Amp / L → Laptop / 01: Unrouted");
    await act(async () => { fireEvent.click(cell("Amp / L", "Laptop / 01")); });
    expect(mocks.emit).toHaveBeenCalledWith("acme_router", "route", { row: "amp.1", col: "laptop.1" });
  });

  it("draws every crosspoint of a destination routed to nothing as unrouted", () => {
    draw();
    expect(cell("Amp / R", "Stagebox / 01")).toHaveAccessibleName("Amp / R → Stagebox / 01: Unrouted");
    expect(cell("Amp / R", "Laptop / 01")).toHaveAccessibleName("Amp / R → Laptop / 01: Unrouted");
  });

  it("follows the route as the plugin reports it moving", () => {
    draw();
    act(() => {
      useConnectionStore.setState({ liveState: { ...STATE, "plugin.acme_router.source.amp.1": "Laptop / 01" } });
    });
    expect(cell("Amp / L", "Stagebox / 01")).toHaveAccessibleName("Amp / L → Stagebox / 01: Unrouted");
    expect(cell("Amp / L", "Laptop / 01")).toHaveAccessibleName("Amp / L → Laptop / 01: Routed");
  });

  it("compares exactly when the layout names no mode", () => {
    const { route_match: _omitted, ...layout } = LAYOUT;
    draw(layout);
    expect(cell("Amp / L", "Stagebox / 01")).toHaveAccessibleName("Amp / L → Stagebox / 01: Routed");
    expect(cell("Amp / L", "Laptop / 01")).toHaveAccessibleName("Amp / L → Laptop / 01: Unrouted");
  });

  it("compares in the mode the layout declares", () => {
    draw({ ...LAYOUT, route_match: "port" });
    expect(cell("Amp / L", "Laptop / 01")).toHaveAccessibleName("Amp / L → Laptop / 01: Routed");
  });

  it("draws every crosspoint unrouted for a layout that names no route key", () => {
    const { route_state_pattern: _omitted, ...layout } = LAYOUT;
    draw(layout);
    expect(cell("Amp / L", "Stagebox / 01")).toHaveAccessibleName("Amp / L → Stagebox / 01: Unrouted");
  });
});

describe("what each row and column is called", () => {
  it("heads each row and column with the name the plugin publishes for it", () => {
    draw();
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "", "Laptop / 01", "Stagebox / 01",
    ]);
    expect(screen.getByRole("cell", { name: "Amp / L" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Amp / R" })).toBeInTheDocument();
  });

  it("heads a row the plugin gives no name with its key's own text, and still sends that", async () => {
    draw(LAYOUT, { ...STATE, "plugin.acme_router.rx.amp.2": "" });
    expect(screen.getByRole("cell", { name: "amp.2" })).toBeInTheDocument();
    await act(async () => { fireEvent.click(cell("amp.2", "Laptop / 01")); });
    expect(mocks.emit).toHaveBeenCalledWith("acme_router", "route", { row: "amp.2", col: "laptop.1" });
  });
});
