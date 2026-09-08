import { describe, it, expect, vi } from "vitest";
import { ApiError } from "../api/errors";
import {
  runSaveWithRetry,
  type SaveAttemptDeps,
  type SaveStatePatch,
} from "./projectStoreSave";
import { saveFailureMessage } from "./projectStore";
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
    saveProject: vi.fn(async () => ({ etag: '"def"' })),
    isConflict: () => false,
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
    const conflict = new Error("The system restarted since this page loaded the project.");
    const { d, patches } = deps({
      saveProject: vi.fn(async () => {
        throw conflict;
      }),
      isConflict: () => true,
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
