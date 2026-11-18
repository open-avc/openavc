import { describe, it, expect, vi } from "vitest";
import { ApiError, SERVER_NOT_ANSWERING } from "../api/errors";
import {
  runSaveWithRetry,
  type SaveAttemptDeps,
  type SaveStatePatch,
} from "./projectStoreSave";
import { saveFailureKind, saveFailureMessage } from "./projectStore";
import { ConflictError } from "../api/projectClient";
import type { ProjectConfig } from "../api/types";

/**
 * The save loop's failure message is what the UI Builder renders as
 * "Save failed: <this>", so it is the whole of what a person is told when
 * their work does not reach the server. It used to be `String(e)`, which
 * printed `ApiError: API 401: {"detail":"Authentication required"}`.
 */

const PROJECT = { openavc_version: "0.13.0" } as unknown as ProjectConfig;

function deps(over: Partial<SaveAttemptDeps> = {}): {
  d: SaveAttemptDeps;
  patches: SaveStatePatch[];
} {
  const patches: SaveStatePatch[] = [];
  const d: SaveAttemptDeps = {
    getProject: () => PROJECT,
    getEtag: () => '"abc"',
    getGeneration: () => 0,
    newOperationId: () => "op-1",
    saveProject: vi.fn(async () => ({ etag: '"def"' })),
    classify: saveFailureKind,
    conflictMessage: (e) => (e as Error).message,
    failureMessage: saveFailureMessage,
    setState: (patch) => patches.push(patch),
    sleep: async () => {},
    maxRetries: 0,
    ...over,
  };
  return { d, patches };
}

/** The state patch the loop wrote when it gave up. */
function failurePatch(patches: SaveStatePatch[]): SaveStatePatch | undefined {
  return patches.find((p) => p.saving === false && typeof p.error === "string");
}

describe("saveFailureMessage", () => {
  it("gives an expired session its own sentence", () => {
    const message = saveFailureMessage(
      new ApiError(401, '{"detail":"Authentication required"}'),
    );
    expect(message).toBe(
      "Your session had ended. The change is still here, so you can save it again.",
    );
  });

  it("never leaks the transport envelope", () => {
    const message = saveFailureMessage(
      new ApiError(401, '{"detail":"Authentication required"}'),
    );
    expect(message).not.toContain("ApiError");
    expect(message).not.toContain("401");
    expect(message).not.toContain("{");
  });

  it("passes any other refusal through as the server's own sentence", () => {
    expect(
      saveFailureMessage(new ApiError(400, '{"detail":"Project name is required"}')),
    ).toBe("Project name is required");
  });

  it("shows a change the running system could not take in the server's words", () => {
    const sentence =
      "Not applied: the project file could not be written. The disk is full. " +
      "Free up disk space and try again. The previous project is still running.";
    const message = saveFailureMessage(
      new ApiError(500, JSON.stringify({ detail: sentence, code: "not_applied" })),
    );
    expect(message).toBe(sentence);
  });

  it("keeps a plain Error's message", () => {
    expect(saveFailureMessage(new Error("Failed to fetch"))).toBe("Failed to fetch");
  });
});

describe("runSaveWithRetry", () => {
  it("reports an expired session in words, not as a thrown value", async () => {
    const { d, patches } = deps({
      saveProject: vi.fn(async () => {
        throw new ApiError(401, '{"detail":"Authentication required"}');
      }),
    });

    expect(await runSaveWithRetry(d)).toBe("failed");
    expect(failurePatch(patches)?.error).toBe(
      "Your session had ended. The change is still here, so you can save it again.",
    );
  });

  it("leaves the conflict path alone", async () => {
    const conflict = new ConflictError("The system restarted since this page loaded the project.");
    const { d, patches } = deps({
      saveProject: vi.fn(async () => {
        throw conflict;
      }),
    });

    expect(await runSaveWithRetry(d)).toBe("conflict");
    const patch = patches.find((p) => p.conflictDetected);
    expect(patch?.error).toBe(
      "The system restarted since this page loaded the project.",
    );
  });

  it("still saves, so a build that fails every write fails this too", async () => {
    const { d, patches } = deps();
    expect(await runSaveWithRetry(d)).toBe("saved");
    expect(patches.some((p) => p.error)).toBe(false);
    expect(patches.at(-1)).toMatchObject({ saving: false, dirty: false, etag: '"def"' });
  });
});

