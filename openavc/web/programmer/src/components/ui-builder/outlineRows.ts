/**
 * The Outline's rows: a page's controls as the tree the Outline draws.
 *
 * Each row is a control at its depth under its container (`containerOf`, in
 * pageGeometry.ts), whether it has children and is folded, and its neighbours
 * among its own siblings, which the Outline's order buttons trade places with
 * (`swapElementsInOrder`). A search keeps every match and every container
 * above one, and ignores folding; a control a hand-edited parent cycle cuts off
 * from the page is listed at page level after the tree, so it can be found and
 * fixed.
 *
 * The rule worth knowing before changing anything here: the walks guard against
 * a cycle or a repeated id each its own way, and containerTree.test.ts pins
 * what each guard makes the rows say. The reach walk's skip of an element it
 * has already reached changes no answer; it bounds the work when ids repeat.
 *
 * Read by the Outline (OutlinePanel.tsx); it reads only the container rule.
 */
import type { UIElement } from "../../api/types";
import { containerOf } from "./pageGeometry";

/** One row of the Outline tree. */
export interface OutlineRow {
  id: string;
  /** Nesting depth, 0 for a page-level element. Drives the indent. */
  depth: number;
  hasChildren: boolean;
  collapsed: boolean;
  /** The visible neighbour under the SAME parent. Z-order inside a container is
   *  position among its siblings, so this is what the order buttons swap with —
   *  the row above might be a child of something else entirely. */
  prevSiblingId?: string;
  nextSiblingId?: string;
}

/** Each element's children, keyed by its container's id; page level is null. */
type ChildLists = Map<string | null, UIElement[]>;

/**
 * Flatten a page's elements into the rows the Outline draws.
 *
 * Containers are real parents now, so the panel that lists them has to be a
 * tree or the hierarchy is invisible in the one place it should be obvious.
 * A search keeps any row that matches or has a match somewhere under it, and
 * ignores collapse -- a hit three levels down is useless if the containers
 * above it were filtered away.
 */
export function outlineRows(
  elements: UIElement[],
  opts: { collapsed?: Iterable<string>; matchIds?: ReadonlySet<string> | null } = {},
): OutlineRow[] {
  const collapsed = new Set(opts.collapsed ?? []);
  const match = opts.matchIds ?? null;
  const children = childLists(elements);
  const kept = match ? searchKept(children, match) : null;
  const shown = (el: UIElement) => !kept || kept.has(el.id);
  return [
    ...treeRows(children, shown, (id) => !match && collapsed.has(id)),
    ...strandedRows(elements, reachableIds(children), match),
  ];
}

function childLists(elements: UIElement[]): ChildLists {
  const ids = new Set(elements.map((e) => e.id));
  const byParent: ChildLists = new Map();
  for (const el of elements) {
    const key = containerOf(el, ids);
    const list = byParent.get(key);
    if (list) list.push(el);
    else byParent.set(key, [el]);
  }
  return byParent;
}

/** What a search keeps: every match, and every container with a match under it. */
function searchKept(children: ChildLists, match: ReadonlySet<string>): Set<string> {
  const kept = new Set<string>();
  const keep = (el: UIElement, seen: Set<string>): boolean => {
    let show = match.has(el.id);
    for (const child of children.get(el.id) ?? []) {
      if (seen.has(child.id)) continue;
      if (keep(child, new Set(seen).add(child.id))) show = true;
    }
    if (show) kept.add(el.id);
    return show;
  };
  for (const el of children.get(null) ?? []) keep(el, new Set([el.id]));
  return kept;
}

/**
 * Everything with a path down from a root, whether or not it is drawn today.
 * Kept apart from the rows, so a folded container is not mistaken for an
 * unreachable one.
 */
function reachableIds(children: ChildLists): Set<string> {
  const reachable = new Set<string>();
  const reach = (siblings: UIElement[], seen: Set<string>) => {
    for (const el of siblings) {
      if (reachable.has(el.id)) continue;
      reachable.add(el.id);
      reach((children.get(el.id) ?? []).filter((c) => !seen.has(c.id)), new Set([...seen, el.id]));
    }
  };
  reach(children.get(null) ?? [], new Set());
  return reachable;
}

/** The rows down from the page: each shown control, then its children unless it is folded. */
function treeRows(
  children: ChildLists,
  shown: (el: UIElement) => boolean,
  folded: (id: string) => boolean,
): OutlineRow[] {
  const rows: OutlineRow[] = [];
  const emit = (siblings: UIElement[], depth: number, seen: Set<string>) => {
    const visible = siblings.filter(shown);
    visible.forEach((el, i) => {
      const kids = (children.get(el.id) ?? []).filter((c) => !seen.has(c.id));
      const hasChildren = kids.some(shown);
      const isCollapsed = folded(el.id) && hasChildren;
      rows.push({ id: el.id, depth, hasChildren, collapsed: isCollapsed, ...neighboursAt(visible, i) });
      if (hasChildren && !isCollapsed) emit(kids, depth + 1, new Set([...seen, el.id]));
    });
  };
  emit(children.get(null) ?? [], 0, new Set());
  return rows;
}

/**
 * A hand-edited parent cycle leaves elements with no path down from a root.
 * The renderer drops them; the Outline is where you would go to fix that, so
 * it shows them at page level rather than pretending they aren't there.
 */
function strandedRows(
  elements: UIElement[],
  reachable: Set<string>,
  match: ReadonlySet<string> | null,
): OutlineRow[] {
  const stranded = elements.filter(
    (el) => !reachable.has(el.id) && (!match || match.has(el.id)),
  );
  return stranded.map((el, i) => ({
    id: el.id, depth: 0, hasChildren: false, collapsed: false, ...neighboursAt(stranded, i),
  }));
}

/** The rows beside the i'th of a run of siblings, which the order buttons trade with. */
function neighboursAt(siblings: UIElement[], i: number): Pick<OutlineRow, "prevSiblingId" | "nextSiblingId"> {
  return { prevSiblingId: siblings[i - 1]?.id, nextSiblingId: siblings[i + 1]?.id };
}
