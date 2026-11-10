/**
 * What the page review says, and what every one of its checks measures with.
 *
 * A finding (`ReviewFinding`: the element, its kind, one sentence that stands on
 * its own, and the key that makes it the same finding in two arrangements), the
 * box a check measures (`Seat`) and the containers above it (`Container`, the
 * chain ending at `pageContainer`), an element's `show` bindings (`showOf`,
 * which Validate reads too), and the words a sentence is built from. A new kind
 * of finding is a new member of `ReviewFinding.kind`.
 *
 * Mirrors the shared head of openavc/ui/page_review.py: `Finding`, `Container`,
 * `Seat`, `page_container`, `_show`, `_article` and `_joined`.
 */

import type { UIElement } from "../../api/types";
import { referenceBox } from "./touchTarget";

/** One thing that will not draw the way it was written. */
export interface ReviewFinding {
  elementId: string;
  /** Groups findings for a caller that wants to count or filter them. */
  kind:
    | "too_small_for_contents"
    | "small_touch_target"
    | "outside_its_container"
    | "overlap"
    | "no_placement"
    | "binding_not_rendered"
    | "binding_without_key"
    | "binding_not_dispatched"
    | "property_not_rendered"
    | "nothing_to_draw"
    | "keypad_never_sends"
    | "keypad_sends_early"
    | "unknown_element_type"
    | "style_too_large"
    | "too_small_to_draw"
    | "matrix_not_configured"
    | "matrix_config_unread"
    | "matrix_no_route_feedback"
    | "matrix_default_size"
    | "matrix_duplicate_values"
    | "matrix_lock_unbacked"
    | "custom_page_elements_not_drawn"
    | "custom_page_without_a_file"
    | "covers_master";
  /** The whole finding in one self-contained sentence. */
  message: string;
  /** What makes a finding the same finding across two arrangements -- an
   *  element can collide with three neighbours, and all three are worth saying
   *  once each rather than once per layout that inherits the collision. */
  key: string;
}

/** An element's `show` bindings, when it carries an object of them.
 *
 *  Every review check that reads a slot (`value`, `look`, `items`,
 *  `visible_when`) starts here. Mirrors `_show` in page_review.py, which
 *  also turns away a list; no check here reads a slot a list has. */
export function showOf(el: UIElement): Record<string, unknown> | null {
  const show = ((el.bindings ?? {}) as Record<string, unknown>).show;
  return show && typeof show === "object" ? (show as Record<string, unknown>) : null;
}

/** One box in an element's ancestry, as a remedy would have to name it. */
export interface Container {
  /** How it reads mid-sentence: `'grp_strip'`, or `the page` for the root. */
  label: string;
  widthPx: number;
  heightPx: number;
}

/**
 * Where one box is drawn, in reference pixels: what every per-box check measures.
 *
 * Its own size, the container it has to fit (and how a sentence names that
 * container), the containers above that one, innermost first, and which way
 * round the screen is.
 */
export interface Seat {
  boxPx: { width: number; height: number };
  parentPx: { width: number; height: number };
  parentName: string;
  ancestors: Container[];
  orientation: string;
}

/** The root every chain ends at. It is the one box no remedy can grow.
 *
 *  A function rather than a constant because a portrait arrangement is drawn on
 *  the same screen turned. Mirrors `page_container` in page_review.py. */
export function pageContainer(orientation: string = "landscape"): Container {
  const ref = referenceBox(orientation);
  return { label: "the page", widthPx: ref.width, heightPx: ref.height };
}

/** `a` or `an`, so a type name reads as a sentence rather than a token. */
export function article(word: string): string {
  const first = word.slice(0, 1).toLowerCase();
  return first && "aeiou".includes(first) ? "an" : "a";
}

/** Python's `str.capitalize` on a word this short: first letter up. */
export function capitalized(word: string): string {
  return word.slice(0, 1).toUpperCase() + word.slice(1);
}

/** A list read out loud: one, one and another, or one, another and a third. */
export function joined(parts: string[]): string {
  if (parts.length < 3) return parts.join(" and ");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}
