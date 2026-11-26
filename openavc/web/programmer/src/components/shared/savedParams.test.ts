// The saved-on-this-system markers' cases, the ones `tests/test_saved_markers.py`
// runs against the server's rule: each value reads as the login marker, a saved
// parameter's marker, or neither, alike on both sides.

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { isSavedParam, savedParamName } from "./savedParams";
import { SAVED_LOGIN } from "../../views/devices/deviceLogins";

type Case = { name: string; value: unknown; login: boolean; param: boolean; param_name: string | null };

// Read off disk rather than imported: the cases live outside this Vite
// project, and vitest runs with openavc/web/programmer as the cwd.
const { cases }: { cases: Case[] } = JSON.parse(
  readFileSync(resolve(process.cwd(), "../../../tests/fixtures/saved_marker_cases.json"), "utf-8"),
);

describe("saved-on-this-system markers", () => {
  it("has a case of each kind", () => {
    expect(cases.some((c) => c.login)).toBe(true);
    expect(cases.some((c) => c.param)).toBe(true);
    expect(cases.some((c) => !c.login && !c.param)).toBe(true);
  });

  it.each(cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(c.value === SAVED_LOGIN).toBe(c.login);
    expect(isSavedParam(c.value)).toBe(c.param);
    expect(savedParamName(c.value)).toBe(c.param_name);
  });
});
