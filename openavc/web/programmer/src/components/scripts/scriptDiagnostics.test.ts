import { describe, expect, it } from "vitest";
import { scriptMarkers, type MarkerModel } from "./scriptDiagnostics";

const SEVERITY = { warning: 4, error: 8 };

/** A Monaco text model over `source`, as far as the marks read one. */
function model(source: string): MarkerModel {
  const lines = source.split("\n");
  return {
    getValue: () => source,
    getPositionAt(offset: number) {
      const before = source.slice(0, offset).split("\n");
      return { lineNumber: before.length, column: before[before.length - 1].length + 1 };
    },
    getLineCount: () => lines.length,
    getLineLength: (line: number) => lines[line - 1].length,
  };
}

const SOURCE = [
  "from openavc import state, on_state_change",
  'level = state.get("var.volume")',
  "state.set('var.missing', 1)",
  '@on_state_change("var.gone")',
  "def handler(key, old_value, new_value):",
  "    pass",
].join("\n");

describe("script editor marks", () => {
  it("warns on each var. a script names that the project does not define", () => {
    const marks = scriptMarkers(model(SOURCE), SEVERITY, ["volume"], [], []);
    expect(marks).toEqual([
      {
        severity: 4,
        message:
          'Variable "missing" is not defined in this project. Create it in the Project view or in a macro\'s Set Variable step.',
        startLineNumber: 3, startColumn: 12, endLineNumber: 3, endColumn: 23,
      },
      {
        severity: 4,
        message:
          'Variable "gone" is not defined in this project. Create it in the Project view or in a macro\'s Set Variable step.',
        startLineNumber: 4, startColumn: 19, endLineNumber: 4, endColumn: 27,
      },
    ]);
  });

  it("marks a runtime error and a lint warning across their whole line", () => {
    const marks = scriptMarkers(
      model(SOURCE),
      SEVERITY,
      ["volume", "missing", "gone"],
      [{ line: 2, message: "boom" }, { line: 99, message: "past the end" }],
      [{ line: 4, message: "nothing sends this" }],
    );
    expect(marks).toEqual([
      { severity: 8, message: "boom", startLineNumber: 2, startColumn: 1, endLineNumber: 2, endColumn: 32 },
      {
        severity: 4, message: "nothing sends this",
        startLineNumber: 4, startColumn: 1, endLineNumber: 4, endColumn: 29,
      },
    ]);
  });
});
