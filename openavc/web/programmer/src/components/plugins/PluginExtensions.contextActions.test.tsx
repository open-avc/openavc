import { describe, it, expect, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

// Two plugins may give a context action the same id: an entry is named by its
// plugin first. Pressing one plugin's button holds only that button while its
// request runs, never the other plugin's of the same id.

const mocks = vi.hoisted(() => ({
  emit: vi.fn(),
  actions: [] as unknown[],
}));

vi.mock("../../store/pluginStore", () => {
  const state = {
    get extensions() {
      return { views: [], device_panels: [], status_cards: [], context_actions: mocks.actions, panel_elements: [] };
    },
  };
  return { usePluginStore: (selector: (s: unknown) => unknown) => selector(state) };
});

vi.mock("../../api/restClient", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/restClient")>()),
  emitContextAction: mocks.emit,
}));

import { ContextActionRenderer } from "./PluginExtensions";

function action(pluginId: string, label: string) {
  return {
    id: "refresh", label, context: "global", event: "action.refresh",
    plugin_id: pluginId, plugin_name: `Acme ${pluginId}`,
  };
}

describe("context actions of the same id in two plugins", () => {
  it("holds only the pressed plugin's button while its request runs", async () => {
    let finish: () => void = () => {};
    mocks.emit.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
    mocks.actions = [action("acme_a", "Refresh A"), action("acme_b", "Refresh B")];
    render(<ContextActionRenderer context="global" />);

    fireEvent.click(screen.getByRole("button", { name: /Refresh A/ }));
    expect(mocks.emit).toHaveBeenCalledWith("acme_a", "refresh", {});
    expect(screen.getByRole("button", { name: /Refresh A/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Refresh B/ })).not.toBeDisabled();

    await act(async () => { finish(); });
    expect(screen.getByRole("button", { name: /Refresh A/ })).not.toBeDisabled();
  });
});
