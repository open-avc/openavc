// A script's failures as the Scripts view shows them: the row in the list and
// the editor's mark, both from the server's record (`GET /api/scripts/errors`),
// split out of ScriptView so they are unit-testable.
import type { LogEntry } from "../../store/logStore";
import type { RuntimeError } from "./ScriptEditor";

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
 * The record is the only place a script's line is to be had: a log line
 * carries the failure's sentence and never its traceback. It does not expire
 * either, so this is the marker that is there at 9am for the failure that
 * happened at 3.
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

/**
 * An editor marker for a script that did not load, from the server's load
 * error (`GET /api/scripts/errors`, and a reload's answer).
 *
 * Python words a syntax, indentation or tab error as what is wrong followed by
 * `(<file>, line N)`, and the sentence before it can name another line ("after
 * function definition on line 1"), so the line is read from that ending alone.
 * Returns null for a load error with no such ending: a top-level line that
 * raised, whose message carries no line.
 */
export function markerFromLoadError(
  error: string | undefined,
  scriptFile: string,
): RuntimeError | null {
  if (!error || !scriptFile) return null;
  const where = error.match(/\(([^()]+), line (\d+)\)\s*$/);
  if (!where || !fileMatches(where[1], scriptFile)) return null;
  return { line: parseInt(where[2], 10), message: `This script did not load: ${error}` };
}

/** A script's last failure as the Scripts list shows it. */
export interface RuntimeErrorRow {
  count: number;
  handler: string;
  event: string;
  /** The readable error (`describeStoredError`). */
  error: string;
  /** Whether the editor marks a line in the script for it. A timeout, or a
   *  control's call refused before the function ran, has no line to mark. */
  marksLine: boolean;
}

/** The row's hover text: what failed, on what, and where to look when the
 *  editor has a line to show. */
export function runtimeErrorTitle(row: RuntimeErrorRow): string {
  const error = row.error.trimEnd();
  const sentence = /[.!?]$/.test(error) ? error : `${error}.`;
  const where = row.marksLine ? " Open the script to see where." : "";
  return `${row.handler} failed on ${row.event}: ${sentence}${where}`;
}

/** The Scripts list's rows, from the server's records: the readable error,
 *  and whether the editor has a line in that script's file to mark. */
export function toRuntimeErrorRows(
  records: Record<string, StoredRuntimeError>,
  scripts: { id: string; file: string }[],
): Record<string, RuntimeErrorRow> {
  const fileOf = new Map(scripts.map((s) => [s.id, s.file]));
  const rows: Record<string, RuntimeErrorRow> = {};
  for (const [id, record] of Object.entries(records)) {
    rows[id] = {
      count: record.count,
      handler: record.handler,
      event: record.event,
      error: describeStoredError(record),
      marksLine: markerFromStoredError(record, fileOf.get(id) || id) !== null,
    };
  }
  return rows;
}

/** Does a path the server wrote (a traceback frame's, or a load error's) point
 *  at this script's file? It may be absolute and in the server's own
 *  separator; the project only knows the file's name. */
function fileMatches(framePath: string, scriptFile: string): boolean {
  const base = (p: string) => p.split(/[\\/]/).pop() ?? p;
  return base(framePath) === base(scriptFile);
}

/**
 * The id of the most recent ERROR-level script-category log entry, or 0 when
 * there is none. A primitive (never a new object → no React 19 crash) that
 * changes only when a new script error is logged — the narrow useLogStore
 * subscription useScriptErrorsRefresh re-asks the record on, instead of
 * subscribing to the whole rapidly-updating logEntries array.
 */
export function latestScriptErrorId(entries: LogEntry[]): number {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (e.level === "ERROR" && e.category === "script") return e.id;
  }
  return 0;
}
