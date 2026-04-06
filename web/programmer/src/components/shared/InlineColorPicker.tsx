/**
 * Shared inline color picker — swatch + hex input + popover HexColorPicker.
 *
 * Used by FeedbackBindingEditor, SurfaceConfigurator, and any other
 * component that needs a compact color picker inline.
 */
import { useState, useRef, useEffect } from "react";
import { HexColorPicker } from "react-colorful";

interface InlineColorPickerProps {
  value: string;
  onChange: (color: string) => void;
}

export function InlineColorPicker({ value, onChange }: InlineColorPickerProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const swatchRef = useRef<HTMLDivElement>(null);
  const [popoverPos, setPopoverPos] = useState<{ top?: number; bottom?: number; left: number }>({ left: 0 });

  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const handleScroll = (e: Event) => {
      if (ref.current && ref.current.contains(e.target as Node)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("scroll", handleScroll, true);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("scroll", handleScroll, true);
    };
  }, [open]);

  const handleOpen = () => {
    if (!open && swatchRef.current) {
      const rect = swatchRef.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom;
      const spaceAbove = rect.top;
      const popoverHeight = 150;
      const flipUp = spaceBelow < popoverHeight && spaceAbove > spaceBelow;
      setPopoverPos(flipUp
        ? { bottom: window.innerHeight - rect.top + 4, left: rect.left }
        : { top: rect.bottom + 4, left: rect.left });
    }
    setOpen(!open);
  };

  return (
    <div ref={ref} style={{ position: "relative", display: "flex", alignItems: "center", gap: 4 }}>
      <div
        ref={swatchRef}
        onClick={handleOpen}
        style={{
          width: 22, height: 22, borderRadius: 4, flexShrink: 0,
          backgroundColor: value || "transparent",
          border: "1px solid var(--border-color)", cursor: "pointer",
        }}
      />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="#000"
        style={{ width: 72, padding: "3px 4px", fontSize: 11, borderRadius: 3, border: "1px solid var(--border-color)" }}
      />
      {open && (
        <div style={{
          position: "fixed", zIndex: 9999,
          top: popoverPos.top, bottom: popoverPos.bottom, left: popoverPos.left,
          background: "var(--bg-elevated)", border: "1px solid var(--border-color)",
          borderRadius: "var(--border-radius)", padding: "var(--space-xs)",
          boxShadow: "var(--shadow-lg)",
        }}>
          <HexColorPicker
            color={value || "#000000"}
            onChange={onChange}
            style={{ width: 160, height: 130 }}
          />
        </div>
      )}
    </div>
  );
}
