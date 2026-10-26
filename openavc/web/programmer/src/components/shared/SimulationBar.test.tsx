/**
 * @vitest-environment jsdom
 * @vitest-environment-options { "url": "http://localhost:8080/programmer/" }
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Simulation stays on through a restart until somebody stops it, so every
// view says so while it runs, with Stop beside it. When it was left on but is
// not running (a comeback that failed, a simulator that stopped), the
// server's sentence says why, and Turn Off is what keeps the next restart
// from simulating again.

vi.mock("../../store/toastStore", () => ({ showError: vi.fn() }));

import { useConnectionStore } from "../../store/connectionStore";
import { showError } from "../../store/toastStore";
import { SimulationBar } from "./SimulationBar";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
  window.fetch = fetchMock as typeof window.fetch;
  useConnectionStore.setState({ liveState: {} });
});

afterEach(() => {
  useConnectionStore.setState({ liveState: {} });
});

function posted(): string[] {
  return fetchMock.mock.calls.map(([url]) => String(url));
}

describe("SimulationBar", () => {
  it("draws nothing when simulation is off", () => {
    render(<SimulationBar />);
    expect(screen.queryByTestId("simulation-bar")).not.toBeInTheDocument();
  });

  it("says the real equipment is not being controlled, and stops it", async () => {
    useConnectionStore.setState({ liveState: { "system.simulation_active": true } });
    render(<SimulationBar />);

    expect(screen.getByTestId("simulation-bar")).toHaveTextContent(
      "Simulation is on. The devices are connected to simulators, not the real equipment.",
    );
    expect(screen.getByTestId("simulation-bar")).toHaveTextContent(
      "It stays on after a restart until you stop it.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Stop Simulation" }));
    expect(posted()).toEqual(["/api/simulation/stop"]);
  });

  it("says why simulation is not running, and offers both ways out", async () => {
    useConnectionStore.setState({
      liveState: {
        "system.simulation_active": false,
        "system.simulation_error":
          "Simulation did not resume after the restart, so the devices are connecting to the real equipment. Port 19500 is in use.",
      },
    });
    render(<SimulationBar />);

    const bar = screen.getByTestId("simulation-bar");
    expect(bar).toHaveTextContent("Simulation did not resume after the restart");
    expect(bar).toHaveTextContent("Port 19500 is in use.");
    expect(bar).toHaveTextContent(
      "Simulation comes back at the next restart unless you turn it off.",
    );
    expect(screen.queryByRole("button", { name: "Stop Simulation" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Start Simulation" }));
    await userEvent.click(screen.getByRole("button", { name: "Turn Off Simulation" }));
    expect(posted()).toEqual(["/api/simulation/start", "/api/simulation/stop"]);
  });

  it("shows the server's refusal when a button fails", async () => {
    fetchMock.mockResolvedValue(
      new Response('{"detail":"No project loaded"}', { status: 400 }),
    );
    useConnectionStore.setState({
      liveState: { "system.simulation_error": "The simulator stopped, so the devices are connecting to the real equipment." },
    });
    render(<SimulationBar />);

    await userEvent.click(screen.getByRole("button", { name: "Start Simulation" }));
    expect(showError).toHaveBeenCalledWith("No project loaded");
  });
});
