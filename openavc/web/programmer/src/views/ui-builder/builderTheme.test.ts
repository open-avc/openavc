import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// The UI Builder reads the chosen theme once, and Panel Settings shows a blank
// accent and font from what that read returned: the theme's own variables, or
// null when the theme cannot be read.

vi.mock("../../api/restClient", () => ({
  getTunnelPrefix: () => "",
  listThemes: vi.fn(() => Promise.resolve([])),
}));

import { blankThemeId, projectThemeId, useBuilderTheme } from "./builderTheme";
import { listThemes } from "../../api/restClient";
import { useThemeFilesStore } from "../../store/themeFilesStore";

type Case = { name: string; settings: Record<string, unknown>; expected: string };

// The same table tests/test_blank_theme_parity.py runs through the project
// schema and the panel. Read off disk: it lives outside this Vite project, and
// vitest runs with openavc/web/programmer as the cwd.
const casesPath = resolve(process.cwd(), "../../../tests/fixtures/blank_theme_cases.json");
const cases: Case[] = JSON.parse(readFileSync(casesPath, "utf-8")).cases;

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

  // Another tab, the assistant or a project replaced changed a saved theme:
  // the list and the theme drawn are read again, not only on mount.
  it("reads the theme list and the theme again when a saved theme changes", async () => {
    answer({ variables: { accent: "#111111" } });
    vi.mocked(listThemes).mockClear();
    const { result } = renderHook(() => useBuilderTheme("hall-look"));
    await waitFor(() => expect(result.current.variables).toEqual({ accent: "#111111" }));
    expect(vi.mocked(listThemes)).toHaveBeenCalledTimes(1);

    answer({ variables: { accent: "#222222" } });
    act(() => useThemeFilesStore.getState().bump());
    await waitFor(() => expect(result.current.variables).toEqual({ accent: "#222222" }));
    expect(vi.mocked(listThemes)).toHaveBeenCalledTimes(2);
  });
});

describe("the theme a project draws", () => {
  it("has cases", () => {
    expect(cases.length).toBeGreaterThan(10);
  });

  for (const c of cases) {
    it(c.name, () => {
      expect(projectThemeId(c.settings as never)).toBe(c.expected);
    });
  }

  it("reads no settings at all as a project with nothing set", () => {
    expect(projectThemeId(undefined)).toBe(projectThemeId({}));
  });

  it("is the light or dark setting's own theme once the id is taken away", () => {
    expect(blankThemeId({ theme: "light", theme_id: "paper" } as never)).toBe("light-modern");
    expect(blankThemeId({ theme: "dark", theme_id: "paper" } as never)).toBe("dark-default");
  });
});
