import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ProjectConfig } from "../api/types";

// An unsaved project edit is kept in the tab across a reload, and offered back
// on the next load: as it was while the system still has the revision it was
// made on, as a conflict once the project has moved, and not at all once the
// system already holds it. A login typed into a device's settings is never
// written to the browser's storage with it.

const { getProject, saveProject, listDrivers } = vi.hoisted(() => ({
  getProject: vi.fn(),
  saveProject: vi.fn(),
  listDrivers: vi.fn(),
}));

vi.mock("../api/restClient", () => ({
  getProject,
  saveProject,
  listDrivers,
  ConflictError: class ConflictError extends Error {},
}));

const { useProjectStore } = await import("./projectStore");
const {
  draftOf, filledFromServer, judgeDraft, settleDraft, readDraft, startProjectDraft, useDraftOffer,
  restoreDraft, discardDraft, draftKey,
} = await import("./projectDraft");
const { ConflictError } = await import("../api/restClient");

const SAVED = "<saved on this system>";
const TYPED = "hunter22-typed";

const LOBBY_DRIVERS = [
  {
    id: "acme_widget",
    name: "Acme Widget",
    config_schema: {
      host: { type: "string" },
      access_code: { type: "string", secret: true },
      label: { type: "string" },
    },
  },
];

function lobby(overrides: Partial<ProjectConfig> = {}): ProjectConfig {
  return {
    openavc_version: "0.17.0",
    project: { id: "lobby", name: "Lobby" },
    devices: [
      { id: "widget1", driver: "acme_widget", name: "Widget One", config: { label: "A", access_code: SAVED } },
    ],
    connections: { widget1: { host: "10.0.0.5", password: SAVED } },
    plugins: {},
    macros: [],
    variables: [],
    ...overrides,
  } as unknown as ProjectConfig;
}

/** The lobby with a second widget added in this tab, logins typed. */
function withTypedWidget(base = lobby()): ProjectConfig {
  return {
    ...base,
    devices: [
      ...base.devices,
      { id: "widget2", driver: "acme_widget", name: "Widget Two", config: { label: "B", access_code: TYPED } },
    ],
    connections: { ...base.connections, widget2: { host: "10.0.0.6", password: TYPED } },
  } as ProjectConfig;
}

const knownSecrets = (driver: string) =>
  driver === "acme_widget" ? new Set(["password", "access_code"]) : null;

function storedText(): string {
  return sessionStorage.getItem(draftKey()) ?? "";
}

beforeEach(() => {
  vi.useFakeTimers();
  sessionStorage.clear();
  getProject.mockReset();
  saveProject.mockReset();
  listDrivers.mockReset();
  listDrivers.mockResolvedValue(LOBBY_DRIVERS);
  useDraftOffer.setState({ offer: null });
  useProjectStore.setState({
    project: null,
    etag: null,
    revision: null,
    dirty: false,
    saving: false,
    savePending: false,
    saveRetryIn: null,
    loading: false,
    error: null,
    loadError: null,
    conflictDetected: false,
    loadGeneration: 0,
    undoStack: [],
    redoStack: [],
  });
});

const running: (() => void)[] = [];

/** Start keeping drafts; stopped after the test, whatever it asserts. */
function started(): () => void {
  const stop = startProjectDraft();
  running.push(stop);
  return stop;
}

afterEach(() => {
  running.splice(0).forEach((stop) => stop());
  vi.useRealTimers();
});

describe("what a draft keeps of a login", () => {
  it("leaves a typed login out and keeps the saved-login marker", () => {
    const draft = draftOf(withTypedWidget(), '"b-1"', "b-1", knownSecrets);
    const text = JSON.stringify(draft);
    expect(text).not.toContain(TYPED);
    expect(draft.project.devices[0].config.access_code).toBe(SAVED);
    expect(draft.project.connections.widget1.password).toBe(SAVED);
    expect(draft.project.devices[1].config.label).toBe("B");
    expect(draft.project.connections.widget2.host).toBe("10.0.0.6");
    expect(draft.blanked).toEqual(
      expect.arrayContaining([
        { deviceId: "widget2", holder: "config", field: "access_code" },
        { deviceId: "widget2", holder: "connection", field: "password" },
      ]),
    );
  });

  it("keeps none of a device's values when its driver's settings are not known", () => {
    const project = lobby({
      devices: [{ id: "mystery", driver: "not_here", name: "Mystery", config: { code: "4417", label: "M" } }],
      connections: { mystery: { host: "10.0.0.9", port: 23 } },
    } as Partial<ProjectConfig>);
    const draft = draftOf(project, '"b-1"', "b-1", () => null);
    const text = JSON.stringify(draft);
    expect(text).not.toContain("4417");
    expect(text).not.toContain("10.0.0.9");
  });

  it("fills each value left out from the system's copy, where it has one", () => {
    const server = lobby();
    const draft = draftOf(withTypedWidget(server), '"b-1"', "b-1", () => null);
    const filled = filledFromServer(draft, server);
    expect(filled.devices[0].config).toEqual(server.devices[0].config);
    expect(filled.connections.widget1).toEqual(server.connections.widget1);
    // The new device is not on the system: its values stay empty.
    expect(filled.connections.widget2.host).toBe("");
  });
});

