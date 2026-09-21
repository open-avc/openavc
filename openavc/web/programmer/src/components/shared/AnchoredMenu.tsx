import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { LAYER } from "./layers";

/**
 * A dropdown that escapes whatever is clipping its trigger.
 *
 * A row menu is usually a child of the row, inside a card that sets
 * `overflow: hidden` for its rounded corners. An absolutely positioned menu is
 * clipped by that card no matter how high its z-index goes, so on the last row
 * of a list the menu is cut off at the card's bottom edge. The Project
 * Library's menu lost Duplicate, Export and Delete that way, leaving only the
 * one action the row already performs when clicked.
 *
 * So the menu renders in a portal on `document.body`, positioned against the
 * trigger's viewport rect, and flips above the trigger when there isn't room
 * below. Nothing an ancestor does to overflow reaches it.
 *
 * Closing is the caller's: it owns which row is open, and a menu that closed
 * itself would fight that. This closes on an outside click, Escape, scroll and
 * resize by calling `onClose`.
 */
export function AnchoredMenu({
  anchorRef,
  onClose,
  align = "right",
  minWidth = 140,
  children,
}: {
  /** The element to position against, normally the button that opened it. */
  anchorRef: React.RefObject<HTMLElement | null>;
  onClose: () => void;
  /** Which edge of the menu lines up with the same edge of the anchor. */
  align?: "left" | "right";
  minWidth?: number;
  children: React.ReactNode;
}) {
  const menuRef = useRef<HTMLDivElement | null>(null);
  // Placed off-screen until measured, so the first paint isn't at 0,0.
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useLayoutEffect(() => {
    const anchor = anchorRef.current;
    const menu = menuRef.current;
    if (!anchor || !menu) return;

    const a = anchor.getBoundingClientRect();
    const m = menu.getBoundingClientRect();
    const margin = 8;

    // Below the trigger, unless the menu would run off the bottom and there is
    // more room above it. Clamped so it is always fully on screen.
    const below = a.bottom;
    const above = a.top - m.height;
    const fitsBelow = below + m.height + margin <= window.innerHeight;
    let top = fitsBelow || above < margin ? below : above;
    top = Math.max(margin, Math.min(top, window.innerHeight - m.height - margin));

    let left = align === "right" ? a.right - m.width : a.left;
    left = Math.max(margin, Math.min(left, window.innerWidth - m.width - margin));

    setPos({ top, left });
  }, [anchorRef, align]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    // Capture, so a scroll inside any container closes it rather than leaving
    // the menu behind where the trigger used to be.
    const onScroll = () => onClose();
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [onClose]);

  return createPortal(
    <div
      ref={menuRef}
      role="menu"
      style={{
        position: "fixed",
        top: pos?.top ?? -9999,
        left: pos?.left ?? -9999,
        visibility: pos ? "visible" : "hidden",
        zIndex: LAYER.popover,
        background: "var(--bg-elevated)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
        boxShadow: "var(--shadow-lg)",
        minWidth,
        overflow: "hidden",
      }}
      onClick={(e) => e.stopPropagation()}
    >
      {children}
    </div>,
    document.body,
  );
}
