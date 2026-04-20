import { useState, useRef, useEffect } from "react";
import { HexColorPicker } from "react-colorful";

interface InlineColorPickerProps {
  value: string;
  onChange: (color: string) => void;
  placeholder?: string;
  clearable?: boolean;
  size?: "sm" | "md";
}

export function InlineColorPicker({
  value,
  onChange,
  placeholder,
  clearable = false,
  size = "sm",
}: InlineColorPickerProps) {
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
      const popoverHeight = size === "md" ? 170 : 150;
      const flipUp = spaceBelow < popoverHeight && rect.top > spaceBelow;
      setPopoverPos(flipUp
        ? { bottom: window.innerHeight - rect.top + 4, left: rect.left }
        : { top: rect.bottom + 4, left: rect.left });
    }
    setOpen(!open);
  };

  const displayColor = value || placeholder || "transparent";
  const isInherited = !value && !!placeholder;
  const swatchPx = size === "md" ? 24 : 22;
  const inputPx = size === "md" ? 80 : 72;
  const pickerW = size === "md" ? 180 : 160;
  const pickerH = size === "md" ? 150 : 130;

  return (
    <div ref={ref} style={{ position: "relative", display: "flex", alignItems: "center", gap: 4 }}>
      <div
        ref={swatchRef}
        onClick={handleOpen}
        style={{
          width: swatchPx, height: swatchPx, borderRadius: 4, flexShrink: 0,
          backgroundColor: displayColor,
          border: isInherited ? "1px dashed var(--border-color)" : "1px solid var(--border-color)",
          cursor: "pointer",
          opacity: isInherited ? 0.6 : 1,
        }}
      />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder || "#000"}
        style={{
          width: inputPx, padding: size === "md" ? "4px 6px" : "3px 4px",
          fontSize: size === "md" ? "var(--font-size-sm)" : 11,
          borderRadius: 3, border: "1px solid var(--border-color)",
        }}
      />
      {clearable && value && (
        <button
          onClick={() => onChange("")}
          style={{ padding: "2px 4px", fontSize: 10, color: "var(--text-muted)", borderRadius: 3 }}
        >
          Clear
        </button>
      )}
      {open && (
        <div style={{
          position: "fixed", zIndex: 9999,
          top: popoverPos.top, bottom: popoverPos.bottom, left: popoverPos.left,
          background: "var(--bg-elevated)", border: "1px solid var(--border-color)",
          borderRadius: "var(--border-radius)", padding: "var(--space-xs)",
          boxShadow: "var(--shadow-lg)",
        }}>
          <HexColorPicker
            color={value || placeholder || "#000000"}
            onChange={onChange}
            style={{ width: pickerW, height: pickerH }}
          />
        </div>
      )}
    </div>
  );
}
