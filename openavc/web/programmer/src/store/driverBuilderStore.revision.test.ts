import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DriverDefinition } from "../api/types";

vi.mock("../api/restClient", () => ({
  listDriverDefinitions: vi.fn(),
  createDriverDefinition: vi.fn(),
  updateDriverDefinition: vi.fn(),
}));

import * as api from "../api/restClient";
import { ApiError } from "../api/errors";
import { useDriverBuilderStore } from "./driverBuilderStore";

const CHANGED = "This driver was changed by another session since you opened it.";

function listed(name: string, revision: string): DriverDefinition {
  return {
    id: "acme_widget", name, transport: "tcp", source: "user", _revision: revision,
    default_config: {}, config_schema: {}, state_variables: {},
    commands: { power_on: { label: "On", send: "PWR ON\\r" } }, responses: [], polling: {},
  } as unknown as DriverDefinition;
}

function changedElsewhere(revision: string): ApiError {
  return new ApiError(409, JSON.stringify({ detail: CHANGED, code: "driver_changed", revision }));
}

const store = () => useDriverBuilderStore.getState();

/** acme_widget opened in the editor, as read at revision r1. */
function openedAtR1() {
  useDriverBuilderStore.setState({
    definitions: [listed("Acme Widget", "r1")], selectedId: null, dirty: false,
    saving: false, loading: false, error: null, saveRefusal: null, changedElsewhere: null,
  });
  store().selectDriver("acme_widget");
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listDriverDefinitions).mockResolvedValue([listed("Acme Widget", "r1")]);
  openedAtR1();
});

describe("A driver's save names the copy it was opened at", () => {
  it("sends the revision the driver was opened at", async () => {
    vi.mocked(api.updateDriverDefinition).mockResolvedValue(
      { status: "updated", driver_id: "acme_widget", revision: "r2" },
    );
    store().updateDraft({ name: "Acme Widget 2" });

    await store().save();

    expect(api.updateDriverDefinition).toHaveBeenCalledWith(
      "acme_widget", expect.objectContaining({ name: "Acme Widget 2" }), "r1",
    );
  });

  it("names the revision its own save answered on the next save, kept typing or not", async () => {
    let finish!: () => void;
    vi.mocked(api.updateDriverDefinition).mockImplementationOnce(
      () => new Promise((resolve) => {
        finish = () => resolve({ status: "updated", driver_id: "acme_widget", revision: "r2" });
      }),
    );
    vi.mocked(api.listDriverDefinitions).mockResolvedValue([listed("Acme Widget 2", "r2")]);
    store().updateDraft({ name: "Acme Widget 2" });

    const saving = store().save();
    store().updateDraft({ name: "Acme Widget 3" });
    finish();
    await saving;

    expect(store().revision).toBe("r2");
    expect(store().dirty).toBe(true);
    vi.mocked(api.updateDriverDefinition).mockResolvedValue(
      { status: "updated", driver_id: "acme_widget", revision: "r3" },
    );
    await store().save();
    expect(vi.mocked(api.updateDriverDefinition).mock.calls[1][2]).toBe("r2");
  });

  it("sends no revision for a new driver", async () => {
    vi.mocked(api.createDriverDefinition).mockResolvedValue(
      { status: "created", driver_id: "acme_gadget", revision: "g1" },
    );
    store().newDriver();
    store().updateDraft({ id: "acme_gadget", name: "Acme Gadget" });

    await store().save();

    expect(api.updateDriverDefinition).not.toHaveBeenCalled();
    expect(store().revision).toBe("g1");
  });
});

