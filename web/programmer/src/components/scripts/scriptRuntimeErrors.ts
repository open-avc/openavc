// Pure extraction of inline editor error markers from the log ring, split out
// of ScriptView so it's unit-testable (the ScriptView memo just calls it, and
// re-runs it reactively when a new script-error entry arrives).
import type { LogEntry } from "../../store/logStore";
import type { RuntimeError } from "./ScriptEditor";

/**
 * Editor markers for the selected script: ERROR-level, script-category log
 * entries whose message names the script (by id or file) and carries a
 * `line N` location. The message is trimmed to its first line for the marker.
 */
export function extractScriptRuntimeErrors(
  entries: LogEntry[],
  selectedId: string,
  scriptFile: string,
): RuntimeError[] {
  const errors: RuntimeError[] = [];
  for (const entry of entries) {
    if (entry.level !== "ERROR" || entry.category !== "script") continue;
    if (!entry.message.includes(selectedId) && !entry.message.includes(scriptFile)) continue;
    const lineMatch = entry.message.match(/line (\d+)/);
    if (lineMatch) {
      errors.push({ line: parseInt(lineMatch[1], 10), message: entry.message.split("\n")[0] });
    }
  }
  return errors;
}

/**
 * The id of the most recent ERROR-level script-category log entry, or 0 when
 * there is none. A primitive (never a new object → no React 19 crash) that
 * changes only when a new script error is logged — the narrow useLogStore
 * subscription ScriptView uses to re-run the marker memo, instead of
 * subscribing to the whole rapidly-updating logEntries array.
 */
export function latestScriptErrorId(entries: LogEntry[]): number {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (e.level === "ERROR" && e.category === "script") return e.id;
  }
  return 0;
}
