import { useState, useRef, useEffect } from "react";
import { HexColorPicker } from "react-colorful";
import type { UIElementOption } from "../../../api/types";
import { VariableKeyPicker } from "../../shared/VariableKeyPicker";

interface SelectFeedbackEditorProps {
  value: Record<string, unknown> | null;
  options: UIElementOption[];
  onChange: (value: Record<string, unknown>) => void;
  onClear: () => void;
}

export function SelectFeedbackEditor({
  value,
  options,
  onChange,
  onClear,
}: SelectFeedbackEditorProps) {
  const current = value || { source: "state", key: "", style_map: {} };
  const styleMap =
    (current.style_map as Record<string, Record<string, string>>) ?? {};

  const handleKeyChange = (key: string) => {
    onChange({ ...current, key });
  };

  const handleStyleChange = (
    optionValue: string,
    field: string,
    color: string,
  ) => {
    const existing = styleMap[optionValue] || {};
    onChange({
      ...current,
      style_map: {
        ...styleMap,
        [optionValue]: { ...existing, [field]: color || undefined },
      },
    });
  };

  if (options.length === 0) {
    return (
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          padding: "var(--space-sm)",
        }}
      >
        Add options to this select element first (in Basic properties above).
      </div>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm)",
      }}
    >
      <div>
        <label style={labelStyle}>State Key</label>
        <VariableKeyPicker
          value={String(current.key || "")}
          onChange={handleKeyChange}
          placeholder="Select state key..."
        />
        <div style={helpStyle}>
          Style each option based on a live state value. The element will highlight
          the option that matches the current value of the selected key.
        </div>
      </div>

      <div>
        <label style={labelStyle}>Style per option value</label>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-sm)",
          }}
        >
          {options.map((opt) => {
            const optStyle = styleMap[opt.value] || {};
            return (
              <div
                key={opt.value}
                style={{
                  border: "1px solid var(--border-color)",
                  borderRadius: "var(--border-radius)",
                  padding: "6px 8px",
                }}
              >
                <div
                  style={{
                    fontSize: "var(--font-size-sm)",
                    fontWeight: 500,
                    marginBottom: 4,
                    display: "flex",
                    justifyContent: "space-between",
                  }}
                >
                  <span>{opt.label}</span>
                  <span
                    style={{
                      fontSize: 11,
                      color: "var(--text-muted)",
                      fontFamily: "var(--font-mono)",
                    }}
                  >
                    {opt.value}
                  </span>
                </div>
                <div
                  style={{
                    display: "flex",
                    gap: "var(--space-md)",
                    alignItems: "center",
                  }}
                >
                  <span
                    style={{
                      fontSize: 11,
                      color: "var(--text-muted)",
                      width: 24,
                    }}
                  >
                    BG
                  </span>
                  <InlineColorPicker
                    value={optStyle.bg_color || ""}
                    onChange={(c) =>
                      handleStyleChange(opt.value, "bg_color", c)
                    }
                  />
                  <span
                    style={{
                      fontSize: 11,
                      color: "var(--text-muted)",
                      width: 24,
                    }}
                  >
                    Text
                  </span>
                  <InlineColorPicker
                    value={optStyle.text_color || ""}
                    onChange={(c) =>
                      handleStyleChange(opt.value, "text_color", c)
                    }
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {value && (
        <button
          onClick={onClear}
          style={{
            padding: "4px 8px",
            borderRadius: "var(--border-radius)",
            fontSize: "var(--font-size-sm)",
            color: "var(--color-error)",
            background: "transparent",
            border: "1px solid var(--border-color)",
            alignSelf: "flex-start",
            cursor: "pointer",
          }}
        >
          Remove Binding
        </button>
      )}
    </div>
  );
}

function InlineColorPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (c: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  return (
    <div
      ref={ref}
      style={{
        position: "relative",
        display: "flex",
        alignItems: "center",
        gap: 4,
      }}
    >
      <div
        onClick={() => setOpen(!open)}
        style={{
          width: 20,
          height: 20,
          borderRadius: 3,
          backgroundColor: value || "transparent",
          border: "1px solid var(--border-color)",
          cursor: "pointer",
        }}
      />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="#000"
        style={{ width: 70, padding: "3px 4px", fontSize: 11 }}
      />
      {open && (
        <div
          style={{
            position: "absolute",
            zIndex: 100,
            top: 26,
            left: 0,
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            padding: "var(--space-xs)",
            boxShadow: "var(--shadow-lg)",
          }}
        >
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

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  color: "var(--text-muted)",
  marginBottom: 2,
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "4px 6px",
  fontSize: "var(--font-size-sm)",
};

const helpStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  lineHeight: 1.4,
  marginTop: 4,
  fontStyle: "italic",
};
