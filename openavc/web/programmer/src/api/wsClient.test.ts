/**
 * @vitest-environment jsdom
 * @vitest-environment-options { "url": "http://localhost:8080/programmer/" }
 *
 * The URL matters: api/base.ts derives the API prefix from the page path at
 * import time, so a page served from "/" would give it "//api", which resolves
 * as a protocol-relative URL to another origin and gets no credential.
 */
import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from "vitest";
import {
  AUTH_REQUIRED_EVENT,
  clearSession,
  installFetchAuth,
  setSessionToken,
} from "./auth";
import * as ws from "./wsClient";

/**
 * A restart ends every session token, because they live in the server's
 * memory. The tab does not learn that from the WebSocket: the server refuses
 * an unauthenticated upgrade before accepting it, so the handshake ends as a
 * plain HTTP 403 and the browser reports close code 1006 with no message —
 * the same close an unreachable port produces. Retrying that forever is what
 * left the IDE looking healthy behind a red dot until the next autosave 401ed
 * in front of the user with a raw error.
 *
 * So a reconnect failure asks REST, where the answer is a status code.
 */

/** Stands in for the browser's WebSocket, which never connects to anything. */
class FakeSocket {
  static last: FakeSocket | null = null;
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  readyState = FakeSocket.CONNECTING;

  constructor(
    readonly url: string,
    readonly protocols?: string | string[],
  ) {
    FakeSocket.last = this;
  }

  readonly sent: unknown[] = [];

  send(data: string): void {
    this.sent.push(JSON.parse(data));
  }
  close(): void {
    this.readyState = FakeSocket.CLOSED;
  }
}

/** The most recent socket's handshake done, nothing from the server yet. */
function handshake(): FakeSocket {
  const socket = FakeSocket.last!;
  socket.readyState = FakeSocket.OPEN;
  socket.onopen?.();
  return socket;
}

/** The most recent socket, open and served: the server's first message (its
 *  state snapshot) arrived. */
function open(): FakeSocket {
  const socket = handshake();
  socket.onmessage!({ data: JSON.stringify({ type: "state.snapshot", state: {} }) } as MessageEvent);
  return socket;
}

/** Close the most recent socket the way the browser does. */
function close(code: number, reason = ""): void {
  FakeSocket.last!.onclose!({ code, reason });
}

let served: Response;
let fetches: string[];

/** The network, under the auth interceptor rather than over it. */
const network = vi.fn(async (input: RequestInfo | URL) => {
  fetches.push(typeof input === "string" ? input : String(input));
  return served.clone();
});

beforeAll(() => {
  // Install the real auth interceptor over it, so a probe that comes back 401
  // travels the same path a real one does.
  window.fetch = network as unknown as typeof window.fetch;
  installFetchAuth();
});

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  network.mockClear();
  fetches = [];
  served = new Response("{}", { status: 200 });
  FakeSocket.last = null;
  setSessionToken("session-token");
});

afterEach(() => {
  ws.disconnect();
  clearSession();
  vi.unstubAllGlobals();
});

