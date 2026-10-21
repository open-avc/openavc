import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// Browse Plugins marks a plugin this OpenAVC is too old for and does not offer
// Install, as Browse Drivers does for a driver. Before, Install was offered and
// the server refused the plugin after the download.

const mocks = vi.hoisted(() => ({
  install: vi.fn(async () => undefined),
  plugins: [] as unknown[],
}));

vi.mock("../../store/pluginStore", () => {
  const state = {
    get communityPlugins() { return mocks.plugins; },
    installedPlugins: [],
    communityLoading: false,
    communityError: null,
    installingIds: new Set<string>(),
    loadCommunity: vi.fn(),
    installCommunityPlugin: mocks.install,
    updateCommunityPlugin: vi.fn(),
    uninstallPlugin: vi.fn(),
  };
  return { usePluginStore: (selector: (s: unknown) => unknown) => selector(state) };
});

import { BrowsePlugins } from "./BrowsePlugins";
import { useConnectionStore } from "../../store/connectionStore";

function plugin(id: string, minOpenavc: string) {
  return {
    id, name: `Acme ${id}`, file: `${id}/plugin.py`, format: "python", category: "utility",
    version: "1.0.0", author: "Acme", license: "MIT", platforms: ["all"], capabilities: [],
    verified: true, description: "An Acme plugin.", min_openavc_version: minOpenavc,
  };
}

beforeEach(() => {
  useConnectionStore.setState({ liveState: { "system.version": "0.37.0" } });
  mocks.install.mockClear();
});

describe("Browse Plugins, a plugin that needs a newer OpenAVC", () => {
  it("is marked and its Install does nothing", () => {
    mocks.plugins = [plugin("future", "99.0.0")];
    render(<BrowsePlugins />);
    expect(screen.getByText("Needs OpenAVC v99.0.0")).toBeTruthy();
    const install = screen.getByRole("button", { name: /Install/ });
    expect(install).toBeDisabled();
    fireEvent.click(install);
    expect(mocks.install).not.toHaveBeenCalled();
  });

  it("is offered when this OpenAVC is new enough", () => {
    mocks.plugins = [plugin("today", "0.36.0")];
    render(<BrowsePlugins />);
    expect(screen.queryByText(/Needs OpenAVC/)).toBeNull();
    expect(screen.getByRole("button", { name: /Install/ })).not.toBeDisabled();
  });
});
