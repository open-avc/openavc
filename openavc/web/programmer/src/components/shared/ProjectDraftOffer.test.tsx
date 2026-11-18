import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { ProjectConfig } from "../../api/types";

vi.mock("../../api/restClient", () => ({
  getProject: vi.fn(),
  saveProject: vi.fn(),
  listDrivers: vi.fn(),
  ConflictError: class ConflictError extends Error {},
}));

const { useDraftOffer, draftOf, draftKey, readDraft } = await import("../../store/projectDraft");
const { ProjectDraftOffer } = await import("./ProjectDraftOffer");

const project = {
  project: { id: "lobby", name: "Lobby" },
  devices: [],
  connections: {},
} as unknown as ProjectConfig;

function offer(kind: "current" | "stale", reenter: string[] = []): void {
  const draft = draftOf(project, { etag: '"b-1"', revision: "b-1" }, () => null);
  sessionStorage.setItem(draftKey(), JSON.stringify(draft));
  useDraftOffer.setState({ offer: { kind, draft, reenter } });
}

beforeEach(() => {
  sessionStorage.clear();
  useDraftOffer.setState({ offer: null });
});

describe("the unsaved changes offer", () => {
  it("asks to restore, naming the devices whose login has to be entered again", () => {
    offer("current", ["Widget Two", "Camera"]);
    render(<ProjectDraftOffer />);
    expect(screen.getByText("Restore unsaved changes?")).toBeInTheDocument();
    expect(screen.getByText(/Enter the logins again for Widget Two, Camera\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restore" })).toBeInTheDocument();
  });

  it("offers a moved project's changes to look at, not to save", () => {
    offer("stale");
    render(<ProjectDraftOffer />);
    expect(screen.getByText(/cannot be saved over the newer project/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open them" })).toBeInTheDocument();
  });

  it("is not answered by Escape", () => {
    offer("current");
    render(<ProjectDraftOffer />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(useDraftOffer.getState().offer).not.toBeNull();
    expect(readDraft()).not.toBeNull();
  });

  it("removes the kept changes on Discard", () => {
    offer("current");
    render(<ProjectDraftOffer />);
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(useDraftOffer.getState().offer).toBeNull();
    expect(readDraft()).toBeNull();
  });
});
