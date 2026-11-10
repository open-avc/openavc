/**
 * When two overlapping controls are stacked on purpose: `mutuallyExclusive`.
 *
 * Mirrors "Deliberately stacked elements" in openavc/ui/page_review.py;
 * tests/test_ui_page_review_mirrors.py holds the operator spellings below to
 * the ones the panel evaluates.
 */

import type { UIElement } from "../../api/types";
import { isPlainObject } from "./authoredValues";
import { showOf } from "./reviewFinding";

//
// Two boxes in the same place are usually a mistake and sometimes the entire
// design: a tab strip is N panels at identical coordinates, each gated by a
// `visible_when` on the same key. Warning about those fires on every page that
// uses the pattern, and a checker that cries wolf on correct work teaches people
// to stop reading it -- which costs more than the collisions it does catch.
//
// So an overlap is suppressed only when the two conditions PROVABLY cannot both
// hold. Everything this cannot prove still warns: a missed collision between two
// conditionally-shown elements costs one warning, and a false one costs the
// credibility of every warning printed beside it.

/** The operator spellings the panel's `_evalConditionOp` understands, by what
 *  they mean; held to the AI side's by tests/test_ui_page_review_mirrors.py. */
const EQ_OPS = new Set(["eq", "equals", "=="]);
const NE_OPS = new Set(["ne", "not_equals", "!="]);
/** Range operators, mapped to whether the bound they set is inclusive. */
const LOWER_OPS: Record<string, boolean> = { gt: false, ">": false, gte: true, ">=": true };
const UPPER_OPS: Record<string, boolean> = { lt: false, "<": false, lte: true, "<=": true };

/** One condition, read off a `visible_when`. */
type Leaf = { key: string; op: string; value: unknown };

/** The kinds of authored literal both surfaces read alike. */
type LiteralKind = "null" | "bool" | "number" | "text";

/**
 * Which kind of authored literal this is, where both surfaces agree; null where
 * they do not.
 *
 * A list or an object as a condition value is not something either surface can
 * reason about, and Python and JavaScript do not even agree on whether an empty
 * one is truthy. Equality, truthiness and the bounds below all ask this first.
 * Mirrors `_literal_kind` in page_review.py.
 */
function literalKind(value: unknown): LiteralKind | null {
  if (value === null) return "null";
  switch (typeof value) {
    case "boolean":
      return "bool";
    case "number":
      return "number";
    case "string":
      return "text";
    default:
      return null;
  }
}

/**
 * Two authored literals, compared the way both surfaces can agree on.
 *
 * Not `==`: Python says `1 == True` and JavaScript says `"1" == 1`, and they
 * disagree about which. Same kind and same value, or different.
 */
function sameValue(left: unknown, right: unknown): boolean {
  const kind = literalKind(left);
  return kind !== null && kind === literalKind(right) && left === right;
}

/** A condition value as a number, or null when it is not one.
 *
 *  A numeric string is deliberately not one: the panel compares it with
 *  JavaScript's coercing `>`, which has no Python equivalent worth mirroring,
 *  and undecidable here only costs a warning nobody needed suppressed. */
function conditionNumber(value: unknown): number | null {
  return literalKind(value) === "number" ? (value as number) : null;
}

/** Whether a key pinned to `value` satisfies `op target`.
 *
 *  True for anything undecidable, so an unreadable pair yields no proof of
 *  exclusivity rather than a false one. */
function conditionHolds(value: unknown, op: string, target: unknown): boolean {
  if (EQ_OPS.has(op)) return sameValue(value, target);
  if (NE_OPS.has(op)) return !sameValue(value, target);
  if (op === "truthy" || op === "falsy") return truthHolds(value, op);
  if (op in LOWER_OPS || op in UPPER_OPS) return boundHolds(value, op, target);
  return true;
}

/** Whether a key pinned to `value` is `truthy` or `falsy`, as `op` asks.
 *
 *  True (undecidable) for a value the two surfaces would not read alike. */
function truthHolds(value: unknown, op: string): boolean {
  if (literalKind(value) === null) return true;
  return op === "truthy" ? !!value : !value;
}

/** Whether a key pinned to `value` is on the near side of the bound `op target`.
 *
 *  True (undecidable) unless both are numbers. */
