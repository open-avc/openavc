import { describe, it, expect, vi, beforeEach } from "vitest";

// The assistant on a system behind the release it needs: the status names
// that release, and a send the cloud refuses for it (the release published
// while the pane was open) asks the status again, so the pane shows the update
// in place of the prompt box.

const cloud = vi.hoisted(() => ({
  getAIStatus: vi.fn(),
  streamChatMessage: vi.fn(),
  listConversations: vi.fn(async () => []),
}));
vi.mock("../api/cloudClient", () => cloud);
vi.mock("../api/restClient", () => ({ getProject: vi.fn(), saveProject: vi.fn(), reloadProject: vi.fn() }));

const { useAIChatStore } = await import("./aiChatStore");

const OUTDATED = {
  available: false,
  state: "outdated",
  reason: "This system runs OpenAVC v0.41.0. Update it to v0.42.0 to use the assistant.",
  needs_release: "0.42.0",
};

beforeEach(() => {
  cloud.getAIStatus.mockReset();
  cloud.streamChatMessage.mockReset();
  useAIChatStore.setState({
    available: true, unavailableState: "unpaired", unavailableReason: "", neededRelease: "",
    messages: [], sending: false, undoStack: [],
  });
});

describe("the assistant's availability", () => {
  it("names the release to install when the system is behind it", async () => {
    cloud.getAIStatus.mockResolvedValue(OUTDATED);
    await useAIChatStore.getState().checkAvailability();
    const s = useAIChatStore.getState();
    expect(s.available).toBe(false);
    expect(s.unavailableState).toBe("outdated");
    expect(s.neededRelease).toBe("0.42.0");
  });

  it("names none for any other refusal", async () => {
    useAIChatStore.setState({ neededRelease: "0.42.0" });
    cloud.getAIStatus.mockResolvedValue({ available: false, state: "unavailable", reason: "Paused." });
    await useAIChatStore.getState().checkAvailability();
    expect(useAIChatStore.getState().unavailableState).toBe("unavailable");
    expect(useAIChatStore.getState().neededRelease).toBe("");
  });

  it("asks again when a send is refused for the release", async () => {
    cloud.streamChatMessage.mockImplementation((_req, callbacks) => {
      callbacks.onError?.(OUTDATED.reason, "0.42.0");
      return new AbortController();
    });
    cloud.getAIStatus.mockResolvedValue(OUTDATED);

    useAIChatStore.getState().sendMessage("Add a projector", "sys-1", null);

    await vi.waitFor(() => expect(useAIChatStore.getState().unavailableState).toBe("outdated"));
    expect(useAIChatStore.getState().neededRelease).toBe("0.42.0");
  });

  it("does not ask again for a send refused for anything else", async () => {
    cloud.streamChatMessage.mockImplementation((_req, callbacks) => {
      callbacks.onError?.("Request timed out. Please try again.");
      return new AbortController();
    });

    useAIChatStore.getState().sendMessage("Add a projector", "sys-1", null);

    await vi.waitFor(() => expect(useAIChatStore.getState().sending).toBe(false));
    expect(cloud.getAIStatus).not.toHaveBeenCalled();
    expect(useAIChatStore.getState().available).toBe(true);
  });
});
