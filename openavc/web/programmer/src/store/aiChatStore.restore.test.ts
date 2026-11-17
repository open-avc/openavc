import { describe, it, expect, vi, beforeEach } from "vitest";
import type { ProjectConfig } from "../api/types";

// The assistant's Undo (and Revert all) writes back the project as it was
// before a reply, then reloads the tab's copy from the server. An edit the
// tab has not saved yet is not in that write, so the reload would throw it
// away without a word. The restore stops at it instead: nothing is written,
// and the chat says why.

const { getProject, saveProject, reloadProject } = vi.hoisted(() => ({
  getProject: vi.fn(),
  saveProject: vi.fn(),
  reloadProject: vi.fn(),
}));

vi.mock("../api/restClient", () => ({ getProject, saveProject, reloadProject }));

const { useAIChatStore, RESTORE_STOPPED_AT_UNSAVED } = await import("./aiChatStore");
const { useProjectStore } = await import("./projectStore");

const named = (name: string) =>
  ({ openavc_version: "0.17.0", project: { name } }) as unknown as ProjectConfig;

const BEFORE_THE_REPLY = { ...named("Before the reply"), _etag: '"r1"' };
const ON_THE_SERVER = named("After the reply");
const EDITED_IN_THE_TAB = named("Edited in the tab");

const MESSAGES = [
  { id: "u1", role: "user" as const, content: "Add a projector", createdAt: "" },
  { id: "a1", role: "assistant" as const, content: "Added it", createdAt: "" },
];
const UNDO_STACK = [{ messageId: "a1", snapshot: BEFORE_THE_REPLY }];

const CLEAN = {
  dirty: false,
  savePending: false,
  saving: false,
  saveRetryIn: null,
  error: null,
  conflictDetected: false,
};

/** Each way the tab can hold an edit the server does not have. */
const UNSAVED = {
  "waiting on its save timer": { dirty: true, savePending: true },
  "being saved": { dirty: true, saving: true },
  "waiting to be tried again": { dirty: true, saveRetryIn: 2 },
  "left by a failed save": { dirty: true, error: "The disk is full." },
  "left by a version conflict": { dirty: true, conflictDetected: true, error: "Another session saved." },
  "not yet handed to a save": { dirty: true },
};

beforeEach(() => {
  getProject.mockReset();
  saveProject.mockReset();
  reloadProject.mockReset();
  getProject.mockResolvedValue({ ...ON_THE_SERVER, _etag: '"r7"' });
  saveProject.mockResolvedValue({ status: "saved", etag: '"r8"' });
  reloadProject.mockResolvedValue({ status: "reloaded" });
  useProjectStore.setState({
    ...CLEAN,
    project: ON_THE_SERVER,
    etag: '"r7"',
    loading: false,
    loadError: null,
  });
  useAIChatStore.setState({ messages: MESSAGES, undoStack: UNDO_STACK, error: null, suggestions: [] });
});

const restores = {
  "Undo": () => useAIChatStore.getState().undoMessage("a1"),
  "Revert all": () => useAIChatStore.getState().revertAll(),
};

describe.each(Object.entries(restores))("the assistant's %s", (_name, restore) => {
  it.each(Object.entries(UNSAVED))("stops at an edit %s, writing nothing", async (_stage, unsaved) => {
    useProjectStore.setState({ project: EDITED_IN_THE_TAB, ...unsaved });

    await restore();

    expect(saveProject).not.toHaveBeenCalled();
    expect(reloadProject).not.toHaveBeenCalled();
    expect(useProjectStore.getState().project).toEqual(EDITED_IN_THE_TAB);
    expect(useProjectStore.getState().dirty).toBe(true);
    expect(useAIChatStore.getState().undoStack).toEqual(UNDO_STACK);
    expect(useAIChatStore.getState().messages).toEqual(MESSAGES);
    expect(useAIChatStore.getState().error).toBe(RESTORE_STOPPED_AT_UNSAVED);
  });

  it("writes the project back and loads it when nothing is unsaved", async () => {
    await restore();

    const { _etag: _ignored, ...body } = BEFORE_THE_REPLY;
    expect(saveProject).toHaveBeenCalledWith(body, '"r7"');
    expect(reloadProject).toHaveBeenCalled();
    expect(getProject).toHaveBeenCalledTimes(2);
    expect(useProjectStore.getState().etag).toBe('"r7"');
    expect(useAIChatStore.getState().undoStack).toEqual([]);
    expect(useAIChatStore.getState().error).toBeNull();
  });

  it("keeps an edit made while the write was on its way, rather than reloading over it", async () => {
    saveProject.mockImplementation(async () => {
      useProjectStore.setState({ project: EDITED_IN_THE_TAB, dirty: true, savePending: true });
      return { status: "saved", etag: '"r8"' };
    });

    await restore();

    expect(saveProject).toHaveBeenCalledTimes(1);
    expect(getProject).toHaveBeenCalledTimes(1);
    expect(useProjectStore.getState().project).toEqual(EDITED_IN_THE_TAB);
    expect(useProjectStore.getState().dirty).toBe(true);
    // The write went out: the reply is undone on the server whatever the tab
    // does next, and the edit's own save then meets it as a conflict.
    expect(useAIChatStore.getState().undoStack).toEqual([]);
  });
});
