import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { DriverDefinition, ProjectConfig } from "../api/types";

vi.mock("../api/restClient", () => ({
  listDriverDefinitions: vi.fn(),
  deleteDriverDefinition: vi.fn(),
}));
vi.mock("../components/driver-builder/InstalledDriversView", () => ({ InstalledDriversView: () => null }));
vi.mock("../components/driver-builder/CommunityBrowser", () => ({ CommunityBrowser: () => null }));
vi.mock("../components/driver-builder/DriverEditor", () => ({ DriverEditor: () => null }));

import * as api from "../api/restClient";
import { ApiError } from "../api/errors";
import { useDriverBuilderStore } from "../store/driverBuilderStore";
import { useNavigationStore } from "../store/navigationStore";
import { useProjectStore } from "../store/projectStore";
import { DriverPanel } from "./DriverBuilderView";

const WIDGET = {
  id: "acme_widget", name: "Acme Widget", transport: "tcp", source: "user", _revision: "r1",
  default_config: {}, config_schema: {}, state_variables: {}, commands: {}, responses: [], polling: {},
} as unknown as DriverDefinition;

function project(devices: { id: string; name: string; driver: string }[]) {
  useProjectStore.setState({ project: { devices } as unknown as ProjectConfig });
}

async function pressDelete() {
  render(<DriverPanel />);
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(useDriverBuilderStore.getState().loading).toBe(false));
  fireEvent.click(await screen.findByTitle("Delete driver"));
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listDriverDefinitions).mockResolvedValue([WIDGET]);
  useDriverBuilderStore.setState({ definitions: [WIDGET], selectedId: null, dirty: false,
    saving: false, loading: false, error: null, saveRefusal: null, changedElsewhere: null });
  useNavigationStore.setState({ activeView: "devices", pendingFocus: null });
});

describe("The Driver Builder's delete of a driver devices use", () => {
  it("names the devices instead of asking to delete, and sends nothing", async () => {
    project([
      { id: "room_a", name: "Room A Projector", driver: "acme_widget" },
      { id: "room_b", name: "Room B Display", driver: "acme_other" },
    ]);

    await pressDelete();

    const dialog = screen.getByRole("alertdialog", { name: "Driver In Use" });
    expect(dialog).toHaveTextContent("Room A Projector");
    expect(dialog).not.toHaveTextContent("Room B Display");
    expect(screen.queryByRole("alertdialog", { name: "Delete Driver" })).toBeNull();
    expect(api.deleteDriverDefinition).not.toHaveBeenCalled();
  });

  it("opens a device to choose another driver", async () => {
    project([{ id: "room_a", name: "Room A Projector", driver: "acme_widget" }]);
    await pressDelete();

    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Open Device" }));

    expect(useNavigationStore.getState().pendingFocus).toEqual({ type: "device", id: "room_a" });
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("names the devices the server refused it for, when the project changed since", async () => {
    project([]);
    vi.mocked(api.deleteDriverDefinition).mockRejectedValueOnce(new ApiError(409, JSON.stringify({
      detail: "Lobby Display uses this driver. Open the device and choose another driver first.",
      code: "driver_in_use",
      devices: [{ id: "lobby_1", name: "Lobby Display" }],
    })));
    await pressDelete();

    fireEvent.click(within(screen.getByRole("alertdialog", { name: "Delete Driver" }))
      .getByRole("button", { name: "Delete" }));

    expect(await screen.findByRole("alertdialog", { name: "Driver In Use" })).toHaveTextContent("Lobby Display");
    expect(useDriverBuilderStore.getState().error).toBeNull();
  });
});
