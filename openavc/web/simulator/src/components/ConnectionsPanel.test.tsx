import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { SingleClientInfo } from "../store/api";

vi.mock("../store/api", () => ({
  setSingleClient: vi.fn(() => Promise.resolve()),
}));

import { ConnectionsPanel } from "./ConnectionsPanel";
import { setSingleClient } from "../store/api";

function info(overrides: Partial<SingleClientInfo> = {}): SingleClientInfo {
  return {
    mode: null,
    declared: null,
    source: "simulator",
    open: 1,
    ignored: 0,
    refused: 0,
    ...overrides,
  };
}

function open() {
  fireEvent.click(screen.getByText(/^Connections \(/));
}

beforeEach(() => vi.mocked(setSingleClient).mockClear());

describe("the Connections section", () => {
  it("counts the open connections of a device that answers every one", () => {
    render(<ConnectionsPanel deviceId="tv1" singleClient={info({ open: 2 })} />);
    expect(screen.getByText("Connections (2 open)")).toBeTruthy();
  });

  it("says how many connections get no answers", () => {
    render(
      <ConnectionsPanel
        deviceId="tv1"
        singleClient={info({ mode: "newest", source: "edited", open: 2, ignored: 1 })}
      />,
    );
    expect(screen.getByText("Connections (one at a time, 2 open, 1 not answered)")).toBeTruthy();
  });

  it("counts the connections it closed", () => {
    render(<ConnectionsPanel deviceId="tv1" singleClient={info({ mode: "reject", refused: 3 })} />);
    expect(screen.getByText("Connections (one at a time, 1 open, 3 closed)")).toBeTruthy();
  });

  it("changes what the device does with another connection", () => {
    render(<ConnectionsPanel deviceId="tv1" singleClient={info()} />);
    open();
    fireEvent.change(screen.getByLabelText("When another arrives"), { target: { value: "newest" } });
    expect(setSingleClient).toHaveBeenCalledWith("tv1", "newest");
  });

  it("goes back to answering every connection", () => {
    render(<ConnectionsPanel deviceId="tv1" singleClient={info({ mode: "oldest", source: "edited" })} />);
    open();
    fireEvent.change(screen.getByLabelText("When another arrives"), { target: { value: "" } });
    expect(setSingleClient).toHaveBeenCalledWith("tv1", null);
  });

  it("says what the chosen mode does to the connections", () => {
    render(<ConnectionsPanel deviceId="tv1" singleClient={info({ mode: "newest", declared: "newest", source: "driver" })} />);
    open();
    expect(screen.getByText(/The connection already open gets no more answers and is not closed/)).toBeTruthy();
    expect(screen.getByText("As the driver declares.")).toBeTruthy();
  });

  it("offers the driver's setting back once it is changed", () => {
    render(<ConnectionsPanel deviceId="tv1" singleClient={info({ mode: null, declared: "newest", source: "edited" })} />);
    open();
    fireEvent.click(screen.getByText("Use the driver's setting"));
    expect(setSingleClient).toHaveBeenCalledWith("tv1", "newest");
  });
});
