import { describe, it, expect, vi, beforeEach } from "vitest";

// How a run ended is the server's answer. A second press inside half a second
// was answered here with a "debounced" status the server never sends, and the
// Test button then said nothing at all.

const base = vi.hoisted(() => ({
  request: vi.fn(async () => ({ status: "completed", macro_id: "m" })),
}));

vi.mock("./base", () => ({ request: base.request }));

import { executeMacro } from "./stateClient";

beforeEach(() => {
  base.request.mockClear();
});

describe("executeMacro", () => {
  it("asks the server every time, and answers with what it said", async () => {
    await executeMacro("m");
    const second = await executeMacro("m");

    expect(base.request).toHaveBeenCalledTimes(2);
    expect(base.request).toHaveBeenCalledWith("/macros/m/execute", { method: "POST" });
    expect(second.status).toBe("completed");
  });
});
