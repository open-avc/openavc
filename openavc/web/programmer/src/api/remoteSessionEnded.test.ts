/**
 * @vitest-environment jsdom
 * @vitest-environment-options { "url": "http://localhost:8001/tunnel/t1/programmer/" }
 *
 * A tab opened through Remote Access lives at /tunnel/<id>/programmer/, and
 * api/base.ts reads that prefix at import time, so this file runs there.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * When the Remote Access session a tab came through ends (closed in the
 * portal, timed out, or its access token expired), the cloud answers for the
 * system with a bare-text refusal, and closes the tab's socket with its own
 * code. The tab used to freeze behind a red dot, fall onto the cloud's raw
 * "Tunnel not found" text when a view loaded, or, for the expired token's
 * 401, offer the system's sign-in screen, where no password could help. It
 * now says the session ended.
 *
 * Which answers and closes mean that is one case file, which the panel's
 * harness runs through its own copy of the rule.
 */

type AnswerCase = {
  name: string;
  status: number;
  content_type: string;
  body: string;
  ended: boolean;
};
type CloseCase = { name: string; code: number; reason: string; ended: boolean };

// Read off disk: the case file lives outside this Vite project, and vitest
// runs with openavc/web/programmer as its cwd.
const corpus: { answers: AnswerCase[]; closes: CloseCase[] } = JSON.parse(
  readFileSync(
    resolve(process.cwd(), "../../../tests/fixtures/remote_session_ended_cases.json"),
    "utf-8",
  ),
);

class FakeSocket {
  static last: FakeSocket | null = null;
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: { code: number; reason?: string }) => void) | null = null;
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

/** A reply shaped as the case says: the system's own errors are JSON, the
 *  cloud's refusals bare text. */
function replyFor(c: AnswerCase): Response {
  const headers: Record<string, string> = c.content_type ? { "content-type": c.content_type } : {};
  return new Response(c.body, { status: c.status, headers });
}

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  network.mockClear();
  fetches = [];
  served = new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
  FakeSocket.last = null;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the case file", () => {
  it("has answers and closes on both sides of the line", () => {
    for (const list of [corpus.answers, corpus.closes]) {
      expect(new Set(list.map((c: { ended: boolean }) => c.ended))).toEqual(new Set([true, false]));
    }
  });
});

describe("an answer to a request made inside the session", () => {
  for (const c of corpus.answers) {
    it(`${c.name}: ${c.ended ? "the session ended" : "the session stands"}`, async () => {
      const [ended, signIn] = await outcome(replyFor(c));
      expect(ended).toBe(c.ended ? 1 : 0);
      // An ended session never offers the system's sign-in: no password
      // could bring it back.
      if (c.ended) expect(signIn).toBe(0);
    });
  }

  it("a 401 from the system itself still asks for its password", async () => {
    const own = corpus.answers.find((c) => c.status === 401 && c.content_type === "application/json");
    expect(await outcome(replyFor(own!))).toEqual([0, 1]);
  });
});

describe("how the tab's socket closed", () => {
  for (const c of corpus.closes) {
    it(`${c.name}: ${c.ended ? "the session ended" : "not by itself"}`, async () => {
      const { auth } = await load();
      expect(auth.isEndedRemoteClose(c.code)).toBe(c.ended);
    });
  }
});

describe("the tab's live connection", () => {
  it("asks once it drops, learns the session ended, and stops reconnecting", async () => {
    const { auth, ws } = await load();
    served = replyFor(corpus.answers.find((c) => c.ended && c.status === 404)!);
    const ended = vi.fn();
    window.addEventListener(auth.REMOTE_SESSION_ENDED_EVENT, ended);

    ws.connect();
    const first = FakeSocket.last!;
    first.readyState = FakeSocket.OPEN;
    // Served: the server's first message (its state snapshot) arrived.
    first.onmessage!({ data: JSON.stringify({ type: "state.snapshot", state: {} }) } as MessageEvent);
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

  it("hears the cloud's close for an ended session and stops at once, asking nothing", async () => {
    const { auth, ws } = await load();
    const ended = vi.fn();
    window.addEventListener(auth.REMOTE_SESSION_ENDED_EVENT, ended);
    const closing = corpus.closes.find((c) => c.ended)!;
    const timers = vi.spyOn(globalThis, "setTimeout");

    ws.connect();
    const first = FakeSocket.last!;
    first.readyState = FakeSocket.OPEN;
    first.onmessage!({ data: JSON.stringify({ type: "state.snapshot", state: {} }) } as MessageEvent);
    timers.mockClear();
    first.onclose!({ code: closing.code, reason: closing.reason });

    expect(ended).toHaveBeenCalledTimes(1);
    expect(fetches).toEqual([]);
    expect(timers).not.toHaveBeenCalled();
    ws.connect();
    expect(FakeSocket.last).toBe(first);
    timers.mockRestore();
    window.removeEventListener(auth.REMOTE_SESSION_ENDED_EVENT, ended);
    ws.disconnect();
  });
});
