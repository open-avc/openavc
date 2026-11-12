/**
 * In-place text editing on the design canvas: the authored field an edit writes.
 *
 * The panel records which authored field produced the text on screen and hands
 * that path back when an edit commits (`recordSource` in the panel's modules/text_edit.js). These two
 * turn such a path into a patch the element write door already takes.
 */
import type { UIElement } from "../../api/types";

/**
 * Slots the renderer reads by TRUTHINESS rather than by `!== undefined`, so an
 * empty string there does not blank the text -- it falls through to the next
 * thing in the chain.
 *
 * `evaluateFeedback`'s binary branch tests `isActive && binding.label_active`
 * and `evaluateToggleLook` tests `word !== ''`, both falling back to the
 * element's own `label`. Its multi-state branch, by contrast, tests
 * `appearance.label !== undefined`, so an empty string there really does blank
 * it. Clearing one of these four is therefore the author asking for the
 * fallback, and the honest write is to REMOVE the key rather than store a ""
 * that reads as a different word on screen.
 */
const TRUTHINESS_READ_SLOTS = new Set([
  "label_active",
  "label_inactive",
  "on_label",
  "off_label",
]);

/**
 * Whether committing an empty string at this path should delete the key
 * instead of storing "".
 */
export function emptyCommitDeletes(path: (string | number)[]): boolean {
  const last = path[path.length - 1];
  return typeof last === "string" && TRUTHINESS_READ_SLOTS.has(last);
}

type PathContainer = Record<string | number, unknown>;

/** A copy of what a path segment indexes into: a list for a position, an
 *  object for a field name. Anything else found there is replaced by an empty
 *  one, so a path always lands. */
function containerCopy(node: unknown, seg: string | number): PathContainer {
  if (typeof seg === "number") return (Array.isArray(node) ? [...node] : []) as unknown as PathContainer;
  return { ...(node && typeof node === "object" ? node : {}) } as PathContainer;
}

/**
 * Turn a recorded source path plus a new string into a patch for the element.
 *
 * Returns a patch containing only the top-level key the path reaches into, so
 * the existing `handlePropertyChange` write door -- and the undo burst it
 * already runs -- work unchanged. Structures along the way are cloned, never
 * mutated: the project object in the store is shared with the render.
 *
 * An empty value at a truthiness-read slot deletes that key (see
 * `TRUTHINESS_READ_SLOTS`). Everywhere else it stores the empty string, which
 * is what blanking those fields means.
 *
 * Array segments stay arrays -- `bindings.do.press` is a list whose first
 * action carries the toggle words.
 */
export function patchForTextPath(
  element: UIElement,
  path: (string | number)[],
  value: string,
): Partial<UIElement> | null {
  if (path.length === 0) return null;
  const drop = value === "" && emptyCommitDeletes(path);

  // `drop` is only ever true for a path ending in a field name.
  const write = (node: unknown, depth: number): unknown => {
    const seg = path[depth];
    const copy = containerCopy(node, seg);
    if (depth < path.length - 1) copy[seg] = write(copy[seg], depth + 1);
    else if (drop) delete copy[seg];
    else copy[seg] = value;
    return copy;
  };

  const top = path[0];
  if (typeof top !== "string") return null;
  // The element itself is the outermost container, so depth 0 is `top`. One
  // walk covers a bare `label` and a five-deep look slot alike.
  const next = write(element, 0) as Record<string, unknown>;
  return { [top]: next[top] } as Partial<UIElement>;
}
