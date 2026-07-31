import {
  useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode,
} from "react";
import { modalLayer } from "./layers";

/**
 * The one modal in the Programmer IDE.
 *
 * Everything that dims the page and puts a panel in front of it goes through
 * here: the backdrop, the z-index, Escape, the focus trap, returning focus
 * where it came from, and the ARIA that tells a screen reader this is a
 * dialog. Call sites bring their panel's content and its look; they do not
 * bring their own overlay.
 *
 * This replaced twenty separate overlays. Three of them were shared
 * components that each had their own copy of the focus trap, and seventeen
 * were hand-rolled at the point of use — of which none trapped focus and only
 * two could be closed with the keyboard at all. If you are adding a dialog,
 * you want this; if this doesn't do what your dialog needs, add the option
 * here rather than starting a twenty-first overlay.
 */

const FOCUSABLE =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

interface StackEntry {
  /** The modal's backdrop element — what decides where it sits in the stack. */
  el: HTMLElement;
  setDepth: (depth: number) => void;
}

/**
 * Every open modal, bottom of the stack first. Escape and the Tab trap answer
 * only to the last one, so a confirm opened on top of a dialog takes the
 * Escape for itself and leaves the dialog underneath open — which is what
 * people expect, and what the old per-dialog `document` listeners could not do
 * (they all fired at once, so one Escape closed the whole stack).
 *
 * The order comes from where each backdrop sits in the document, NOT from the
 * order the modals mounted: React runs a child's effects before its parent's,
 * so a modal nested inside another registers first and mount order would name
 * the wrong one as top-most.
 */
const openModals: StackEntry[] = [];

/** True when `b` paints over `a`: it is nested inside it, or comes after it. */
function isAbove(a: HTMLElement, b: HTMLElement): boolean {
  return (a.compareDocumentPosition(b) & a.DOCUMENT_POSITION_FOLLOWING) !== 0;
}

/** Re-sort the stack and tell each modal how deep it now sits. */
function restack(): void {
  openModals.sort((x, y) => (x.el === y.el ? 0 : isAbove(x.el, y.el) ? -1 : 1));
  openModals.forEach((entry, depth) => entry.setDepth(depth));
}

function isTopMost(entry: StackEntry | null): boolean {
  return entry !== null && openModals[openModals.length - 1] === entry;
}

/**
 * Keypresses a modal has already answered.
 *
 * Every modal listens on `document`, so one Escape reaches all of them in
 * registration order — and answering it can change who is top-most before the
 * next listener runs, because the browser flushes a discrete event like keydown
 * synchronously. Without this, Escape on a confirm stacked over a dialog closed
 * the confirm and then let the dialog underneath answer the same keypress, which
 * for a dialog that re-raises its confirm looked like Escape doing nothing at all.
 */
const answeredKeys = new WeakSet<KeyboardEvent>();

export interface ModalProps {
  /**
   * Dismiss handler for Escape and a backdrop click. Leave it off for a modal
   * the user must not dismiss (an update in progress, say) — then neither
   * gesture does anything.
   */
  onClose?: () => void;
  /** Accessible name. Use `labelledBy` instead when a heading already says it. */
  label?: string;
  labelledBy?: string;
  describedBy?: string;
  /** "alertdialog" for a confirm that needs an answer before anything else. */
  role?: "dialog" | "alertdialog";
  /** Off for dialogs where a stray click outside must not throw away input. */
  closeOnBackdrop?: boolean;
  closeOnEscape?: boolean;
  /**
   * Where focus lands when the modal opens: a CSS selector inside the panel,
   * or "none" to leave focus alone. Defaults to the first focusable thing in
   * the panel.
   */
  initialFocus?: string;
  /** Select the text in the initially-focused field (a prefilled input). */
  selectOnFocus?: boolean;
  /** Overrides for the backdrop — a heavier dim, a different alignment. */
  overlayStyle?: CSSProperties;
  /** The panel's own look: width, padding, layout. */
  panelStyle?: CSSProperties;
  panelClassName?: string;
  children: ReactNode;
}

