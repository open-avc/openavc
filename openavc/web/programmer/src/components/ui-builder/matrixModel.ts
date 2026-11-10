/**
 * What a matrix is, once the shorthand is expanded: the TypeScript twin of
 * openavc/ui/matrix_model.py.
 *
 * Each axis (`sources`, `destinations`) is written as a list of entries or as a
 * generator standing for one (`{from: {count, values, labels, <key patterns>},
 * exclude, overrides}`), and both resolve to one list of entries here as there.
 * The RENDERER does not have a copy of this -- the server expands a matrix before
 * the panel or this canvas ever sees one (matrix plan D6) -- but the review
 * does, because the review already exists twice by design and
 * `tests/test_ui_review_parity.py` compares the two message for message. So a
 * resolver that drifts from the Python one changes a sentence and turns that
 * suite red, which is not true of a renderer and is exactly why the renderer
 * went without. A change to how an axis resolves (another key pattern, another
 * way to name an entry) is made here and in matrix_model.py together.
 *
 * Read by the page review's matrix checks, the floor's row count
 * (controlMinimums.ts) and the matrix's properties.
 */
import { isPlainObject, own } from "./authoredValues";

export interface MatrixEntry {
  value: unknown;
  /** Absent when the entry names a live `label_key` and nobody typed a name --
   *  see withDefaultLabel. */
  label?: string;
  label_key?: string;
  route_key?: string;
  audio_route_key?: string;
  lock_key?: string;
  route?: unknown[];
  /** A source's second value: what the DEVICE calls it. See matrixSourceReports. */
  report_value?: unknown;
}

export type MatrixAxisName = "sources" | "destinations";

/** How each axis names an entry nobody labelled. */
const MATRIX_LABEL_PREFIX: Record<MatrixAxisName, string> = {
  sources: "In",
  destinations: "Out",
};

const MATRIX_NUMERIC = /^[+-]?\d+(?:\.\d+)?$/;

/** A routed-source value in comparable form, or null when nothing is routed.
 *
 *  Mirrors `route_value` in matrix_model.py, which mirrors `_routeValue` in
 *  panel.js -- the copy that decides what a panel actually lights. */
export function matrixRouteValue(value: unknown): number | string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return routedNumber(value);
  return routedText(String(value).trim());
}

/** A number a device reported: 0 and the non-finite ones mean nothing is routed. */
function routedNumber(value: number): number | null {
  return Number.isFinite(value) && value !== 0 ? value : null;
}

/** Text a device reported: a number written out compares as that number. */
function routedText(text: string): number | string | null {
  if (text === "" || text === "0") return null;
  if (MATRIX_NUMERIC.test(text)) return Number(text);
  return text.toLowerCase();
}

/** The single digit run in a string, or null when it does not have exactly one. */
function matrixRouteDigits(text: string): number | null {
  const runs = text.match(/\d+/g);
  return runs && runs.length === 1 ? Number(runs[0]) : null;
}

/** Do two routed-source values name the same source? */
export function matrixRouteMatches(a: unknown, b: unknown): boolean {
  const x = matrixRouteValue(a);
  const y = matrixRouteValue(b);
  if (x === null || y === null) return false;
  if (x === y) return true;
  const dx = typeof x === "number" ? x : matrixRouteDigits(x);
  const dy = typeof y === "number" ? y : matrixRouteDigits(y);
  return dx !== null && dx === dy;
}

/** A key pattern with `*` replaced by this entry's value, first `*` only. */
function matrixSubstitute(pattern: unknown, value: unknown): string | undefined {
  if (typeof pattern !== "string" || !pattern) return undefined;
  return pattern.replace("*", String(value));
}

/** The keys a generator writes on each entry from a pattern, in the order an
 *  entry carries them. The pattern table in `_generate`, matrix_model.py. */
const GENERATED_KEYS = ["label_key", "route_key", "audio_route_key", "lock_key"] as const;

/** The values a generator stands for: its `values` list, else 1 to `count`. */
function generatorValues(from: Record<string, unknown>): unknown[] {
  if (Array.isArray(from.values)) return from.values;
  const count = Number(from.count ?? 0);
  const whole = Number.isFinite(count) ? Math.trunc(count) : 0;
  return whole > 0 ? Array.from({ length: whole }, (_, i) => i + 1) : [];
}

