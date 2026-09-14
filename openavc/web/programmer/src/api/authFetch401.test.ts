/**
 * @vitest-environment jsdom
 * @vitest-environment-options { "url": "http://localhost:8080/programmer/" }
 */
import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from "vitest";
import {
  AUTH_REQUIRED_EVENT,
  clearSession,
  hasSession,
  installFetchAuth,
  setSessionToken,
} from "./auth";

/**
 * Which 401s mean "the credential you were holding is dead".
 *
 * Not the one from the session-mint endpoint: that door is where a typed
 * password is exchanged for a token, so its 401 means the password was wrong.
 * Announcing an expiry there told somebody who had never had a session that
 * theirs had ended, right above the correct "Wrong username or password", and
 * would have cleared a live session held by whoever asked.
 */

/** The response the wrapped fetch hands back; set per test. */
let reply: Response;
/** Every URL the underlying fetch was called with. */
let seen: string[];

beforeAll(() => {
  // Install ONCE, over a mock that reads `reply`. The interceptor captures the
  // underlying fetch at install time and `installed` guards re-entry, so the
  // indirection is what lets each test choose a status.
  window.fetch = vi.fn(async (input: RequestInfo | URL) => {
    seen.push(typeof input === "string" ? input : input.toString());
    return reply;
  }) as unknown as typeof window.fetch;
  installFetchAuth();
});

beforeEach(() => {
  seen = [];
  reply = new Response("{}", { status: 200 });
  clearSession();
});

afterEach(() => {
  clearSession();
});

/** Count AUTH_REQUIRED_EVENTs fired while `fn` runs. */
async function expiriesDuring(fn: () => Promise<unknown>): Promise<number> {
  let fired = 0;
  const onFired = () => { fired += 1; };
  window.addEventListener(AUTH_REQUIRED_EVENT, onFired);
  try {
    await fn();
  } finally {
    window.removeEventListener(AUTH_REQUIRED_EVENT, onFired);
  }
  return fired;
}

describe("a 401 from the session-mint endpoint", () => {
  it("is not reported as an expiry", async () => {
    reply = new Response('{"detail":"Unauthorized"}', { status: 401 });

    const fired = await expiriesDuring(() => fetch("/api/auth/session", { method: "POST" }));

    expect(fired).toBe(0);
  });

  it("leaves a session already in hand alone", async () => {
    setSessionToken("a-live-token");
    reply = new Response('{"detail":"Unauthorized"}', { status: 401 });

    await fetch("/api/auth/session", { method: "POST" });

    expect(hasSession()).toBe(true);
  });

  it("is still not an expiry behind the cloud tunnel's path prefix", async () => {
    reply = new Response("{}", { status: 401 });

    const fired = await expiriesDuring(() =>
      fetch("/tunnel/abc123/api/auth/session", { method: "POST" }),
    );

    expect(fired).toBe(0);
  });

  it("does not swallow the refusal — the caller still sees the 401", async () => {
    reply = new Response('{"detail":"Unauthorized"}', { status: 401 });

    const res = await fetch("/api/auth/session", { method: "POST" });

    expect(res.status).toBe(401);
    expect(seen).toContain("/api/auth/session");
  });
});

describe("a 401 from anywhere else under /api", () => {
  it("is reported as an expiry and drops the session", async () => {
    setSessionToken("a-dead-token");
    reply = new Response('{"detail":"Unauthorized"}', { status: 401 });

    const fired = await expiriesDuring(() => fetch("/api/project"));

    expect(fired).toBe(1);
    expect(hasSession()).toBe(false);
  });

  it("is reported for a path that merely starts the same way", async () => {
    // Guards the endsWith match: this is not the mint endpoint.
    reply = new Response("{}", { status: 401 });

    const fired = await expiriesDuring(() => fetch("/api/auth/sessions/all"));

    expect(fired).toBe(1);
  });
});

describe("a 401 is the only status that ends a session", () => {
  it("a 200 from the mint endpoint fires nothing", async () => {
    const fired = await expiriesDuring(() => fetch("/api/auth/session", { method: "POST" }));
    expect(fired).toBe(0);
  });

  it("a 403 elsewhere fires nothing", async () => {
    reply = new Response("{}", { status: 403 });
    const fired = await expiriesDuring(() => fetch("/api/project"));
    expect(fired).toBe(0);
  });
});
