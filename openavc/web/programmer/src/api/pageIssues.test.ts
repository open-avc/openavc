import { describe, it, expect, vi, afterEach } from "vitest";
import { pageIssues, type PageIssue } from "./pageIssues";

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

describe("pageIssues", () => {
  it("posts the pages and master elements it is given, and nothing else, and hands back the issues", async () => {
    const fetchFn = mockFetch({ ok: true, status: 200, body: { issues: [ISSUE] } });
    const pages = [{ id: "main", elements: [] }];
    const ui = { pages, master_elements: [{ id: "m" }], settings: { idle_page: "main" } };
    expect(await pageIssues(ui)).toEqual([ISSUE]);
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toContain("/ui/page-issues");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ ui: { pages, master_elements: [{ id: "m" }] } });
  });

  it("posts no master elements as none", async () => {
    const fetchFn = mockFetch({ ok: true, status: 200, body: { issues: [] } });
    expect(await pageIssues({ pages: [] })).toEqual([]);
    expect(JSON.parse(fetchFn.mock.calls[0][1].body)).toEqual({ ui: { pages: [], master_elements: [] } });
  });

  it("answers nothing known, and never rejects, when it cannot ask", async () => {
    mockFetch({ ok: false, status: 500, body: { detail: "boom" } });
    expect(await pageIssues({ pages: [] })).toBeUndefined();
    mockFetch(new TypeError("Failed to fetch"));
    expect(await pageIssues({ pages: [] })).toBeUndefined();
    mockFetch({ ok: true, status: 200, body: { issues: "not a list" } });
    expect(await pageIssues({ pages: [] })).toBeUndefined();
  });
});
