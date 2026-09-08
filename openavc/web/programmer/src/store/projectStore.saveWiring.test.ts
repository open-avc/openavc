import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ApiError } from "../api/errors";
import type { ProjectConfig } from "../api/types";

/**
 * The store is where the save loop's failure sentence is chosen, and the loop
 * takes it as an injected dep — so the loop's own tests pass whatever they are
 * handed and cannot tell whether the real store still hands over the real
 * formatter. This drives `save()` end to end for that one reason: a build that
 * went back to `String(e)` here would put
 * `ApiError: API 401: {"detail":"Authentication required"}` in front of the
 * user again with every other test green.
 */

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

const PROJECT = { openavc_version: "0.13.0" } as unknown as ProjectConfig;

beforeEach(() => {
  vi.useFakeTimers();
  saveProject.mockReset();
  useProjectStore.setState({
    project: PROJECT,
    etag: '"1"',
    dirty: true,
    error: null,
    conflictDetected: false,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

/** Run a save to completion, stepping past the retry backoff. */
async function save(): Promise<void> {
  const done = useProjectStore.getState().save();
  await vi.advanceTimersByTimeAsync(10_000);
  await done;
}

describe("the store's save failure message", () => {
  it("tells the user their session ended, not what was thrown", async () => {
    saveProject.mockRejectedValue(
      new ApiError(401, '{"detail":"Authentication required"}'),
    );

    await save();

    expect(useProjectStore.getState().error).toBe(
      "Your session had ended. The change is still here, so you can save it again.",
    );
  });

  it("passes any other refusal through as the server's own sentence", async () => {
    saveProject.mockRejectedValue(
      new ApiError(400, '{"detail":"Project name is required"}'),
    );

    await save();

    expect(useProjectStore.getState().error).toBe("Project name is required");
  });

  it("leaves nothing behind on a save that works", async () => {
    saveProject.mockResolvedValue({ etag: '"2"' });

    await save();

    expect(useProjectStore.getState().error).toBeNull();
    expect(useProjectStore.getState().etag).toBe('"2"');
  });
});
