import { describe, it, expect, vi, beforeEach } from "vitest";
import type { ProjectConfig } from "../api/types";

// A failed project load is its own failure, said in its own words: it never
// lands in `error`, which is the save's (and the version conflict's), so a
// load that failed is never shown as "Save failed". Retry gets the project
// whatever state the tab is in, unsaved edits included.

const { saveProject, getProject } = vi.hoisted(() => ({
  saveProject: vi.fn(),
  getProject: vi.fn(),
}));

vi.mock("../api/restClient", () => ({
  saveProject,
  getProject,
  ConflictError: class ConflictError extends Error {},
}));

const { useProjectStore } = await import("./projectStore");
const { SERVER_NOT_ANSWERING } = await import("../api/errors");

const PROJECT = { openavc_version: "0.13.0", project: { name: "Old" } } as unknown as ProjectConfig;
const SERVER_COPY = { openavc_version: "0.13.0", project: { name: "New" }, _etag: '"2"' };
const noAnswer = () => Promise.reject(new TypeError("Failed to fetch"));

beforeEach(() => {
  saveProject.mockReset();
  getProject.mockReset();
  useProjectStore.setState({
    project: PROJECT,
    etag: '"1"',
    dirty: false,
    saving: false,
    loading: false,
    error: null,
    loadError: null,
    conflictDetected: false,
  });
});

describe("a failed project load", () => {
  it.each(["load", "forceReload"] as const)("is the load's own failure after %s, never the save's", async (method) => {
    getProject.mockImplementation(noAnswer);
    await useProjectStore.getState()[method]();
    expect(useProjectStore.getState().loadError).toBe(SERVER_NOT_ANSWERING);
    expect(useProjectStore.getState().error).toBeNull();
    expect(useProjectStore.getState().loading).toBe(false);
  });

  it("after Reload Project on a version conflict, never leaves the conflict's sentence to read as a failed save", async () => {
    useProjectStore.setState({ error: "Another session saved", conflictDetected: true, dirty: true });
    getProject.mockImplementation(noAnswer);
    await useProjectStore.getState().forceReload();
    expect(useProjectStore.getState().error).toBeNull();
    expect(useProjectStore.getState().conflictDetected).toBe(false);
    expect(useProjectStore.getState().loadError).toBe(SERVER_NOT_ANSWERING);
  });

  it("is cleared by the next load that gets the project", async () => {
    getProject.mockImplementation(noAnswer);
    await useProjectStore.getState().load();
    getProject.mockResolvedValue({ ...SERVER_COPY });
    await useProjectStore.getState().load();
    expect(useProjectStore.getState().loadError).toBeNull();
    expect(useProjectStore.getState().project?.project.name).toBe("New");
  });
});

describe("retrying a failed load", () => {
  it("gets the project when nothing is unsaved", async () => {
    useProjectStore.setState({ loadError: SERVER_NOT_ANSWERING });
    getProject.mockResolvedValue({ ...SERVER_COPY });
    await useProjectStore.getState().retryLoad();
    expect(useProjectStore.getState().loadError).toBeNull();
    expect(useProjectStore.getState().project?.project.name).toBe("New");
    expect(saveProject).not.toHaveBeenCalled();
  });

  it("saves the unsaved edits first, then gets the project, never dropping them", async () => {
    const edited = { ...PROJECT, project: { name: "Edited" } } as unknown as ProjectConfig;
    useProjectStore.setState({ project: edited, dirty: true, loadError: SERVER_NOT_ANSWERING });
    saveProject.mockResolvedValue({ etag: '"3"' });
    getProject.mockResolvedValue({ ...edited, _etag: '"3"' });
    await useProjectStore.getState().retryLoad();
    expect(saveProject).toHaveBeenCalledWith(edited, '"1"');
    expect(getProject).toHaveBeenCalledTimes(1);
    expect(useProjectStore.getState().loadError).toBeNull();
    expect(useProjectStore.getState().dirty).toBe(false);
  });

  it("keeps the edits and the line when the save fails too", async () => {
    vi.useFakeTimers();
    try {
      const edited = { ...PROJECT, project: { name: "Edited" } } as unknown as ProjectConfig;
      useProjectStore.setState({ project: edited, dirty: true, loadError: SERVER_NOT_ANSWERING });
      saveProject.mockImplementation(noAnswer);
      const retry = useProjectStore.getState().retryLoad();
      await vi.runAllTimersAsync();
      await retry;
      expect(getProject).not.toHaveBeenCalled();
      expect(useProjectStore.getState().project).toBe(edited);
      expect(useProjectStore.getState().dirty).toBe(true);
      expect(useProjectStore.getState().loadError).toBe(SERVER_NOT_ANSWERING);
      expect(useProjectStore.getState().error).toBe(SERVER_NOT_ANSWERING);
    } finally {
      vi.useRealTimers();
    }
  });
});
