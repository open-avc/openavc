import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, parseApiError } from "../api/errors";
import { ConflictError, saveProject } from "../api/projectClient";
import type { ProjectConfig } from "../api/types";
import { importParsedProject } from "./projectImport";

vi.hoisted(() => { window.history.replaceState(null, "", "/programmer/"); });

const project = { openavc_version: "0.14.0", project: { id: "imported", name: "Imported" } } as ProjectConfig;

afterEach(() => vi.unstubAllGlobals());

describe("plain project import intent", () => {
  it.each([
    ["edit", "/api/project"],
    ["import", "/api/project?mode=import"],
  ] as const)("sends %s intent with the original concurrency token", async (mode, url) => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ status: "saved" }), {
      status: 200, headers: { etag: '"next"' },
    }));
    vi.stubGlobal("fetch", fetcher);
    expect(await saveProject(project, '"previous"', mode)).toEqual({ status: "saved", etag: '"next"' });
    const [actualUrl, options] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(actualUrl).toBe(url);
    expect(options.method).toBe("PUT");
    expect(options.headers).toEqual({ "Content-Type": "application/json", "If-Match": '"previous"' });
    expect(JSON.parse(options.body as string)).toEqual(project);
  });

  it("passes on what an import did with the files beside the project it replaced", async () => {
    const notice = "The previous project's scripts were set aside in the backup taken before the import.";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      status: "saved", needs_login: [], plugins_need_login: [], previous_files: "set_aside", notice,
    }), { status: 200, headers: { etag: '"next"' } })));
    const answer = await saveProject(project, '"previous"', "import");
    expect(answer).toMatchObject({ previous_files: "set_aside", notice });

    const imported = await importParsedProject(project, {
      getEtag: () => '"previous"',
      saveProject: async () => answer,
      reloadProject: async () => undefined,
      forceReload: async () => undefined,
      isConflict: () => false,
      errorMessage: parseApiError,
      onError: vi.fn(),
    });
    expect(imported).toEqual({ needsLogin: [], pluginsNeedLogin: [], previousFiles: "set_aside", notice });
  });

  it("shows the equipment refusal without adopting the rejected project", async () => {
    const reason = "This project uses a different product hardware model.";
    const reload = vi.fn();
    const adopt = vi.fn();
    const onError = vi.fn();
    const result = await importParsedProject(project, {
      getEtag: () => '"previous"',
      saveProject: async () => { throw new ApiError(422, JSON.stringify({ detail: reason })); },
      reloadProject: reload,
      forceReload: adopt,
      isConflict: (error) => error instanceof ConflictError,
      errorMessage: parseApiError,
      onError,
    });
    expect(result).toBe(false);
    expect(onError).toHaveBeenCalledWith(reason);
    expect(reload).not.toHaveBeenCalled();
    expect(adopt).not.toHaveBeenCalled();
  });
});
