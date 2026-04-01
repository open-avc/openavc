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

  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  return (
    <div ref={ref} style={{ position: "relative", display: "flex", alignItems: "center", gap: 4 }}>
      <div
        onClick={() => setOpen(!open)}
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
          position: "absolute", zIndex: 100, top: 28, left: 0,
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