describe("wsClient reconnect", () => {
  it("asks REST whether the session survived, once a reconnect fails", async () => {
    ws.connect();
    open();
    close(1006);

    await vi.waitFor(() => expect(fetches).toEqual(["/api/system/version"]));
  });

  it("shows the sign-in screen when REST says the session is gone", async () => {
    served = new Response('{"detail":"Authentication required"}', { status: 401 });
    const authRequired = vi.fn();
    window.addEventListener(AUTH_REQUIRED_EVENT, authRequired);

    ws.connect();
    open();
    close(1006);

    await vi.waitFor(() => expect(authRequired).toHaveBeenCalledTimes(1));
    window.removeEventListener(AUTH_REQUIRED_EVENT, authRequired);
  });

  it("stays quiet while the server is merely down", async () => {
    network.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const authRequired = vi.fn();
    window.addEventListener(AUTH_REQUIRED_EVENT, authRequired);

    ws.connect();
    open();
    close(1006);

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(authRequired).not.toHaveBeenCalled();
    window.removeEventListener(AUTH_REQUIRED_EVENT, authRequired);
  });

  it("stays quiet when the server is up and the session is still good", async () => {
    const authRequired = vi.fn();
    window.addEventListener(AUTH_REQUIRED_EVENT, authRequired);

    ws.connect();
    open();
    close(1006);

    await vi.waitFor(() => expect(fetches).toHaveLength(1));
    expect(authRequired).not.toHaveBeenCalled();
    window.removeEventListener(AUTH_REQUIRED_EVENT, authRequired);
  });

  it("has nothing to ask about when no session is cached", async () => {
    clearSession();

    ws.connect();
    open();
    close(1006);

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fetches).toEqual([]);
  });

  it("leaves the pre-open rule alone: three strikes, no probe", async () => {
    const authRequired = vi.fn();
    window.addEventListener(AUTH_REQUIRED_EVENT, authRequired);

    ws.connect();
    close(1006);
    close(1006);
    expect(authRequired).not.toHaveBeenCalled();
    close(1006);

    expect(authRequired).toHaveBeenCalledTimes(1);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fetches).toEqual([]);
    window.removeEventListener(AUTH_REQUIRED_EVENT, authRequired);
  });

  it("still bounces straight to the sign-in screen on an explicit 4001", async () => {
    const authRequired = vi.fn();
    window.addEventListener(AUTH_REQUIRED_EVENT, authRequired);

    ws.connect();
    open();
    close(4001);

    expect(authRequired).toHaveBeenCalledTimes(1);
    window.removeEventListener(AUTH_REQUIRED_EVENT, authRequired);
  });
});

/**
 * A server still starting, one whose start failed, or one on its way down
 * accepts the socket and closes it at once with NOT_READY_CLOSE, the phase as
 * the reason (middleware/startup_splash.py). The tab must not read as
 * connected in between, and a start, which takes seconds, is tried again
 * soon rather than after a growing wait.
 */
describe("wsClient and a server that is not ready", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("never reads as connected when turned away, and retries a start on a steady wait", () => {
    vi.useFakeTimers();
    const connected = vi.fn();
    const closes: ws.SocketClose[] = [];
    const offConnect = ws.onConnect(connected);
    const offDisconnect = ws.onDisconnect((c) => closes.push(c));
    try {
      ws.connect();
      const first = handshake();
      close(ws.NOT_READY_CLOSE, "starting");
      expect(connected).not.toHaveBeenCalled();
      expect(closes).toEqual([{ code: ws.NOT_READY_CLOSE, reason: "starting" }]);

      vi.advanceTimersByTime(999);
      expect(FakeSocket.last).toBe(first);
      vi.advanceTimersByTime(1);
      const second = FakeSocket.last;
      expect(second).not.toBe(first);

      handshake();
      close(ws.NOT_READY_CLOSE, "starting");
      vi.advanceTimersByTime(1000);
      expect(FakeSocket.last).not.toBe(second);

      open();
      expect(connected).toHaveBeenCalledTimes(1);
    } finally {
      offConnect();
      offDisconnect();
    }
    // The server said why: nothing to ask about the session.
    expect(fetches).toEqual([]);
  });

  it("backs a failed start off as it would a dropped network", () => {
    vi.useFakeTimers();
    ws.connect();
    const first = handshake();
    close(ws.NOT_READY_CLOSE, "failed");

    vi.advanceTimersByTime(1999);
    expect(FakeSocket.last).toBe(first);
    vi.advanceTimersByTime(1);
    const second = FakeSocket.last;
    expect(second).not.toBe(first);

    handshake();
    close(ws.NOT_READY_CLOSE, "failed");
    vi.advanceTimersByTime(2999);
    expect(FakeSocket.last).toBe(second);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.last).not.toBe(second);
  });

  it("holds a command until the socket is served", () => {
    ws.connect();
    const socket = handshake();
    ws.send({ type: "macro.execute", macro_id: "system_on" });
    expect(socket.sent).toEqual([]);

    open();
    expect(socket.sent).toEqual([{ type: "macro.execute", macro_id: "system_on" }]);
  });
});