function boundHolds(value: unknown, op: string, target: unknown): boolean {
  const left = conditionNumber(value);
  const right = conditionNumber(target);
  if (left === null || right === null) return true;
  if (op in LOWER_OPS) return LOWER_OPS[op] ? left >= right : left > right;
  return UPPER_OPS[op] ? left <= right : left < right;
}

/** Whether a lower bound and an upper bound leave no number that meets both.
 *
 *  False (no proof) unless both bounds are numbers. */
function noRoomBetween(lower: Leaf, upper: Leaf): boolean {
  const low = conditionNumber(lower.value);
  const high = conditionNumber(upper.value);
  if (low === null || high === null) return false;
  const closed = LOWER_OPS[lower.op] && UPPER_OPS[upper.op];
  return high < low || (high === low && !closed);
}

/** Whether condition `a` rules out `b`, read in one direction only. */
function rulesOut(a: Leaf, b: Leaf): boolean {
  if (EQ_OPS.has(a.op)) {
    // `key == a.value` pins the key to one value, so the other condition is
    // either satisfied by that value or contradicts it outright.
    return !conditionHolds(a.value, b.op, b.value);
  }
  if (a.op === "truthy") return b.op === "falsy";
  if (a.op in LOWER_OPS && b.op in UPPER_OPS) return noRoomBetween(a, b);
  return false;
}

/** One condition as a leaf, or null if it is unreadable. */
function conditionLeaf(condition: unknown): Leaf | null {
  if (!isPlainObject(condition)) return null;
  const { key, operator, value } = condition;
  if (typeof key !== "string" || !key) return null;
  return { key, op: String(operator || "eq").toLowerCase(), value: value ?? null };
}

/**
 * A `visible_when` as branches of ANDed leaves: visible if any branch is.
 *
 * A bare condition and an `all:` block are one branch; an `any:` block is one
 * branch per condition. Null means there is nothing to reason about, which is
 * not the same as an empty list -- an empty list of branches is never
 * satisfiable, and would suppress every overlap on the page. An unreadable
 * condition is dropped from `all:` and makes `any:` undecidable; each block's
 * reader says why.
 */
function visibleWhenBranches(el: UIElement): Leaf[][] | null {
  const block = showOf(el)?.visible_when;
  if (!isPlainObject(block)) return null;

  if (Array.isArray(block.any)) return anyBranches(block.any);
  if (Array.isArray(block.all)) return allBranch(block.all);
  const leaf = conditionLeaf(block);
  return leaf ? [[leaf]] : null;
}

/** An `any:` block, one branch per condition; null if any one is unreadable.
 *  Dropping an unreadable condition here would NARROW the set, which could
 *  prove an exclusivity that is not there. */
function anyBranches(conditions: unknown[]): Leaf[][] | null {
  const leaves = conditions.map(conditionLeaf);
  if (!leaves.length || leaves.some((leaf) => leaf === null)) return null;
  return leaves.map((leaf) => [leaf as Leaf]);
}

/** An `all:` block as one branch of its readable conditions; null when none is.
 *  Dropping an unreadable condition only widens the set: if the wider condition
 *  is still exclusive, so is the real one. */
function allBranch(conditions: unknown[]): Leaf[][] | null {
  const kept = conditions.map(conditionLeaf).filter((leaf): leaf is Leaf => !!leaf);
  return kept.length ? [kept] : null;
}

/**
 * Whether two elements can never be on screen at the same time.
 *
 * Proof, not inference: every way `a` can be visible has to contradict every way
 * `b` can be. Two conditions contradict when they name the same key and no
 * single value satisfies both -- different `eq` values, an `eq` against its own
 * `ne`, `truthy` against `falsy`, or two bounds that leave no room between them.
 */
export function mutuallyExclusive(a: UIElement, b: UIElement): boolean {
  const aBranches = visibleWhenBranches(a);
  const bBranches = visibleWhenBranches(b);
  if (!aBranches?.length || !bBranches?.length) return false;
  return aBranches.every((aLeaves) =>
    bBranches.every((bLeaves) =>
      aLeaves.some((x) =>
        bLeaves.some((y) => x.key === y.key && (rulesOut(x, y) || rulesOut(y, x))),
      ),
    ),
  );
}
