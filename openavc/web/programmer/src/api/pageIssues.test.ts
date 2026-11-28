import { describe, it, expect, vi, afterEach } from "vitest";
import { pageIssues, type MissingPage, type PageIssue } from "./pageIssues";

function mockFetch(reply: { ok: boolean; status: number; body?: unknown } | Error) {
  const fn = reply instanceof Error
    ? vi.fn().mockRejectedValue(reply)
    : vi.fn().mockResolvedValue({
      ok: reply.ok,
      status: reply.status,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => reply.body,
      text: async () => JSON.stringify(reply.body ?? ""),
    });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const ISSUE: PageIssue = {
  code: "element_id_taken", message: "Element 'go' already exists on page 'main'",
  page: "other", element: "go", layout: null,
};

const MISSING: MissingPage = {
  code: "idle_page_missing", message: "The idle page 'ghost' does not exist. The pages are: none.",
  element: null, macro: null, step: null,
};

describe("pageIssues", () => {
  it("posts the pages, master elements, idle page and macros it is given, and nothing else, and hands back both answers", async () => {
    const fetchFn = mockFetch({ ok: true, status: 200, body: { issues: [ISSUE], missing_pages: [MISSING] } });
    const pages = [{ id: "main", elements: [] }];
    const macros = [{ id: "m", steps: [{ action: "ui.navigate", page: "ghost" }] }];
    const ui = { pages, master_elements: [{ id: "m" }], settings: { idle_page: "main", lock_code: "1234" } };
    expect(await pageIssues({ ui, macros })).toEqual({ issues: [ISSUE], missing_pages: [MISSING] });
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toContain("/ui/page-issues");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      ui: { pages, master_elements: [{ id: "m" }], settings: { idle_page: "main" } },
      macros,
    });
  });

  it("posts no master elements, idle page or macros as none", async () => {
    const fetchFn = mockFetch({ ok: true, status: 200, body: { issues: [], missing_pages: [] } });
    expect(await pageIssues({ ui: { pages: [] } })).toEqual({ issues: [], missing_pages: [] });
    expect(JSON.parse(fetchFn.mock.calls[0][1].body)).toEqual({
      ui: { pages: [], master_elements: [], settings: {} }, macros: [],
    });
  });

  it("reads a reply with no missing pages as none", async () => {
    mockFetch({ ok: true, status: 200, body: { issues: [ISSUE] } });
    expect(await pageIssues({ ui: { pages: [] } })).toEqual({ issues: [ISSUE], missing_pages: [] });
    mockFetch({ ok: true, status: 200, body: { issues: [], missing_pages: "not a list" } });
    expect(await pageIssues({ ui: { pages: [] } })).toEqual({ issues: [], missing_pages: [] });
  });

  it("answers nothing known, and never rejects, when it cannot ask", async () => {
    mockFetch({ ok: false, status: 500, body: { detail: "boom" } });
    expect(await pageIssues({ ui: { pages: [] } })).toBeUndefined();
    mockFetch(new TypeError("Failed to fetch"));
    expect(await pageIssues({ ui: { pages: [] } })).toBeUndefined();
    mockFetch({ ok: true, status: 200, body: { issues: "not a list" } });
    expect(await pageIssues({ ui: { pages: [] } })).toBeUndefined();
  });
});
