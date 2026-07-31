/**
 * The one z-index ladder for anything that floats above the page.
 *
 * Every floating layer in the Programmer picks its z-index from here. Before
 * this existed the IDE had five accidental tiers — modals at 100, 1000, 1001
 * and 10000, and picker popovers at 9999 — so whether a dropdown appeared
 * above or below the dialog you opened it from depended entirely on which
 * dialog you happened to be in. A picker that renders behind its own dialog
 * is unusable, and there was no rule to point at that said which should win.
 *
 * The rule now, in order:
 *
 *   modal  <  nested modal  <  popover  <  toast
 *
 * A popover always clears the modal it was opened from (however deep the
 * modals are stacked), and a toast always clears everything, because it is
 * reporting on what just happened and must never be the thing that's hidden.
 *
 * Layers that live *inside* the page — canvas selection handles, sticky
 * headers, drag ghosts — stay in the low hundreds and never come near this
 * ladder. Nothing here should be written as a bare number at a call site;
 * import the constant so the ordering stays readable in one place.
 */
export const LAYER = {
  /** A modal dialog: its backdrop and its panel. */
  modal: 1000,
  /**
   * A modal opened from inside another modal. `Modal` works this out on its
   * own from how many modals are already open, so a call site only names this
   * when it is placing a backdrop by hand.
   */
  modalNested: 1020,
  /**
   * Picker, combobox, and context-menu portals. Above every modal tier so a
   * dropdown is usable no matter which dialog it was opened from.
   */
  popover: 1200,
  /** Toasts. Above everything. */
  toast: 1300,
} as const;

/** Each level of modal nesting adds this much. */
export const MODAL_LAYER_STEP = 20;

/**
 * Nesting past this many modals stops climbing, so a runaway stack can never
 * reach the popover tier and start covering its own dropdowns. Ten deep is
 * far more than the IDE ever opens (the deepest real case is three).
 */
export const MAX_MODAL_DEPTH = 9;

/** The z-index for a modal at `depth` levels of nesting (0 = not nested). */
export function modalLayer(depth: number): number {
  return LAYER.modal + Math.min(Math.max(depth, 0), MAX_MODAL_DEPTH) * MODAL_LAYER_STEP;
}
