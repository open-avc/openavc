/**
 * @vitest-environment jsdom
 * @vitest-environment-options { "url": "http://localhost:8080/programmer/" }
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";

// While the devices are simulated, every view says so: the bar sits above
// the view, not inside one, so moving around the IDE never hides it.

vi.mock("./hooks/useWebSocket", () => ({ useWebSocket: () => undefined }));
vi.mock("./components/layout/Sidebar", () => ({ Sidebar: () => <nav /> }));
vi.mock("./views/DashboardView", () => ({ DashboardView: () => <div>Dashboard view</div> }));
vi.mock("./views/LogView", () => ({ LogView: () => <div>Log view</div> }));

import App from "./App";
import { useConnectionStore } from "./store/connectionStore";
import { useNavigationStore } from "./store/navigationStore";

beforeEach(() => {
  sessionStorage.setItem("openavc.programmer.session", "session-token");
  window.fetch = vi.fn(async () => new Response("{}", { status: 200 })) as typeof window.fetch;
  useConnectionStore.setState({ liveState: { "system.simulation_active": true } });
});

afterEach(() => {
  sessionStorage.clear();
  useConnectionStore.setState({ liveState: {} });
  useNavigationStore.getState().navigateTo("dashboard");
});

describe("the simulation bar", () => {
  it("is on every view while the devices are simulated", async () => {
    render(<App />);
    expect(await screen.findByText("Dashboard view")).toBeInTheDocument();
    expect(screen.getByTestId("simulation-bar")).toHaveTextContent("Simulation is on.");

    act(() => useNavigationStore.getState().navigateTo("log"));
    expect(await screen.findByText("Log view")).toBeInTheDocument();
    expect(screen.getByTestId("simulation-bar")).toHaveTextContent("Simulation is on.");

    act(() => useConnectionStore.setState({ liveState: { "system.simulation_active": false } }));
    expect(screen.queryByTestId("simulation-bar")).not.toBeInTheDocument();
  });
});
