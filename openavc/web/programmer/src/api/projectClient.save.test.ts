import { describe, it, expect, vi, afterEach } from "vitest";
import { ConflictError, saveProject } from "./projectClient";
import type { ProjectConfig } from "./types";

// What saveProject hands back from the server's answer to a project PUT.

const PROJECT = { openavc_version: "0.17.0" } as unknown as ProjectConfig;
const realFetch = globalThis.fetch;

function answer(status: number, body: unknown, headers: Record<string, string> = {}) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status, headers }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

afterEach(() => {
  globalThis.fetch = realFetch;
});

describe("saveProject on an import", () => {
  it("hands back the plugins waiting for their login as well as the devices", async () => {
    const device = { id: "projector_1", name: "Projector 1" };
    const plugin = { id: "acme_widget_plugin", name: "Acme Widget" };
    answer(200, { status: "saved", needs_login: [device], plugins_need_login: [plugin] }, { etag: '"b"' });

    const saved = await saveProject(PROJECT, '"a"', "import");

    expect(saved.needs_login).toEqual([device]);
    expect(saved.plugins_need_login).toEqual([plugin]);
  });

  it("hands back no login lists for an ordinary save", async () => {
    answer(200, { status: "saved" }, { etag: '"b"' });
    const saved = await saveProject(PROJECT, '"a"');
    expect(saved).toEqual({ status: "saved", etag: '"b"' });
  });
});

describe("saveProject's conflict", () => {
  it("is thrown for a 409 that says another writer moved the project", async () => {
    answer(409, { detail: "Project was modified by another session.", code: "conflict" });
    await expect(saveProject(PROJECT, '"a"')).rejects.toBeInstanceOf(ConflictError);
  });
});
