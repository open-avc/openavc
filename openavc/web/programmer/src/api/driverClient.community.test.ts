import { describe, it, expect, vi, afterEach } from "vitest";
import { CatalogUnreachableError, fetchCommunityDrivers } from "./driverClient";

// The server answers 200 with no drivers and a sentence when it cannot reach
// the community library. Taken as a list, that read as an empty library.

function serverAnswers(body: unknown) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  }));
}

const UNREACHABLE =
  "Could not reach the community driver library at raw.githubusercontent.com. " +
  "Check that this system can reach the internet.";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchCommunityDrivers", () => {
  it("throws the server's sentence when the library could not be reached", async () => {
    serverAnswers({ drivers: [], error: UNREACHABLE });
    const err = await fetchCommunityDrivers().catch((e) => e);
    expect(err).toBeInstanceOf(CatalogUnreachableError);
    expect(err.message).toBe(UNREACHABLE);
  });

  it("answers the drivers when there are some", async () => {
    serverAnswers({ drivers: [{ id: "acme_widget" }], error: null });
    expect(await fetchCommunityDrivers()).toEqual([{ id: "acme_widget" }]);
  });
});