describe("a save waiting to be tried again", () => {
  const noAnswer = () => vi.fn(async () => {
    throw new TypeError("Failed to fetch");
  });

  it("waits in a state of its own, never written as a failure", async () => {
    const { d, patches } = deps({ maxRetries: 2, saveProject: noAnswer() });

    expect(await runSaveWithRetry(d)).toBe("failed");
    expect(patches.filter((p) => p.saveRetryIn != null).map((p) => p.saveRetryIn)).toEqual([1, 2]);
    // The failure is said once, when the tries are spent.
    expect(patches.filter((p) => typeof p.error === "string").map((p) => p.error)).toEqual([
      SERVER_NOT_ANSWERING,
    ]);
    expect(patches.at(-1)).toMatchObject({ saveRetryIn: null, saving: false });
  });

  it("clears the wait when the try after it saves", async () => {
    let calls = 0;
    const { d, patches } = deps({
      maxRetries: 2,
      saveProject: vi.fn(async () => {
        calls += 1;
        if (calls === 1) throw new TypeError("Failed to fetch");
        return { etag: '"def"' };
      }),
    });

    expect(await runSaveWithRetry(d)).toBe("saved");
    expect(patches.some((p) => typeof p.error === "string")).toBe(false);
    expect(patches.at(-1)).toMatchObject({ saveRetryIn: null, saving: false, dirty: false });
  });
});

const NOT_APPLIED =
  "Not applied: the running system could not take the change. The Programmer's Log has the details. " +
  "The previous project is still running.";

const answered = (status: number, detail: string, code?: string) =>
  new ApiError(status, JSON.stringify(code ? { detail, code } : { detail }));

describe("saveFailureKind", () => {
  it("reads the answer's code, not its status", () => {
    expect(saveFailureKind(new ConflictError("Another session saved"))).toBe("conflict");
    expect(saveFailureKind(answered(409, "Refused", "refused"))).toBe("final");
    expect(saveFailureKind(answered(422, "Unknown word", "refused"))).toBe("final");
    expect(saveFailureKind(answered(500, NOT_APPLIED, "not_applied"))).toBe("not_taken");
  });

  it("tries again what said nothing was taken in, and what said nothing at all", () => {
    expect(saveFailureKind(answered(503, "Server is starting up"))).toBe("not_taken");
    expect(saveFailureKind(new TypeError("Failed to fetch"))).toBe("unknown");
    expect(saveFailureKind(new ApiError(502, "Bad Gateway"))).toBe("unknown");
    expect(saveFailureKind(new ApiError(500, "Internal Server Error"))).toBe("unknown");
  });

  it("never tries again an answer that will be the same", () => {
    expect(saveFailureKind(answered(401, "Authentication required"))).toBe("final");
    expect(saveFailureKind(answered(422, "Invalid project configuration"))).toBe("final");
    expect(saveFailureKind(answered(413, "Project file too large (max 10 MB)"))).toBe("final");
    // A fault in the page itself, not an answer.
    expect(saveFailureKind(new TypeError("x is undefined"))).toBe("final");
  });
});

