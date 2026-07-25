import { describe, it, expect, vi, afterEach } from "vitest";
import { ApiError, parseApiError } from "./errors";
import { request } from "./base";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ApiError", () => {
  it("carries the HTTP status as data for machine-readable classification", () => {
    const err = new ApiError(429, '{"detail":"Too many requests"}');
    expect(err).toBeInstanceOf(Error);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(429);
    expect(err.name).toBe("ApiError");
  });

  it("keeps the legacy 'API <status>: <body>' message so String(e)/.message consumers are unaffected", () => {
    const body = '{"detail":"nope"}';
    const err = new ApiError(503, body);
    expect(err.message).toBe(`API 503: ${body}`);
    // The regex extractor in parseApiError still recognizes this exact shape.
    expect(err.message).toMatch(/^API \d+: /);
  });

  it("extracts a string JSON detail", () => {
    expect(new ApiError(500, '{"detail":"boom"}').detail).toBe("boom");
  });

  it("extracts a {message, errors} detail into a joined string", () => {
    const err = new ApiError(422, '{"detail":{"message":"Invalid","errors":["a","b"]}}');
    expect(err.detail).toBe("Invalid:\na\nb");
  });

  it("falls back to the raw body when it is not JSON", () => {
    expect(new ApiError(502, "upstream down").detail).toBe("upstream down");
  });
});

describe("parseApiError", () => {
  it("returns the clean detail for an ApiError", () => {
    expect(parseApiError(new ApiError(409, '{"detail":"in use"}'))).toBe("in use");
  });

  it("still unwraps a legacy 'API <status>: <body>' string Error", () => {
    expect(parseApiError(new Error('API 400: {"detail":"bad field"}'))).toBe("bad field");
  });

  it("returns the message for a non-API Error", () => {
    expect(parseApiError(new Error("plain failure"))).toBe("plain failure");
  });

  it("stringifies a non-Error value", () => {
    expect(parseApiError("literal string")).toBe("literal string");
  });
});

describe("request() error contract", () => {
  it("throws a typed ApiError carrying the response status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 501,
        text: async () => '{"detail":"not implemented"}',
      }),
    );
    const err = await request("/whatever").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(501);
    expect(err.detail).toBe("not implemented");
  });
});