/** One generated entry before its override: the value, its typed name, each
 *  pattern filled in with the value, and the shared route list. */
function generatedEntry(
  from: Record<string, unknown>,
  value: unknown,
  label: unknown,
): MatrixEntry {
  const entry: MatrixEntry = { value } as MatrixEntry;
  if (label) entry.label = String(label);
  for (const key of GENERATED_KEYS) {
    const resolved = matrixSubstitute(from[key], value);
    if (resolved) entry[key] = resolved;
  }
  if (Array.isArray(from.route)) entry.route = from.route;
  return entry;
}

/**
 * An entry with no name gets its axis's "In 3" / "Out 2", by its position in
 * what was written, unless it names a live `label_key`: the device is about to
 * say what it is called, and "Out 2" would win over "Lobby TV" the moment the
 * renderer prefers the authored name. Asked last, so an override naming either
 * one is honoured. A generated entry and an authored one both ask it, as
 * `_with_default_label` in matrix_model.py.
 */
function withDefaultLabel(
  entry: { label?: unknown; label_key?: unknown },
  axis: MatrixAxisName,
  position: number,
): MatrixEntry {
  if (!entry.label && !entry.label_key) {
    entry.label = `${MATRIX_LABEL_PREFIX[axis]} ${position + 1}`;
  }
  return entry as unknown as MatrixEntry;
}

/** Expand a generator entry into the list it stands for. */
function generateMatrixAxis(
  spec: Record<string, unknown>,
  axis: MatrixAxisName,
): MatrixEntry[] {
  const from = isPlainObject(spec.from) ? spec.from : {};
  const labels = Array.isArray(from.labels) ? from.labels : [];
  const excluded = new Set((Array.isArray(spec.exclude) ? spec.exclude : []).map(String));
  const overrides = isPlainObject(spec.overrides) ? spec.overrides : {};

  const entries: MatrixEntry[] = [];
  generatorValues(from).forEach((value, position) => {
    if (excluded.has(String(value))) return;
    const entry = generatedEntry(from, value, labels[position]);
    const override = own(overrides, String(value));
    const merged = isPlainObject(override) ? { ...entry, ...override } : entry;
    entries.push(withDefaultLabel(merged, axis, position));
  });
  return entries;
}

/** One authored entry, filled out. A bare scalar means "just this value". */
function normaliseMatrixEntry(
  raw: unknown,
  axis: MatrixAxisName,
  position: number,
): MatrixEntry | null {
  let entry: Record<string, unknown>;
  if (isPlainObject(raw)) entry = { ...raw };
  else if (typeof raw === "string" || typeof raw === "number") entry = { value: raw };
  else return null;
  if (!("value" in entry)) return null;
  return withDefaultLabel(entry, axis, position);
}

/** One axis of a matrix, as the list the renderer draws. */
export function resolveMatrixAxis(config: unknown, axis: MatrixAxisName): MatrixEntry[] {
  if (!isPlainObject(config)) return [];
  const spec = config[axis];
  if (isPlainObject(spec)) return generateMatrixAxis(spec, axis);
  if (Array.isArray(spec)) {
    const entries: MatrixEntry[] = [];
    spec.forEach((raw, position) => {
      const entry = normaliseMatrixEntry(raw, axis, position);
      if (entry) entries.push(entry);
    });
    return entries;
  }
  return [];
}

/**
 * What a source looks like when the DEVICE names it.
 *
 * `value` is what gets SENT and `report_value` is what gets MATCHED; omit it and
 * they are the same, which is every driver in the corpus but one. Mirrors
 * `source_report_value` in openavc/ui/matrix_model.py.
 */
export function matrixSourceReports(entry: MatrixEntry | undefined): unknown {
  if (!entry) return undefined;
  return entry.report_value === undefined || entry.report_value === null
    ? entry.value
    : entry.report_value;
}

/** How many entries this axis draws -- the question controlMinimumBox asks. */
export function matrixAxisCount(config: unknown, axis: string): number {
  return resolveMatrixAxis(config, axis as MatrixAxisName).length;
}
