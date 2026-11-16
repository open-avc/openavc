import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({
  getUpdateNotice: vi.fn(),
  dismissUpdateNotice: vi.fn(),
}));
vi.mock("../api/restClient", () => api);
vi.mock("./ui-builder/projectIssues", () => ({ useProjectIssues: () => [{}, {}] }));

import { UpdateNotice } from "./UpdateNotice";
import { useConnectionStore } from "../store/connectionStore";
import { useNavigationStore } from "../store/navigationStore";
import { useUIBuilderStore } from "../store/uiBuilderStore";

const NOTES = [
  { kind: "changed", message: "Transition Duration changed from 99999 to 1000 ms.", location: "Panel Settings", page_id: "", element_id: "" },
  { kind: "changed", message: "Shown decimals changed from 50 to 20.", location: "Main > volume", page_id: "main", element_id: "volume" },
];

beforeEach(() => {
  api.getUpdateNotice.mockReset().mockResolvedValue({ at: "t1", notes: NOTES });
  api.dismissUpdateNotice.mockReset().mockResolvedValue({ status: "dismissed", cleared: true });
  useUIBuilderStore.setState({ validateRequested: false });
  useNavigationStore.setState({ activeView: "dashboard", pendingFocus: null } as never);
});

describe("the update notice", () => {
  it("says what loading the project changed and how many issues Validate lists", async () => {
    render(<UpdateNotice />);
    expect(await screen.findByText("This project was updated: 2 changes; 2 issues to review")).toBeInTheDocument();
    expect(screen.getByText(NOTES[0].message)).toBeInTheDocument();
    expect(screen.getByText("Main > volume")).toBeInTheDocument();
  });

  it("shows nothing when there is nothing to say", async () => {
    api.getUpdateNotice.mockResolvedValue({ at: "", notes: [] });
    const { container } = render(<UpdateNotice />);
    await waitFor(() => expect(api.getUpdateNotice).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("opens Validate in the UI Builder", async () => {
    render(<UpdateNotice />);
    fireEvent.click(await screen.findByRole("button", { name: "Open Validate" }));
    expect(useUIBuilderStore.getState().validateRequested).toBe(true);
    expect(useNavigationStore.getState().activeView).toBe("ui-builder");
  });

  it("goes to the control a note names", async () => {
    render(<UpdateNotice />);
    fireEvent.click(await screen.findByText(NOTES[1].message));
    expect(useNavigationStore.getState().pendingFocus).toEqual({ type: "element", id: "volume", detail: "page:main" });
  });

  it("is gone once dismissed, and asked for again when the server writes a new one", async () => {
    render(<UpdateNotice />);
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
    expect(api.dismissUpdateNotice).toHaveBeenCalledTimes(1);

    act(() => {
      useConnectionStore.setState({ liveState: { ...useConnectionStore.getState().liveState, "system.project_update_at": "t2" } });
    });
    expect(await screen.findByRole("status")).toBeInTheDocument();
    expect(api.getUpdateNotice).toHaveBeenCalledTimes(2);
  });
});
