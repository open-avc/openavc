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

/** The shape the server keeps for a script that failed while running. */
export interface StoredRuntimeError {
  count: number;
  handler: string;
  event: string;
  error: string;
  traceback: string;
  at: number;
}

/**
 * The last failure in the words Python used for it.
 *
 * `error` on its own is `str(exc)`, and for whole families of exception that
 * is not a sentence: a `KeyError` stringifies to the key alone, so a button
 * that failed reports `'laptop'` and the author learns nothing. The
 * traceback's last line is the same error with its type in front --
 * `KeyError: 'laptop'` -- so take that when it is demonstrably the same
 * error, and the plain one otherwise (a timeout has no traceback at all).
 */
export function describeStoredError(record: StoredRuntimeError): string {
  const lines = (record.traceback ?? "").trimEnd().split("\n");
  const last = (lines[lines.length - 1] ?? "").trim();
  return last !== record.error && last.endsWith(record.error) ? last : record.error;
}

/**
 * An editor marker for the last failure the SERVER recorded, from its stored
 * traceback.
 *
 * The log-ring extraction above only ever sees what is still in the buffer and
 * what happens to carry a `line N`, so a handler that threw before the IDE was
 * opened -- or long enough ago to have rolled out of the ring -- left the
 * editor unmarked. The server's record does not expire, so this is the marker
 * that is there at 9am for the failure that happened at 3.
 *
 * Takes the DEEPEST frame in the script's own file: a handler that fails
 * inside a helper it called has frames for both, and the line worth opening
 * the editor at is the one that actually raised. Frames in other files (the
 * engine, the standard library) are not this script's to show. Returns null
 * when the traceback names no line in the file, which is the honest answer for
 * a timeout -- it has no traceback at all.
 */
export function markerFromStoredError(
  record: StoredRuntimeError | undefined,
  scriptFile: string,
): RuntimeError | null {
  if (!record || !record.traceback || !scriptFile) return null;
  // `  File "/path/to/scripts/room_logic.py", line 12, in handle`
  const framePattern = /File "([^"]+)", line (\d+)/g;
  let line: number | null = null;
  for (const match of record.traceback.matchAll(framePattern)) {
    if (fileMatches(match[1], scriptFile)) line = parseInt(match[2], 10);
  }
  if (line === null) return null;
  return { line, message: `${record.handler}: ${describeStoredError(record)}` };
}

/** Does a traceback frame's path point at this script's file? The record is
 *  written server-side, so the path is absolute and in the server's own
 *  separator; the project only knows the file's name. */
function fileMatches(framePath: string, scriptFile: string): boolean {
  const base = (p: string) => p.split(/[\\/]/).pop() ?? p;
  return base(framePath) === base(scriptFile);
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
