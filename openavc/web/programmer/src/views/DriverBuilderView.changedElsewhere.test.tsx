import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { DriverDefinition } from "../api/types";

vi.mock("../api/restClient", () => ({
  listDriverDefinitions: vi.fn(),
  createDriverDefinition: vi.fn(),
  updateDriverDefinition: vi.fn(),
}));
vi.mock("../api/driverClient", () => ({ validateDriverDefinition: vi.fn() }));
vi.mock("../components/driver-builder/InstalledDriversView", () => ({ InstalledDriversView: () => null }));
vi.mock("../components/driver-builder/CommunityBrowser", () => ({ CommunityBrowser: () => null }));
vi.mock("../components/driver-builder/DriverEditor", () => ({
  DriverEditor: ({ onSave }: { onSave: () => void }) => <button onClick={onSave}>Save</button>,
}));

import * as api from "../api/restClient";
import { ApiError } from "../api/errors";
import { validateDriverDefinition } from "../api/driverClient";
import { useDriverBuilderStore } from "../store/driverBuilderStore";
import { DriverPanel } from "./DriverBuilderView";

const CHANGED = "This driver was changed by another session since you opened it.";

function listed(name: string, revision: string): DriverDefinition {
  return {
    id: "acme_widget", name, transport: "tcp", source: "user", _revision: revision,
    default_config: {}, config_schema: {}, state_variables: {}, commands: {}, responses: [], polling: {},
  } as unknown as DriverDefinition;
}

async function refusedSave() {
  render(<DriverPanel />);
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(useDriverBuilderStore.getState().loading).toBe(false));
  useDriverBuilderStore.getState().selectDriver("acme_widget");
  useDriverBuilderStore.getState().updateDraft({ name: "My Widget" });
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  return screen.findByRole("alertdialog", { name: "Changed in Another Session" });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listDriverDefinitions).mockResolvedValue([listed("Acme Widget", "r1")]);
  vi.mocked(validateDriverDefinition).mockResolvedValue({ issues: [] });
  vi.mocked(api.updateDriverDefinition).mockRejectedValueOnce(new ApiError(
    409, JSON.stringify({ detail: CHANGED, code: "driver_changed", revision: "r2" }),
  ));
  useDriverBuilderStore.setState({ definitions: [listed("Acme Widget", "r1")], selectedId: null,
    dirty: false, saving: false, loading: false, error: null, saveRefusal: null, changedElsewhere: null });
});

describe("A Builder save from a copy another session saved over", () => {
  it("says so and offers reload theirs, save mine over it and keep editing", async () => {
    const dialog = await refusedSave();

    expect(dialog).toHaveTextContent(CHANGED);
    for (const name of ["Reload Theirs", "Save Mine Over It", "Keep Editing"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("saves mine over theirs naming the revision saved now", async () => {
    await refusedSave();
    vi.mocked(api.updateDriverDefinition).mockResolvedValue(
      { status: "updated", driver_id: "acme_widget", revision: "r3" },
    );

    fireEvent.click(screen.getByRole("button", { name: "Save Mine Over It" }));

    await waitFor(() => expect(api.updateDriverDefinition).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.updateDriverDefinition).mock.calls[1][2]).toBe("r2");
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("keeps editing with the edit in place", async () => {
    await refusedSave();

    fireEvent.click(screen.getByRole("button", { name: "Keep Editing" }));

    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(useDriverBuilderStore.getState().draft.name).toBe("My Widget");
    expect(api.updateDriverDefinition).toHaveBeenCalledTimes(1);
  });
});
