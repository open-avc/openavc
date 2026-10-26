import { describe, it, expect, vi, beforeEach } from "vitest";

// A plugin can report parts of its stored config it skips. An editor that
// re-sends the whole config on every autosave gets them back each time, so
// the IDE says them once and stays quiet until they change.

vi.mock("../api/restClient", () => ({
  updatePluginConfig: vi.fn(),
  listPlugins: vi.fn(async () => []),
  getPluginExtensions: vi.fn(async () => ({})),
}));
vi.mock("./toastStore", () => ({ showError: vi.fn() }));
vi.mock("./projectStore", () => ({
  useProjectStore: { getState: () => ({ load: vi.fn(async () => {}) }) },
}));

import * as api from "../api/restClient";
import { showError } from "./toastStore";
import { usePluginStore } from "./pluginStore";

const saved = (config_problems?: string[]) => ({ status: "updated", applied: "hot_applied", config_problems });

describe("saving a plugin config that already carried a problem", () => {
  beforeEach(() => {
    vi.mocked(showError).mockClear();
  });

  it("says it once, then again only when the problems change", async () => {
    const update = vi.mocked(api.updatePluginConfig);
    const problem = 'dials[0]: index "0" is not a dial number (0 or more), so it is on no dial.';

    update.mockResolvedValueOnce(saved([problem]));
    await usePluginStore.getState().updateConfig("acme_surface", {});
    expect(showError).toHaveBeenCalledTimes(1);
    expect(vi.mocked(showError).mock.calls[0][0]).toContain(problem);

    update.mockResolvedValueOnce(saved([problem]));
    await usePluginStore.getState().updateConfig("acme_surface", {});
    expect(showError).toHaveBeenCalledTimes(1);

    update.mockResolvedValueOnce(saved());
    await usePluginStore.getState().updateConfig("acme_surface", {});
    update.mockResolvedValueOnce(saved([problem]));
    await usePluginStore.getState().updateConfig("acme_surface", {});
    expect(showError).toHaveBeenCalledTimes(2);
  });
});
