/**
 * @vitest-environment jsdom
 * @vitest-environment-options { "url": "http://localhost:8080/programmer/" }
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { waitForServerReady } from "./serverReady";

/**
 * A tab whose socket comes back while the server is still starting must not
 * take its REST loads until the server answers them: until then each answers
 * 503, and the tab keeps the previous run's project revision, so its first
 * save is refused as a conflict.
 */

function answers(...bodies: Array<Record<string, unknown> | Error>) {
  const queue = [...bodies];
  const fetchMock = vi.fn(async () => {
    const next = queue.length > 1 ? queue.shift()! : queue[0];
    if (next instanceof Error) throw next;
    return new Response(JSON.stringify(next), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("waitForServerReady", () => {
  it("resolves at once when the server is already up, with the version that started", async () => {
    const fetchMock = answers({ phase: "ready", ready: true, version: "0.41.0", error: null });
    await expect(waitForServerReady(() => true, 1)).resolves.toMatchObject({ phase: "ready", version: "0.41.0" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith("/api/startup-status", { cache: "no-store" });
  });

  it("reads a server that says only ready", async () => {
    answers({ ready: true, error: null });
    await expect(waitForServerReady(() => true, 1)).resolves.toMatchObject({ phase: "ready" });
  });

  it("asks again until the server has finished starting", async () => {
    const fetchMock = answers(
      { phase: "stopping", ready: false, error: null },
      new TypeError("Failed to fetch"),
      { phase: "starting", ready: false, error: null },
      { phase: "ready", ready: true, error: null },
    );
    await expect(waitForServerReady(() => true, 1)).resolves.toMatchObject({ phase: "ready" });
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("stops when the connection it waited for is gone", async () => {
    let wanted = true;
    const fetchMock = answers({ phase: "starting", ready: false, error: null });
    const waiting = waitForServerReady(() => wanted, 1);
    await new Promise((resolve) => setTimeout(resolve, 5));
    wanted = false;
    await expect(waiting).resolves.toBeNull();
    const asked = fetchMock.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(fetchMock.mock.calls.length).toBe(asked);
  });

  it("ends with the reason when the server says it failed to start", async () => {
    const fetchMock = answers({
      phase: "failed", ready: false, version: "0.41.0", error: "Project file is not valid JSON",
      held: false, retry_in: 30,
    });
    await expect(waitForServerReady(() => true, 1)).resolves.toEqual({
      phase: "failed", version: "0.41.0", error: "Project file is not valid JSON", held: false, retryIn: 30,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
