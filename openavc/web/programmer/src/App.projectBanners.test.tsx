/**
 * @vitest-environment jsdom
 * @vitest-environment-options { "url": "http://localhost:8080/programmer/" }
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, fireEvent } from "@testing-library/react";

// A failed save and a failed project load are each said across the top of
// the app, above whichever view is open, so a Save pressed on the Program
// view while the server is stopped says why nothing happened. The two are
// different failures in different words: a load that failed never reads
// "Save failed".

vi.mock("./hooks/useWebSocket", () => ({ useWebSocket: () => undefined }));
vi.mock("./components/layout/Sidebar", () => ({ Sidebar: () => <nav /> }));
vi.mock("./views/DashboardView", () => ({ DashboardView: () => <div>Dashboard view</div> }));
vi.mock("./views/ProjectView", () => ({ ProjectView: () => <div>Program view</div> }));

import App from "./App";
import { SERVER_NOT_ANSWERING } from "./api/errors";
import { useProjectStore } from "./store/projectStore";
import { useNavigationStore } from "./store/navigationStore";

const PROJECT = { openavc_version: "0.13.0", project: { id: "p", name: "P" }, ui: { pages: [] } };
let projectAnswers: boolean;

beforeEach(() => {
  sessionStorage.setItem("openavc.programmer.session", "session-token");
  projectAnswers = true;
  window.fetch = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/project")) {
      if (!projectAnswers) throw new TypeError("Failed to fetch");
      return new Response(JSON.stringify(PROJECT), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }) as typeof window.fetch;
  useProjectStore.setState({
    project: null, dirty: false, saving: false, loading: false,
    error: null, loadError: null, conflictDetected: false,
  });
});

afterEach(() => {
  sessionStorage.clear();
  useNavigationStore.getState().navigateTo("dashboard");
});

async function openProgramView() {
  render(<App />);
  act(() => useNavigationStore.getState().navigateTo("project"));
  expect(await screen.findByText("Program view")).toBeInTheDocument();
  // The load App starts on mount, settled before the test sets its state.
  await act(async () => {});
}

describe("the save failure line", () => {
  it("says why the save failed on the Program view, retries and dismisses", async () => {
    await openProgramView();
    const save = vi.fn(async () => {});
    act(() => useProjectStore.setState({ error: SERVER_NOT_ANSWERING, save }));
    expect(screen.getByText(`Save failed: ${SERVER_NOT_ANSWERING}`)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(save).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/Save failed/)).toBeNull();
    expect(useProjectStore.getState().error).toBeNull();
  });

  it("is drawn in the error colour, Retry the stronger of its two buttons", async () => {
    await openProgramView();
    act(() => useProjectStore.setState({ error: "disk full" }));
    const line = screen.getByText("Save failed: disk full").parentElement as HTMLElement;
    expect(line.style.background).toBe("rgba(244, 67, 54, 0.15)");
    expect(line.style.color).toBe("var(--color-error)");
    expect(line.style.borderBottom).toBe("1px solid var(--color-error)");
    const retry = screen.getByRole("button", { name: "Retry" }) as HTMLButtonElement;
    const dismiss = screen.getByRole("button", { name: "Dismiss" }) as HTMLButtonElement;
    expect([retry.style.padding, retry.style.fontWeight, retry.style.border, retry.style.color]).toEqual(["2px 10px", "600", "1px solid var(--color-error)", "var(--color-error)"]);
    expect([dismiss.style.padding, dismiss.style.fontWeight, dismiss.style.border, dismiss.style.color]).toEqual(["2px 10px", "", "1px solid var(--border-color)", "var(--text-secondary)"]);
  });

  it("stays away while a version conflict has its own banner", async () => {
    await openProgramView();
    act(() => useProjectStore.setState({ error: "Another session saved", conflictDetected: true }));
    expect(screen.queryByText(/Save failed/)).toBeNull();
    expect(screen.getByText("Another session saved")).toBeInTheDocument();
  });
});

describe("the load failure line", () => {
  it("says the project could not be loaded, never that a save failed, and Retry gets it", async () => {
    projectAnswers = false;
    render(<App />);
    expect(await screen.findByText(`Could not load the project: ${SERVER_NOT_ANSWERING}`)).toBeInTheDocument();
    expect(screen.queryByText(/Save failed/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();

    projectAnswers = true;
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Retry" })); });
    expect(screen.queryByText(/Could not load the project/)).toBeNull();
    expect(useProjectStore.getState().project?.project.name).toBe("P");
  });

  it("is on every view", async () => {
    projectAnswers = false;
    render(<App />);
    expect(await screen.findByText(/Could not load the project/)).toBeInTheDocument();
    act(() => useNavigationStore.getState().navigateTo("project"));
    expect(await screen.findByText("Program view")).toBeInTheDocument();
    expect(screen.getByText(/Could not load the project/)).toBeInTheDocument();
  });
});
