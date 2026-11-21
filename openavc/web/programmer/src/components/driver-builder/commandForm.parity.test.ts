import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { commandForm, commandRoute, declaredForms } from "./validateDriver";
import { commandShapeMismatch } from "./liveTestHelpers";
import type { DriverCommandDef } from "../../api/types";

// Which form a command or a setting's write is sent in, held to the platform's
// answer: the same case file drivers/command_form.py and every server-side
// reader run (tests/test_command_form.py).
type Case = {
  name: string;
  origin: "command" | "setting_write";
  block: Record<string, unknown>;
  declared: string[];
  form: string | null;
};

// Read off disk rather than imported: the corpus lives outside this Vite
// project, and vitest runs with openavc/web/programmer as its cwd.
const corpusPath = resolve(process.cwd(), "../../../tests/fixtures/command_form_cases.json");
const cases: Case[] = JSON.parse(readFileSync(corpusPath, "utf-8")).cases;

describe("command form parity corpus", () => {
  it("has cases", () => {
    expect(cases.length).toBeGreaterThan(20);
  });

  for (const c of cases) {
    it(c.name, () => {
      expect(declaredForms(c.block, c.origin)).toEqual(c.declared);
      expect(commandForm(c.block, c.origin)).toBe(c.form);
    });
  }

  it("routes a command that fills no form as a line", () => {
    expect(commandRoute({ send: "" })).toBe("raw");
    expect(commandRoute({ send: "PWR1\r", path: "" })).toBe("raw");
  });
});

describe("the Live Test's shape check", () => {
  it("sends a line beside an empty path or address on a TCP driver", () => {
    const emptyPath = { send: "PWR1\r", path: "" } as unknown as DriverCommandDef;
    const nullAddress = { send: "PWR1\r", address: null } as unknown as DriverCommandDef;
    expect(commandShapeMismatch("tcp", emptyPath)).toBeNull();
    expect(commandShapeMismatch("tcp", nullAddress)).toBeNull();
  });

  it("still stops an OSC command on a TCP driver", () => {
    const osc = { address: "/x" } as unknown as DriverCommandDef;
    expect(commandShapeMismatch("tcp", osc)).toMatch(/OSC fields/);
  });
});
