/**
 * @vitest-environment jsdom
 * @vitest-environment-options { "url": "http://localhost:8001/tunnel/t1/programmer/" }
 *
 * A tab opened through Remote Access lives at /tunnel/<id>/programmer/, and
 * api/base.ts reads that prefix at import time, so this file runs there.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

/**
 * When the Remote Access session a tab came through ends (closed in the
 * portal, timed out, or its access token expired), the cloud answers for the
 * system with a bare-text refusal. The tab used to freeze behind a red dot,
 * fall onto the cloud's raw "Tunnel not found" text when a view loaded, or,
 * for the expired token's 401, offer the system's sign-in screen, where no
 * password could help. It now says the session ended.
 */

class FakeSocket {
  static last: FakeSocket | null = null;
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  readyState = FakeSocket.CONNECTING;
  constructor(readonly url: string, readonly protocols?: string | string[]) {
    FakeSocket.last = this;
  }
  send(): void {}
  close(): void {
    this.readyState = FakeSocket.CLOSED;
  }
}

let served: Response;
let fetches: string[];
const network = vi.fn(async (input: RequestInfo | URL) => {
  fetches.push(typeof input === "string" ? input : String(input));
  return served.clone();
});

/** A fresh copy of the auth and socket modules, so "ended" starts unset. */
async function load() {
  vi.resetModules();
  window.fetch = network as unknown as typeof window.fetch;
  const auth = await import("./auth");
  auth.installFetchAuth();
  const ws = await import("./wsClient");
  return { auth, ws };
}

/** What a request heard back: [remote session ended, sign-in asked for]. */
async function outcome(reply: Response, url = "/tunnel/t1/api/project") {
  const { auth } = await load();
  served = reply;
  const ended = vi.fn();
  const signIn = vi.fn();
  window.addEventListener(auth.REMOTE_SESSION_ENDED_EVENT, ended);
  window.addEventListener(auth.AUTH_REQUIRED_EVENT, signIn);
  try {
    await fetch(url);
  } finally {
    window.removeEventListener(auth.REMOTE_SESSION_ENDED_EVENT, ended);
    window.removeEventListener(auth.AUTH_REQUIRED_EVENT, signIn);
  }
  return [ended.mock.calls.length, signIn.mock.calls.length];
}

const text = (body: string, status: number) => new Response(body, { status });
const json = (body: string, status: number) =>
  new Response(body, { status, headers: { "content-type": "application/json" } });

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  network.mockClear();
  fetches = [];
  served = json("{}", 200);
  FakeSocket.last = null;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a Remote Access session that ended", () => {
  it("is the cloud's 404 for a session that is gone", async () => {
    expect(await outcome(text("Tunnel not found or not active", 404))).toEqual([1, 0]);
  });

  it("is the cloud's 401 for an access token that expired, and asks for no password", async () => {
    expect(await outcome(text("Unauthorized — invalid or missing tunnel access token", 401))).toEqual([1, 0]);
  });

  it("is the cloud's 403 saying remote access ended", async () => {
    expect(await outcome(text("Remote access ended. Open a new session from the portal.", 403))).toEqual([1, 0]);
  });
});

describe("what still is not one", () => {
  it("a 401 from the system itself still asks for its password", async () => {
    expect(await outcome(json('{"detail":"Authentication required"}', 401))).toEqual([0, 1]);
  });

  it("a 404 from the system itself is only a missing thing", async () => {
    expect(await outcome(json('{"detail":"Not Found"}', 404))).toEqual([0, 0]);
  });

  it("the cloud's 403 for a request outside the session's scope", async () => {
    expect(await outcome(text("Panel access only", 403))).toEqual([0, 0]);
  });

  it("the cloud's 502 while the system is not connected", async () => {
    expect(await outcome(text("Agent not connected to tunnel", 502))).toEqual([0, 0]);
  });
});

describe("the tab's live connection", () => {
  it("asks once it drops, learns the session ended, and stops reconnecting", async () => {
    const { auth, ws } = await load();
    served = text("Tunnel not found or not active", 404);
    const ended = vi.fn();
    window.addEventListener(auth.REMOTE_SESSION_ENDED_EVENT, ended);

    ws.connect();
    const first = FakeSocket.last!;
    first.readyState = FakeSocket.OPEN;
    first.onopen!();
    first.onclose!({ code: 1006 });

    // No session token: the cloud vouches for a Remote Access tab, and the
    // probe runs anyway.
    await vi.waitFor(() => expect(ended).toHaveBeenCalledTimes(1));
    expect(fetches).toEqual(["/tunnel/t1/api/system/version"]);

    ws.connect();
    expect(FakeSocket.last).toBe(first);
    window.removeEventListener(auth.REMOTE_SESSION_ENDED_EVENT, ended);
    ws.disconnect();
  });
});
