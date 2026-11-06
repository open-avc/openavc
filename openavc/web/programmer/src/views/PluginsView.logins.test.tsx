import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// The plugin page saves its settings by itself 1.5 s after each change, the
// whole config each time. A login typed there would go out half-typed with
// every pause, and the page would then hold it. So a login field saves on its
// own Save, at once, and the page takes the server's copy of that field back:
// the marker, never what was typed. Every other request carries the marker.

const MARKER = "<saved on this system>";

const plugin = {
  plugin_id: "acme_bridge",
  name: "Acme Bridge",
  version: "1.0.0",
  status: "running",
  category: "integration",
  config_schema: {
    host: { type: "string", label: "Host" },
    password: { type: "string", label: "Password" },
    connection: {
      type: "group",
      label: "Connection",
      fields: { api_key: { type: "string", label: "API key", secret: true } },
    },
  },
};

const serverConfig = () => ({ host: "broker.local", password: MARKER, connection: { api_key: MARKER } });

vi.mock("../api/restClient", () => ({
  listPlugins: vi.fn(async () => [plugin]),
  getPlugin: vi.fn(async () => plugin),
  getPluginExtensions: vi.fn(async () => ({
    views: [], device_panels: [], status_cards: [], context_actions: [], panel_elements: [],
  })),
  getPluginConfig: vi.fn(),
  updatePluginConfig: vi.fn(async () => ({ status: "updated", applied: "hot_applied" })),
  browseCommunityPlugins: vi.fn(async () => ({ plugins: [] })),
  listInstalledPlugins: vi.fn(async () => ({ plugins: [] })),
  getPluginDataInfo: vi.fn(async () => null),
}));
vi.mock("../store/projectStore", () => ({
  useProjectStore: Object.assign(
    (selector: (s: unknown) => unknown) => selector({ project: null }),
    { getState: () => ({ load: vi.fn(async () => {}) }) },
  ),
}));

import * as api from "../api/restClient";
import { usePluginStore } from "../store/pluginStore";
import { PluginsView } from "./PluginsView";

const putBodies = () => vi.mocked(api.updatePluginConfig).mock.calls.map((c) => c[1] as Record<string, unknown>);

beforeEach(() => {
  vi.mocked(api.updatePluginConfig).mockClear();
  vi.mocked(api.getPluginConfig).mockReset();
  vi.mocked(api.getPluginConfig).mockImplementation(async () => ({
    plugin_id: "acme_bridge",
    config: serverConfig(),
    needs_login: ["connection.api_key"],
  }));
  usePluginStore.setState({ selectedId: "acme_bridge" });
});

describe("the plugin page and a plugin's logins", () => {
  it("never sends a typed login with the autosave, only with its own Save, and keeps the marker after", async () => {
    render(<PluginsView />);
    await userEvent.click(await screen.findByRole("button", { name: "Change Password" }));
    await userEvent.type(screen.getByLabelText("Password"), "N3w-secret");

    // Another setting changed: the autosave goes out with the marker.
    const host = screen.getByDisplayValue("broker.local");
    await userEvent.type(host, "x");
    await waitFor(() => expect(api.updatePluginConfig).toHaveBeenCalledTimes(1), { timeout: 3000 });
    expect(putBodies()[0]).toMatchObject({ host: "broker.localx", password: MARKER });
    expect(JSON.stringify(putBodies())).not.toContain("N3w-secret");

    await userEvent.click(screen.getByRole("button", { name: "Save Password" }));
    await waitFor(() => expect(api.updatePluginConfig).toHaveBeenCalledTimes(2));
    expect(putBodies()[1]).toMatchObject({ host: "broker.localx", password: "N3w-secret" });

    // The page takes the server's copy back: the marker.
    expect(await screen.findByRole("button", { name: "Change Password" })).toBeTruthy();
    expect(vi.mocked(api.getPluginConfig).mock.calls.length).toBeGreaterThanOrEqual(2);
    for (const input of document.querySelectorAll("input")) {
      expect((input as HTMLInputElement).value).not.toBe("N3w-secret");
    }
  }, 10000);

  it("asks for a login this system does not have, and sends it by its path in the group", async () => {
    render(<PluginsView />);
    expect(await screen.findByText("Not saved on this system. Enter it here.")).toBeTruthy();
    await userEvent.type(screen.getByLabelText("API key"), "k-123456");
    await userEvent.click(screen.getByRole("button", { name: "Save API key" }));

    await waitFor(() => expect(api.updatePluginConfig).toHaveBeenCalledTimes(1));
    expect(putBodies()[0]).toMatchObject({
      password: MARKER,
      connection: { api_key: "k-123456" },
    });
  });
});
