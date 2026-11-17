import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ProjectConfig, UIPage } from "../api/types";

// Reload Project (the conflict banner's answer, and what an import or a
// backup restore ends with) replaces the tab's project with the server's and
// drops the project store's undo history. The UI Builder keeps a history of
// its own on the same project, and it goes with it: an entry made on the
// project the reload threw away would write that project's page back over
// the one just loaded.

const { getProject, saveProject } = vi.hoisted(() => ({
  getProject: vi.fn(),
  saveProject: vi.fn(),
}));

vi.mock("../api/restClient", () => ({ getProject, saveProject }));

const { useProjectStore } = await import("./projectStore");
const { useUIBuilderStore } = await import("./uiBuilderStore");

const page = (id: string) => ({ id, name: id, elements: [] }) as unknown as UIPage;

const projectWith = (pageId: string): ProjectConfig =>
  ({
    openavc_version: "0.17.0",
    project: { name: "Lobby" },
    devices: [],
    macros: [],
    variables: [],
    ui: { pages: [page(pageId)], settings: { theme: "dark" } },
  }) as unknown as ProjectConfig;

const ON_THE_SERVER = projectWith("server");

beforeEach(() => {
  vi.useFakeTimers();
  getProject.mockReset();
  saveProject.mockReset();
  getProject.mockResolvedValue({ ...ON_THE_SERVER, _etag: '"r2"' });
  useProjectStore.setState({
    project: projectWith("before"),
    etag: '"r1"',
    dirty: false,
    saving: false,
    savePending: false,
    loading: false,
    error: null,
    loadError: null,
    conflictDetected: false,
    undoStack: [],
    redoStack: [],
  });
  useUIBuilderStore.setState({ undoStack: [], redoStack: [], editRun: null });
});

afterEach(() => {
  vi.useRealTimers();
});

/** A Builder edit the way the Builder makes one: the history entry first, then the project. */
function editInTheBuilder(pageId: string) {
  const { project } = useProjectStore.getState();
  useUIBuilderStore.getState().pushUndo({ pages: project!.ui.pages }, "Edit page");
  useProjectStore.getState().update({ ui: { ...project!.ui, pages: [page(pageId)] } });
}

describe("the UI Builder's undo across a forced reload", () => {
  it("has nothing to put back over the project the reload loaded", async () => {
    editInTheBuilder("edited");
    useUIBuilderStore.setState({
      redoStack: [{ description: "Edit page", snapshot: { pages: [page("older")] } }],
    });

    await useProjectStore.getState().forceReload();
    useUIBuilderStore.getState().undo();
    useUIBuilderStore.getState().redo();

    expect(useProjectStore.getState().project).toEqual(ON_THE_SERVER);
    expect(useProjectStore.getState().dirty).toBe(false);
    expect(useUIBuilderStore.getState().undoStack).toEqual([]);
    expect(useUIBuilderStore.getState().redoStack).toEqual([]);
  });

  it("ends a run of quick edits, so the next edit is undoable on its own", async () => {
    const { project } = useProjectStore.getState();
    useUIBuilderStore.getState().joinUndo({ pages: project!.ui.pages }, "Move element");

    await useProjectStore.getState().forceReload();
    useUIBuilderStore.getState().joinUndo({ pages: ON_THE_SERVER.ui.pages }, "Move element");

    expect(useUIBuilderStore.getState().undoStack).toHaveLength(1);
    expect(useUIBuilderStore.getState().undoStack[0].snapshot).toEqual({ pages: ON_THE_SERVER.ui.pages });
  });

  it("is kept when the reload could not get the project", async () => {
    editInTheBuilder("edited");
    getProject.mockRejectedValue(new TypeError("Failed to fetch"));

    await useProjectStore.getState().forceReload();

    expect(useUIBuilderStore.getState().undoStack).toHaveLength(1);
  });

  it("is kept by a plain load, whose refetch decides it by what moved", async () => {
    useUIBuilderStore.getState().pushUndo({ pages: [page("before")] }, "Edit page");

    expect(await useProjectStore.getState().load()).toBe(true);

    expect(useUIBuilderStore.getState().undoStack).toHaveLength(1);
  });
});