describe("Another session changes the open driver", () => {
  it("reloads a driver with no unsaved edit", async () => {
    vi.mocked(api.listDriverDefinitions).mockResolvedValue([listed("Their Widget", "r2")]);

    await store().loadDefinitions();

    expect(store().draft.name).toBe("Their Widget");
    expect(store().revision).toBe("r2");
    expect(store().dirty).toBe(false);
  });

  it("leaves a driver with an unsaved edit as it is", async () => {
    store().updateDraft({ name: "My Widget" });
    vi.mocked(api.listDriverDefinitions).mockResolvedValue([listed("Their Widget", "r2")]);

    await store().loadDefinitions();

    expect(store().draft.name).toBe("My Widget");
    expect(store().revision).toBe("r1");
    expect(store().dirty).toBe(true);
  });

  it("does not reload while this tab's own save is under way", async () => {
    let finish!: () => void;
    vi.mocked(api.updateDriverDefinition).mockImplementationOnce(
      () => new Promise((resolve) => {
        finish = () => resolve({ status: "updated", driver_id: "acme_widget", revision: "r2" });
      }),
    );
    vi.mocked(api.listDriverDefinitions).mockResolvedValue([listed("Acme Widget", "r2")]);
    useDriverBuilderStore.setState({ addedConnectionFields: ["host"] });

    const saving = store().save();
    await store().loadDefinitions();
    const draftDuringSave = store().draft;
    finish();
    await saving;

    expect(store().draft).toBe(draftDuringSave);
    expect(store().revision).toBe("r2");
  });
});

describe("A save refused because another session saved first", () => {
  async function refusedSave() {
    vi.mocked(api.updateDriverDefinition).mockRejectedValueOnce(changedElsewhere("r2"));
    store().updateDraft({ name: "My Widget" });
    await store().save();
  }

  it("offers the choice, keeping the edit and saying nothing else", async () => {
    await refusedSave();

    expect(store().changedElsewhere).toEqual({ message: CHANGED, revision: "r2" });
    expect(store().draft.name).toBe("My Widget");
    expect(store().dirty).toBe(true);
    expect(store().error).toBeNull();
    expect(store().saving).toBe(false);
  });

  it("saves mine over it naming the revision the refusal answered", async () => {
    await refusedSave();
    vi.mocked(api.updateDriverDefinition).mockResolvedValue(
      { status: "updated", driver_id: "acme_widget", revision: "r3" },
    );
    vi.mocked(api.listDriverDefinitions).mockResolvedValue([listed("My Widget", "r3")]);

    await store().saveMineOverIt();

    expect(vi.mocked(api.updateDriverDefinition).mock.calls[1]).toEqual([
      "acme_widget", expect.objectContaining({ name: "My Widget" }), "r2",
    ]);
    expect(store().changedElsewhere).toBeNull();
    expect(store().dirty).toBe(false);
    expect(store().revision).toBe("r3");
  });

  it("reloads theirs, discarding the edit", async () => {
    await refusedSave();
    vi.mocked(api.listDriverDefinitions).mockResolvedValue([listed("Their Widget", "r2")]);

    await store().reloadTheirs();

    expect(store().changedElsewhere).toBeNull();
    expect(store().draft.name).toBe("Their Widget");
    expect(store().dirty).toBe(false);
    expect(store().revision).toBe("r2");
  });

  it("keeps editing", async () => {
    await refusedSave();

    store().keepEditing();

    expect(store().changedElsewhere).toBeNull();
    expect(store().draft.name).toBe("My Widget");
    expect(store().dirty).toBe(true);
    expect(store().revision).toBe("r1");
  });

  it("is another refusal's error when the code is not the stale copy's", async () => {
    vi.mocked(api.updateDriverDefinition).mockRejectedValueOnce(new ApiError(
      409, JSON.stringify({ detail: 'Another driver named "acme_gadget" already exists.' }),
    ));
    store().updateDraft({ name: "My Widget" });

    await store().save();

    expect(store().changedElsewhere).toBeNull();
    expect(store().error).toBe('Another driver named "acme_gadget" already exists.');
  });
});

describe("An import over a saved driver", () => {
  it("names no revision when it needs fixes first, whichever driver was open", async () => {
    const gadget = { ...listed("Acme Gadget", "g1"), id: "acme_gadget" } as DriverDefinition;
    useDriverBuilderStore.setState({ definitions: [listed("Acme Widget", "r1"), gadget] });
    vi.mocked(api.updateDriverDefinition).mockResolvedValue(
      { status: "updated", driver_id: "acme_gadget", revision: "g2" },
    );
    const needsFixes = { ...gadget, transport: "" } as DriverDefinition;

    await store().importDriver(needsFixes, true);
    expect(api.updateDriverDefinition).not.toHaveBeenCalled();
    store().updateDraft({ transport: "tcp", name: "Acme Gadget 2" });
    await store().save();

    expect(api.updateDriverDefinition).toHaveBeenCalledWith(
      "acme_gadget", expect.objectContaining({ name: "Acme Gadget 2" }), null,
    );
  });
});
