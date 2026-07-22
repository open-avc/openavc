import { describe, it, expect, vi, afterEach } from "vitest";
import { importTheme, ThemeExistsError } from "./systemClient";

function mockFetch(response: {
  ok: boolean;
  status: number;
  json?: () => unknown;
  text?: () => string;
}) {
  const fn = vi.fn().mockResolvedValue({
    ok: response.ok,
    status: response.status,
    json: async () => (response.json ? response.json() : {}),
    text: async () => (response.text ? response.text() : ""),
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const file = new File(['{"id":"x"}'], "x.avctheme", { type: "application/json" });

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("importTheme", () => {
  it("POSTs without an overwrite param by default", async () => {
    const fetchFn = mockFetch({ ok: true, status: 200, json: () => ({ status: "imported", id: "x", name: "X" }) });
    const res = await importTheme(file);
    expect(res.id).toBe("x");
    const url = fetchFn.mock.calls[0][0] as string;
    expect(url).toContain("/themes/import");
    expect(url).not.toContain("overwrite");
  });

  it("adds ?overwrite=true when overwriting", async () => {
    const fetchFn = mockFetch({ ok: true, status: 200, json: () => ({ status: "imported", id: "x", name: "X" }) });
    await importTheme(file, true);
    expect(fetchFn.mock.calls[0][0] as string).toContain("?overwrite=true");
  });

  it("throws a typed ThemeExistsError on a custom-collision 409", async () => {
    mockFetch({
      ok: false,
      status: 409,
      json: () => ({ detail: { code: "theme_exists", id: "midnight", name: "Midnight" } }),
    });
    await expect(importTheme(file)).rejects.toBeInstanceOf(ThemeExistsError);
    try {
      await importTheme(file);
    } catch (e) {
      expect((e as ThemeExistsError).themeId).toBe("midnight");
      expect((e as ThemeExistsError).themeName).toBe("Midnight");
    }
  });

  it("throws a plain Error (not ThemeExistsError) on a built-in 409", async () => {
    mockFetch({ ok: false, status: 409, json: () => ({ detail: "Cannot overwrite built-in theme 'dark-default'" }) });
    await expect(importTheme(file)).rejects.toThrow(/built-in/);
    await expect(importTheme(file)).rejects.not.toBeInstanceOf(ThemeExistsError);
  });

  it("throws a plain Error on a non-409 failure", async () => {
    mockFetch({ ok: false, status: 400, text: () => "Invalid JSON" });
    await expect(importTheme(file)).rejects.toThrow(/Invalid JSON/);
  });
});
