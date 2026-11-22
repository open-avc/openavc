import { describe, it, expect, vi, afterEach } from "vitest";
import { askStartupStatus, readStartupStatus } from "./startupStatus";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("readStartupStatus", () => {
  it("reads the phase, the version that started and a failed start's reason", () => {
    expect(readStartupStatus({
      phase: "failed", ready: false, version: "0.41.0", error: "Loading the project failed: bad JSON",
      problems: [], held: false, retry_in: 42,
    })).toEqual({
      phase: "failed", version: "0.41.0", error: "Loading the project failed: bad JSON", held: false, retryIn: 42,
      problems: [],
    });
    expect(readStartupStatus({
      phase: "failed", ready: false, version: "0.41.0", error: "x", problems: [], held: true, retry_in: null,
    })).toMatchObject({ held: true, retryIn: null });
  });

  it("reads what the start went on past, and passes over an entry that is not one", () => {
    expect(readStartupStatus({
      phase: "ready", ready: true, version: "0.41.0", error: null, held: false, retry_in: null,
      problems: [
        { stage: "cloud_agent", detail: "The connection to OpenAVC Cloud did not start." },
        { stage: "update_checks" },
        "scripts",
        { stage: "", detail: "No stage." },
      ],
    })?.problems).toEqual([
      { stage: "cloud_agent", detail: "The connection to OpenAVC Cloud did not start." },
    ]);
    expect(readStartupStatus({ ready: true, error: null })?.problems).toEqual([]);
  });

  it("reads each phase the server says", () => {
    for (const phase of ["starting", "ready", "failed", "stopping"]) {
      expect(readStartupStatus({ phase, ready: phase === "ready", version: "0.41.0" })?.phase).toBe(phase);
    }
  });

  it("reads a server that says only ready and error", () => {
    expect(readStartupStatus({ ready: true, error: null })).toMatchObject({ phase: "ready", version: "" });
    expect(readStartupStatus({ ready: false, error: null })).toMatchObject({ phase: "starting" });
    expect(readStartupStatus({ ready: false, error: "bad" })).toMatchObject({ phase: "failed", error: "bad" });
  });

  it("is not a status when the answer is something else", () => {
    for (const body of [null, "ok", 3, {}, { status: "healthy" }, { phase: "sleeping" }, { detail: "Not Found" }]) {
      expect(readStartupStatus(body)).toBeNull();
    }
  });

  it("keeps a reason only with a failed start", () => {
    expect(readStartupStatus({ phase: "stopping", ready: false, error: "old" })?.error).toBeNull();
  });
});

describe("askStartupStatus", () => {
  it("tells no answer from an answer that is not a status", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    await expect(askStartupStatus("http://h/api/startup-status")).resolves.toEqual({ answered: false, status: null });

    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>", { status: 502 })));
    await expect(askStartupStatus("http://h/api/startup-status")).resolves.toEqual({ answered: true, status: null });

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ phase: "starting", ready: false, version: "1" }))));
    await expect(askStartupStatus("http://h/api/startup-status")).resolves.toMatchObject({
      answered: true, status: { phase: "starting", version: "1" },
    });
  });
});
