import { HexColorPicker } from "react-colorful";
import { useAnchoredPanel } from "./AnchoredPanel";

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
  const displayColor = value || placeholder || "transparent";
  const isInherited = !value && !!placeholder;
  const swatchPx = size === "md" ? 24 : 22;
  const inputPx = size === "md" ? 80 : 72;
  const pickerW = size === "md" ? 180 : 160;
  const pickerH = size === "md" ? 150 : 130;
  // Unlike the list dropdowns this popover is exactly as big as the colour
  // wheel inside it, so it takes no width from the shared panel and is measured
  // instead. `pickerH` is the wheel's own declared height, which is all the
  // flip-up test needs: a 130px popover should not flip up with 200px below it.
  const panel = useAnchoredPanel<HTMLDivElement, HTMLDivElement>({
    width: "intrinsic",
    wantsHeight: pickerH,
  });
  const { open } = panel;

  return (
    <div ref={panel.containerRef} style={{ position: "relative", display: "flex", alignItems: "center", gap: 4 }}>
      <div
        ref={panel.triggerRef}
        onClick={panel.toggle}
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
        placeholder={placeholder || ""}
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
        <div ref={panel.panelRef} style={{
          ...panel.panelStyle,
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
