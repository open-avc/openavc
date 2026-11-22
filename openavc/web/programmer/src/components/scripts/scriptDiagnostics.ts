/**
 * The marks the script editor puts on a script: a `var.X` it names that is not
 * a project variable, each runtime error on its line, and each lint warning
 * (a handler nothing can reach) on its decorator's line.
 *
 * Takes the editor's model and Monaco's severities, so it is tested without
 * the editor.
 */

import type { RuntimeError } from "./ScriptEditor";

/** The part of a Monaco text model the marks read. */
export interface MarkerModel {
  getValue(): string;
  getPositionAt(offset: number): { lineNumber: number; column: number };
  getLineCount(): number;
  getLineLength(line: number): number;
}

export interface Marker {
  severity: number;
  message: string;
  startLineNumber: number;
  startColumn: number;
  endLineNumber: number;
  endColumn: number;
}

/** `state.get("var.X")`, `state.set("var.X", ...)` and `@on_state_change("var.X")`. */
const VARIABLE_USE = /(?:state\.(?:get|set)|@on_state_change)\(\s*["']var\.([a-zA-Z0-9_]+)["']/g;

/** A warning on each `var.X` a script names where X is not a project variable. */
function undefinedVariableMarkers(model: MarkerModel, severity: number, knownVarIds: string[]): Marker[] {
  const content = model.getValue();
  return [...content.matchAll(VARIABLE_USE)]
    .filter((match) => !knownVarIds.includes(match[1]))
    .map((match) => {
      const key = `var.${match[1]}`;
      const startOffset = match.index! + match[0].indexOf(key);
      const startPos = model.getPositionAt(startOffset);
      const endPos = model.getPositionAt(startOffset + key.length);
      return {
        severity,
        message: `Variable "${match[1]}" is not defined in this project. Create it in the Project view or in a macro's Set Variable step.`,
        startLineNumber: startPos.lineNumber,
        startColumn: startPos.column,
        endLineNumber: endPos.lineNumber,
        endColumn: endPos.column,
      };
    });
}

/** A marker across each whole line named, for the lines the model has. */
function lineMarkers(model: MarkerModel, severity: number, items: RuntimeError[]): Marker[] {
  return items
    .filter((item) => item.line >= 1 && item.line <= model.getLineCount())
    .map((item) => ({
      severity,
      message: item.message,
      startLineNumber: item.line,
      startColumn: 1,
      endLineNumber: item.line,
      endColumn: model.getLineLength(item.line) + 1,
    }));
}

/** Every mark for the script in `model`. */
export function scriptMarkers(
  model: MarkerModel,
  severity: { warning: number; error: number },
  knownVarIds: string[],
  runtimeErrors: RuntimeError[],
  lintWarnings: RuntimeError[],
): Marker[] {
  return [
    ...undefinedVariableMarkers(model, severity.warning, knownVarIds),
    ...lineMarkers(model, severity.error, runtimeErrors),
    // Handlers nothing can reach. A warning and not an error on purpose: the
    // handler is written correctly, and the macro or control that will fire it
    // may simply not exist yet -- building the listener first is a normal order
    // to work in. The line is the decorator's, which is the line to change.
    ...lineMarkers(model, severity.warning, lintWarnings),
  ];
}
