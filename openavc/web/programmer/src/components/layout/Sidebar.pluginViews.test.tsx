import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// A plugin's page reaches this bar only through the `views` extension, which a
// plugin registers while it is running. So a plugin that fails to start takes
// its entry out of the bar and leaves nothing in its place: the page somebody
// used yesterday is gone, and the Plugins view that explains why is not where
// they would think to look.
//
// A plugin that is merely "stopped" is somebody having turned it off, and its
// page going with it is the point.

const mocks = vi.hoisted(() => ({
  plugins: [] as Array<Record<string, unknown>>,
  views: [] as Array<Record<string, unknown>>,
  liveState: {} as Record<string, unknown>,
}));

vi.mock("../../store/pluginStore", () => ({
  usePluginStore: (selector: (s: unknown) => unknown) =>
    selector({ plugins: mocks.plugins, extensions: { views: mocks.views } }),
}));

vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: unknown) => unknown) =>
    selector({ connected: true, liveState: mocks.liveState }),
}));

vi.mock("../../store/projectStore", () => ({
  useProjectStore: (selector: (s: unknown) => unknown) => selector({ dirty: false }),
}));

vi.mock("../../store/toastStore", () => ({ showError: vi.fn() }));

vi.mock("../../api/restClient", () => ({ getTunnelPrefix: () => "" }));

vi.mock("../../api/auth", () => ({
  getSessionToken: () => "t",
  hasSession: () => true,
  logout: vi.fn(),
}));

vi.mock("../shared/Modal", () => ({ Modal: () => null }));

import { Sidebar } from "./Sidebar";

function renderSidebar(onViewChange = vi.fn()) {
  render(<Sidebar activeView="dashboard" onViewChange={onViewChange} />);
  return onViewChange;
}

beforeEach(() => {
  mocks.plugins = [];
  mocks.views = [];
  mocks.liveState = {};
});

describe("Sidebar — a plugin whose page is missing", () => {
  it("says so when the plugin failed to start", async () => {
    mocks.plugins = [
      { plugin_id: "video_panel", name: "Video Streams", status: "error" },
    ];

    const onViewChange = renderSidebar();

    const entry = screen.getByRole("button", { name: "Video Streams is not running" });
    // And it leads somewhere that can explain it.
    await userEvent.click(entry);
    expect(onViewChange).toHaveBeenCalledWith("plugins");
  });

  it("says nothing about a plugin somebody turned off", () => {
    mocks.plugins = [
      { plugin_id: "video_panel", name: "Video Streams", status: "stopped" },
    ];

    renderSidebar();

    expect(screen.queryByRole("button", { name: /is not running/ })).toBeNull();
  });

  it("says nothing while the plugin is running and its page is present", () => {
    mocks.plugins = [
      { plugin_id: "video_panel", name: "Video Streams", status: "running" },
    ];
    mocks.views = [
      { plugin_id: "video_panel", id: "streams", label: "Video Streams" },
    ];

    renderSidebar();

    expect(screen.getByRole("button", { name: "Video Streams" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /is not running/ })).toBeNull();
  });
});