describe("which failed saves are tried again", () => {
  const failing = (...errors: unknown[]) => {
    let call = 0;
    return vi.fn(async () => {
      const error = errors[Math.min(call, errors.length - 1)];
      call += 1;
      if (error === "saved") return { etag: '"def"' };
      throw error;
    });
  };

  it("does not try a refusal of the change again, and says why", async () => {
    const save = failing(answered(409, "Refused for this reason.", "refused"));
    const { d, patches } = deps({ maxRetries: 2, saveProject: save });

    expect(await runSaveWithRetry(d)).toBe("failed");
    expect(save).toHaveBeenCalledTimes(1);
    expect(patches.some((p) => p.conflictDetected)).toBe(false);
    expect(failurePatch(patches)?.error).toBe("Refused for this reason.");
  });

  it("does not try an invalid project again", async () => {
    const save = failing(answered(422, "Invalid project configuration"));
    const { d } = deps({ maxRetries: 2, saveProject: save });
    expect(await runSaveWithRetry(d)).toBe("failed");
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("tries a change that was not applied again, and stops when the answer repeats", async () => {
    const save = failing(answered(500, NOT_APPLIED, "not_applied"));
    const { d, patches } = deps({ maxRetries: 2, saveProject: save });

    expect(await runSaveWithRetry(d)).toBe("failed");
    expect(save).toHaveBeenCalledTimes(2);
    expect(failurePatch(patches)?.error).toBe(NOT_APPLIED);
  });

  it("saves a change that was not applied once and is taken the next time", async () => {
    const save = failing(answered(500, NOT_APPLIED, "not_applied"), "saved");
    const { d } = deps({ maxRetries: 2, saveProject: save });
    expect(await runSaveWithRetry(d)).toBe("saved");
    expect(save).toHaveBeenCalledTimes(2);
  });

  it("sends a save whose answer was lost again as the same save", async () => {
    let ids = 0;
    const save = failing(new TypeError("Failed to fetch"), "saved");
    const { d } = deps({
      maxRetries: 2,
      saveProject: save,
      newOperationId: () => `op-${++ids}`,
    });

    expect(await runSaveWithRetry(d)).toBe("saved");
    expect(save).toHaveBeenCalledTimes(2);
    const [first, second] = save.mock.calls as unknown as unknown[][];
    expect(second).toEqual(first);
    expect(second[0]).toBe(PROJECT);
    expect(second.slice(1)).toEqual(['"abc"', "op-1"]);
  });

  it("keeps an edit made during the wait for the save after it", async () => {
    let current: ProjectConfig = PROJECT;
    const edited = { ...PROJECT, project: { id: "p", name: "Edited" } } as unknown as ProjectConfig;
    const save = failing(new TypeError("Failed to fetch"), "saved");
    const { d, patches } = deps({
      maxRetries: 2,
      getProject: () => current,
      saveProject: save,
      sleep: async () => {
        current = edited;
      },
    });

    expect(await runSaveWithRetry(d)).toBe("saved");
    // The resend is the save whose answer was lost, not the edit.
    expect((save.mock.calls as unknown as unknown[][])[1][0]).toBe(PROJECT);
    // The edit is still unsaved, so the store saves again.
    expect(patches.at(-1)).toMatchObject({ dirty: true });
  });

  it("drops the save when the project is reloaded during the wait", async () => {
    let generation = 0;
    const save = failing(new TypeError("Failed to fetch"), "saved");
    const { d, patches } = deps({
      maxRetries: 2,
      getGeneration: () => generation,
      saveProject: save,
      sleep: async () => {
        generation += 1;
      },
    });

    expect(await runSaveWithRetry(d)).toBe("noop");
    expect(save).toHaveBeenCalledTimes(1);
    expect(patches.at(-1)).toMatchObject({ saveRetryIn: null });
  });
});

describe("the save whose answer never came", () => {
  // A tab reloaded before its save was answered keeps the save's id with its
  // unsaved project, so the next page can ask the server whether that save
  // made the current revision (store/projectDraft.ts).
  const lastOperation = (patches: SaveStatePatch[]) =>
    patches.filter((p) => "saveOperation" in p).at(-1);

  it("is named while the save is out", async () => {
    let seen: SaveStatePatch | undefined;
    const { d, patches } = deps({
      saveProject: vi.fn(async () => {
        seen = lastOperation(patches);
        return { etag: '"def"' };
      }),
    });
    await runSaveWithRetry(d);
    expect(seen).toMatchObject({ saveOperation: "op-1", saveSending: PROJECT });
  });

  it("is forgotten once the save is answered", async () => {
    const { d, patches } = deps();
    await runSaveWithRetry(d);
    expect(lastOperation(patches)).toMatchObject({ saveOperation: null, saveSending: null });
  });

  it("is forgotten once the server refuses it", async () => {
    const { d, patches } = deps({
      saveProject: vi.fn(async () => {
        throw new ApiError(422, '{"detail":"Invalid project"}');
      }),
    });
    await runSaveWithRetry(d);
    expect(lastOperation(patches)).toMatchObject({ saveOperation: null, saveSending: null });
  });

  it("is kept when the tries run out with no answer", async () => {
    const { d, patches } = deps({
      saveProject: vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    });
    expect(await runSaveWithRetry(d)).toBe("failed");
    expect(lastOperation(patches)).toMatchObject({ saveOperation: "op-1", saveSending: PROJECT });
  });
});
