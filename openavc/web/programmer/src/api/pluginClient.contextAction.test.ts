import { describe, it, expect, vi, afterEach } from "vitest";
import { emitContextAction } from "./pluginClient";

// An action's name goes in the request's path. A name the server refuses must
// reach it whole, so it is refused, rather than cut at a "?" or "#" into a name
// that runs a different action.

function serverAnswers() {
  const fetch = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => ({ status: "emitted" }),
    text: async () => JSON.stringify({ status: "emitted" }),
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("emitContextAction", () => {
  it("sends a plain name as written", async () => {
    const fetch = serverAnswers();
    await emitContextAction("acme_widget", "refresh_domains", {});
    expect(String(fetch.mock.calls[0][0])).toMatch(/\/api\/plugins\/acme_widget\/context-action\/refresh_domains$/);
  });

  it("sends a name holding a ? or a # whole", async () => {
    const fetch = serverAnswers();
    await emitContextAction("acme_widget", "clear?all#now", {});
    expect(String(fetch.mock.calls[0][0])).toMatch(/\/context-action\/clear%3Fall%23now$/);
  });
});
