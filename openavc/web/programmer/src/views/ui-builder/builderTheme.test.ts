import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

// The UI Builder reads the chosen theme once, and Panel Settings shows a blank
// accent and font from what that read returned: the theme's own variables, or
// null when the theme cannot be read.

vi.mock("../../api/restClient", () => ({
  getTunnelPrefix: () => "",
  listThemes: vi.fn(() => Promise.resolve([])),
}));

import { useBuilderTheme } from "./builderTheme";

function answer(body: unknown, ok = true) {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok, json: () => Promise.resolve(body) })));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useBuilderTheme", () => {
  it("returns the chosen theme's variables", async () => {
    answer({ variables: { accent: "#D4AF37", font_family: "Georgia, serif" } });
    const { result } = renderHook(() => useBuilderTheme("luxury"));
    await waitFor(() => expect(result.current.variables).toEqual({ accent: "#D4AF37", font_family: "Georgia, serif" }));
    expect(vi.mocked(fetch)).toHaveBeenCalledWith("/api/themes/luxury");
  });

  it("returns no variables for a theme the server does not have", async () => {
    answer({ variables: { accent: "#D4AF37" } });
    const { result, rerender } = renderHook(({ id }) => useBuilderTheme(id), { initialProps: { id: "luxury" } });
    await waitFor(() => expect(result.current.variables).not.toBeNull());
    answer(null, false);
    rerender({ id: "gone" });
    await waitFor(() => expect(result.current.variables).toBeNull());
  });
});