describe("judging a draft against the system's copy", () => {
  it("is current while the system has the revision it was made on", () => {
    const draft = draftOf(withTypedWidget(), '"b-1"', "b-1", knownSecrets);
    expect(judgeDraft(draft, { project: lobby(), revision: "b-1" })).toBe("current");
  });

  it("is stale once the system's revision has moved", () => {
    const draft = draftOf(withTypedWidget(), '"b-1"', "b-1", knownSecrets);
    expect(judgeDraft(draft, { project: lobby(), revision: "b-2" })).toBe("stale");
  });

  it("is dropped when the system already holds it", () => {
    // This tab's own save landed after the draft was written: the system
    // has the edit, its login moved out to the marker.
    const edited = withTypedWidget();
    const draft = draftOf(edited, '"b-1"', "b-1", knownSecrets);
    const onSystem = withTypedWidget();
    onSystem.devices[1].config.access_code = SAVED;
    onSystem.connections.widget2.password = SAVED;
    expect(judgeDraft(draft, { project: onSystem, revision: "b-2" })).toBe("drop");
  });

  it("asks the server when its revision has moved and the draft names an unanswered save", () => {
    const draft = draftOf(withTypedWidget(), '"b-1"', "b-1", knownSecrets, { operation: "op-7", sameAsSave: true });
    expect(judgeDraft(draft, { project: lobby(), revision: "b-2" })).toBe("ask");
  });

  it("is dropped for another project", () => {
    const draft = draftOf(withTypedWidget(), '"b-1"', "b-1", knownSecrets);
    const other = lobby({ project: { id: "atrium", name: "Atrium" } } as Partial<ProjectConfig>);
    expect(judgeDraft(draft, { project: other, revision: "b-1" })).toBe("drop");
  });
});

describe("asking the server about the draft's unanswered save", () => {
  const moved = { project: lobby(), etag: '"b-2"', revision: "b-2" };

  it("drops a draft that is the save the server recognises", async () => {
    const resend = vi.fn(async () => ({}));
    const draft = draftOf(withTypedWidget(), '"b-1"', "b-1", knownSecrets, { operation: "op-7", sameAsSave: true });
    expect(await settleDraft(draft, moved, resend)).toBe("drop");
    // Sent against the draft's own revision, with the server's copy as its body.
    expect(resend).toHaveBeenCalledWith(moved.project, '"b-1"', "op-7");
  });

  it("keeps edits made after that save as current against the server's revision", async () => {
    const resend = vi.fn(async () => ({}));
    const draft = draftOf(withTypedWidget(), '"b-1"', "b-1", knownSecrets, { operation: "op-7", sameAsSave: false });
    expect(await settleDraft(draft, moved, resend)).toBe("current");
  });

  it("is stale when the server does not recognise it", async () => {
    const resend = vi.fn(async () => {
      throw new ConflictError("Project was modified by another session.");
    });
    const draft = draftOf(withTypedWidget(), '"b-1"', "b-1", knownSecrets, { operation: "op-7", sameAsSave: true });
    expect(await settleDraft(draft, moved, resend)).toBe("stale");
  });

  it("does not ask while the draft's revision is the server's", async () => {
    const resend = vi.fn(async () => ({}));
    const draft = draftOf(withTypedWidget(), '"b-1"', "b-1", knownSecrets, { operation: "op-7", sameAsSave: true });
    expect(await settleDraft(draft, { ...moved, etag: '"b-1"', revision: "b-1" }, resend)).toBe("current");
    expect(resend).not.toHaveBeenCalled();
  });
});

/** The store holding the server's copy, as a load leaves it. */
function loaded(project: ProjectConfig, revision: string): void {
  useProjectStore.setState({
    project,
    etag: `"${revision}"`,
    revision,
    dirty: false,
    loadGeneration: useProjectStore.getState().loadGeneration + 1,
  });
}