export function Modal({
  onClose,
  label,
  labelledBy,
  describedBy,
  role = "dialog",
  closeOnBackdrop = true,
  closeOnEscape = true,
  initialFocus,
  selectOnFocus = false,
  overlayStyle,
  panelStyle,
  panelClassName,
  children,
}: ModalProps) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const previousFocusRef = useRef<Element | null>(null);
  const entryRef = useRef<StackEntry | null>(null);

  // How many modals this one sits on top of, which is its rung on the ladder.
  // Joining the stack is a layout effect so the height is settled before the
  // browser paints — no frame at the wrong z-index.
  const [depth, setDepth] = useState(0);
  useLayoutEffect(() => {
    const entry: StackEntry = { el: overlayRef.current as HTMLElement, setDepth };
    entryRef.current = entry;
    openModals.push(entry);
    restack();
    return () => {
      const at = openModals.indexOf(entry);
      if (at !== -1) openModals.splice(at, 1);
      entryRef.current = null;
      restack();
    };
  }, []);

  // Put focus in the panel on open, and give it back to whatever had it when
  // the modal closes. The options are read once on purpose: a dialog that
  // changed where focus should start, mid-life, would yank the cursor out of
  // whatever the user was typing in.
  const focusOptions = useRef({ initialFocus, selectOnFocus });
  useEffect(() => {
    const { initialFocus: want, selectOnFocus: select } = focusOptions.current;
    previousFocusRef.current = document.activeElement;
    const frame = requestAnimationFrame(() => {
      const panel = panelRef.current;
      if (!panel || want === "none") return;
      const target = want
        ? panel.querySelector<HTMLElement>(want)
        : panel.querySelector<HTMLElement>(FOCUSABLE);
      const el = target ?? panel;
      el.focus();
      if (select && (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)) {
        el.select();
      }
    });
    return () => {
      cancelAnimationFrame(frame);
      (previousFocusRef.current as HTMLElement)?.focus?.();
    };
  }, []);

  // The key handler subscribes ONCE and reads the current props from here.
  // Re-subscribing when `onClose` changes identity (every render, since call
  // sites pass inline arrows) meant the listener could be removed and re-added
  // *during* a keydown dispatch — and a listener re-added mid-dispatch is not
  // called for that event. Live in the UI Builder, whose own Escape handler
  // renders before ours runs, that ate the keypress outright: Escape on a
  // stacked dialog did nothing at all.
  const latest = useRef({ onClose, closeOnEscape });
  latest.current = { onClose, closeOnEscape };

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Only the top-most modal listens, and only once per keypress.
      if (answeredKeys.has(e)) return;
      if (!isTopMost(entryRef.current)) return;
      answeredKeys.add(e);
      if (e.key === "Escape") {
        const { onClose: close, closeOnEscape: allowed } = latest.current;
        if (allowed && close) close();
        return;
      }
      if (e.key === "Tab" && panelRef.current) {
        const focusable = panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE);
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, []);

  // Only a click that lands on the backdrop itself counts. A click that
  // started on something inside the panel — a dropdown option that unmounts
  // under the pointer, say — never reaches here as the backdrop.
  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!closeOnBackdrop || !onClose) return;
    if (e.target !== e.currentTarget) return;
    onClose();
  };

  return (
    <div
      ref={overlayRef}
      onClick={handleBackdropClick}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: modalLayer(depth),
        ...overlayStyle,
      }}
    >
      <div
        ref={panelRef}
        role={role}
        aria-modal="true"
        aria-label={labelledBy ? undefined : label}
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        tabIndex={-1}
        className={panelClassName}
        style={{
          background: "var(--bg-elevated)",
          borderRadius: "var(--border-radius)",
          boxShadow: "var(--shadow-lg)",
          outline: "none",
          ...panelStyle,
        }}
      >
        {children}
      </div>
    </div>
  );
}

/** How many modals are open right now. Exported for the regression tests. */
export function openModalCount(): number {
  return openModals.length;
}

/**
 * True while any modal is open.
 *
 * A view's own keyboard shortcuts should stand down while a dialog is up: the
 * canvas is behind a backdrop the user can't see past, so Delete, the arrow
 * keys and Escape are meant for the dialog, not for whatever is selected
 * underneath it.
 */
export function isModalOpen(): boolean {
  return openModals.length > 0;
}