describe("the tab's draft", () => {
  it("is written without the typed login after a failed save", async () => {
    const stop = started();
    loaded(lobby(), "b-1");
    await vi.advanceTimersByTimeAsync(0);
    useProjectStore.setState({ project: withTypedWidget(), dirty: true, error: "Save failed" });
    await vi.advanceTimersByTimeAsync(5_000);
    expect(storedText()).toContain("widget2");
    expect(storedText()).not.toContain(TYPED);
    stop();
  });

  it("leaves out a setting a driver made secret since this page first asked", async () => {
    // The page's own first ask: the access code is not a secret yet.
    listDrivers.mockResolvedValue([{ ...LOBBY_DRIVERS[0], config_schema: { access_code: { type: "string" } } }]);
    const stop = started();
    loaded(lobby(), "b-1");
    await vi.advanceTimersByTimeAsync(0);
    // The driver is updated; the Add Device form reads the new list.
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ drivers: LOBBY_DRIVERS }),
      { status: 200, headers: { "content-type": "application/json" } },
    )));
    try {
      await (await import("../api/driverClient")).listDrivers();
    } finally {
      vi.unstubAllGlobals();
    }
    useProjectStore.setState({ project: withTypedWidget(), dirty: true, error: "Save failed" });
    await vi.advanceTimersByTimeAsync(5_000);
    expect(storedText()).toContain("widget2");
    expect(storedText()).not.toContain(TYPED);
    stop();
  });

  it("is written at once as the page goes away", async () => {
    const stop = started();
    loaded(lobby(), "b-1");
    await vi.advanceTimersByTimeAsync(0);
    useProjectStore.setState({ project: withTypedWidget(), dirty: true });
    window.dispatchEvent(new Event("pagehide"));
    expect(storedText()).toContain("widget2");
    stop();
  });

  it("is removed once the save lands", async () => {
    const stop = started();
    loaded(lobby(), "b-1");
    await vi.advanceTimersByTimeAsync(0);
    useProjectStore.setState({ project: withTypedWidget(), dirty: true });
    await vi.advanceTimersByTimeAsync(5_000);
    expect(readDraft()).not.toBeNull();
    useProjectStore.setState({ dirty: false, etag: '"b-2"', revision: "b-2" });
    expect(readDraft()).toBeNull();
    stop();
  });

  it("is offered back on the next load while its revision is current, and restored and saved", async () => {
    sessionStorage.setItem(draftKey(), JSON.stringify(draftOf(withTypedWidget(), '"b-1"', "b-1", knownSecrets)));
    getProject.mockImplementation(async () => ({ ...lobby(), _etag: '"b-1"' }));
    saveProject.mockResolvedValue({ etag: '"b-2"' });
    const stop = started();
    loaded(lobby(), "b-1");
    await vi.advanceTimersByTimeAsync(0);

    const offer = useDraftOffer.getState().offer;
    expect(offer?.kind).toBe("current");
    expect(offer?.reenter).toEqual(["Widget Two"]);

    await restoreDraft();
    await vi.advanceTimersByTimeAsync(5_000);
    const store = useProjectStore.getState();
    expect(store.project?.devices.map((d) => d.id)).toEqual(["widget1", "widget2"]);
    expect(saveProject).toHaveBeenCalledTimes(1);
    expect(saveProject.mock.calls[0][1]).toBe('"b-1"');
    expect(store.dirty).toBe(false);
    expect(useDraftOffer.getState().offer).toBeNull();
    expect(readDraft()).toBeNull();
    stop();
  });

  it("is offered as a conflict once the project has moved, and never saved over it", async () => {
    sessionStorage.setItem(draftKey(), JSON.stringify(draftOf(withTypedWidget(), '"b-1"', "b-1", knownSecrets)));
    getProject.mockImplementation(async () => ({ ...lobby(), _etag: '"c-4"' }));
    const stop = started();
    loaded(lobby(), "c-4");
    await vi.advanceTimersByTimeAsync(0);

    expect(useDraftOffer.getState().offer?.kind).toBe("stale");
    await restoreDraft();
    await vi.advanceTimersByTimeAsync(5_000);
    const store = useProjectStore.getState();
    expect(store.project?.devices.map((d) => d.id)).toEqual(["widget1", "widget2"]);
    expect(store.conflictDetected).toBe(true);
    expect(store.etag).toBe('"b-1"');
    expect(saveProject).not.toHaveBeenCalled();
    stop();
  });

  it("is not offered when the server recognises it as its own landed save, and is removed", async () => {
    const draft = draftOf(withTypedWidget(), '"b-1"', "b-1", knownSecrets, { operation: "op-7", sameAsSave: true });
    sessionStorage.setItem(draftKey(), JSON.stringify(draft));
    saveProject.mockResolvedValue({ etag: '"b-2"' });
    // The server's copy of that save: its login moved out, its defaults filled in.
    const onSystem = withTypedWidget();
    onSystem.devices[1] = { ...onSystem.devices[1], enabled: true, config: { label: "B", access_code: SAVED } };
    onSystem.connections.widget2 = { host: "10.0.0.6", password: SAVED };
    const stop = started();
    loaded(onSystem, "b-2");
    await vi.advanceTimersByTimeAsync(0);
    expect(saveProject).toHaveBeenCalledWith(expect.anything(), '"b-1"', "edit", "op-7");
    expect(useDraftOffer.getState().offer).toBeNull();
    expect(readDraft()).toBeNull();
    stop();
  });

  it("restores edits made after a save the server recognises, saved against the server's revision", async () => {
    const draft = draftOf(withTypedWidget(), '"b-1"', "b-1", knownSecrets, { operation: "op-7", sameAsSave: false });
    sessionStorage.setItem(draftKey(), JSON.stringify(draft));
    const onSystem = { ...lobby(), devices: [{ ...lobby().devices[0], enabled: true }] } as ProjectConfig;
    getProject.mockImplementation(async () => ({ ...structuredClone(onSystem), _etag: '"b-2"' }));
    saveProject.mockResolvedValue({ etag: '"b-3"' });
    const stop = started();
    loaded(onSystem, "b-2");
    await vi.advanceTimersByTimeAsync(0);
    expect(useDraftOffer.getState().offer?.kind).toBe("current");

    await restoreDraft();
    await vi.advanceTimersByTimeAsync(5_000);
    const [project, etag, , operation] = saveProject.mock.calls.at(-1)!;
    expect(etag).toBe('"b-2"');
    expect(operation).not.toBe("op-7");
    expect((project as ProjectConfig).devices.map((d) => d.id)).toEqual(["widget1", "widget2"]);
    expect(useProjectStore.getState().dirty).toBe(false);
    stop();
  });

  it("restores as a conflict when another session saves while the offer is open", async () => {
    sessionStorage.setItem(draftKey(), JSON.stringify(draftOf(withTypedWidget(), '"b-1"', "b-1", knownSecrets)));
    const stop = started();
    loaded(lobby(), "b-1");
    await vi.advanceTimersByTimeAsync(0);
    expect(useDraftOffer.getState().offer?.kind).toBe("current");

    getProject.mockImplementation(async () => ({ ...lobby(), _etag: '"b-2"' }));
    await restoreDraft();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(saveProject).not.toHaveBeenCalled();
    expect(useProjectStore.getState().conflictDetected).toBe(true);
    expect(useProjectStore.getState().etag).toBe('"b-1"');
    stop();
  });

  it("names the save out when it is written", async () => {
    const stop = started();
    loaded(lobby(), "b-1");
    await vi.advanceTimersByTimeAsync(0);
    const edited = withTypedWidget();
    useProjectStore.setState({ project: edited, dirty: true, saving: true, saveOperation: "op-9", saveSending: edited });
    await vi.advanceTimersByTimeAsync(5_000);
    expect(readDraft()).toMatchObject({ operation: "op-9", sameAsSave: true, revision: "b-1" });
    stop();
  });

  it("is not offered when the system already holds it, and is removed", async () => {
    sessionStorage.setItem(draftKey(), JSON.stringify(draftOf(lobby(), '"b-1"', "b-1", knownSecrets)));
    const stop = started();
    loaded(lobby(), "c-4");
    await vi.advanceTimersByTimeAsync(0);
    expect(useDraftOffer.getState().offer).toBeNull();
    expect(readDraft()).toBeNull();
    stop();
  });

  it("is kept while the offer is open, and removed when it is discarded", async () => {
    sessionStorage.setItem(draftKey(), JSON.stringify(draftOf(withTypedWidget(), '"b-1"', "b-1", knownSecrets)));
    const stop = started();
    loaded(lobby(), "b-1");
    await vi.advanceTimersByTimeAsync(5_000);
    expect(readDraft()).not.toBeNull();
    discardDraft();
    expect(readDraft()).toBeNull();
    expect(useProjectStore.getState().project?.devices).toHaveLength(1);
    stop();
  });
});
