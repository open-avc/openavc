import { useState, useMemo } from "react";
import { X } from "lucide-react";
import * as LucideIcons from "lucide-react";
import { Modal } from "../shared/Modal";
import { ICON_CATEGORIES, ALL_ICONS, kebabToPascal } from "./iconPickerHelpers";

function getIconComponent(kebabName: string): React.ComponentType<{ size?: number; color?: string }> | null {
  const pascal = kebabToPascal(kebabName);
  // Try the icons map first, then named exports
  const iconsMap = (LucideIcons as Record<string, unknown>).icons as Record<string, unknown> | undefined;
  const comp = iconsMap?.[pascal] ?? (LucideIcons as Record<string, unknown>)[pascal];
  if (comp) return comp as React.ComponentType<{ size?: number; color?: string }>;
  return null;
}

interface IconPickerProps {
  value: string;
  onChange: (iconName: string) => void;
}

export function IconPicker({ value, onChange }: IconPickerProps) {
  const [open, setOpen] = useState(false);

  const IconComp = value ? getIconComponent(value) : null;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
      <div
        onClick={() => setOpen(true)}
        style={{
          width: 28,
          height: 28,
          borderRadius: "var(--border-radius)",
          border: "1px solid var(--border-color)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          cursor: "pointer",
          background: "var(--bg-base)",
        }}
      >
        {IconComp ? <IconComp size={18} color="var(--text-primary)" /> : null}
      </div>
      <button
        onClick={() => setOpen(true)}
        style={{
          padding: "var(--space-xs) var(--space-sm)",
          borderRadius: "var(--border-radius)",
          fontSize: "var(--font-size-sm)",
          color: "var(--accent)",
          background: "var(--bg-base)",
          border: "1px solid var(--border-color)",
        }}
      >
        {value ? "Change" : "Choose Icon"}
      </button>
      {value && (
        <button
          onClick={() => onChange("")}
          style={{
            padding: "var(--space-2xs) var(--space-xs)",
            fontSize: "var(--font-size-2xs)",
            color: "var(--text-muted)",
            borderRadius: "var(--border-radius)",
          }}
        >
          Clear
        </button>
      )}
      {open && (
        <IconBrowserModal
          currentValue={value}
          onSelect={(name) => {
            onChange(name);
            setOpen(false);
          }}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  );
}

function IconBrowserModal({
  currentValue,
  onSelect,
  onClose,
}: {
  currentValue: string;
  onSelect: (name: string) => void;
  onClose: () => void;
}) {
  const [search, setSearch] = useState("");
  const [activeCategory, setActiveCategory] = useState("All");

  const categories = ["All", ...Object.keys(ICON_CATEGORIES)];

  const filteredIcons = useMemo(() => {
    let icons: string[];
    if (activeCategory === "All") {
      icons = ALL_ICONS;
    } else {
      icons = ICON_CATEGORIES[activeCategory] || [];
    }
    if (search) {
      const q = search.toLowerCase();
      icons = icons.filter((name) => name.includes(q));
    }
    return icons;
  }, [activeCategory, search]);

  return (
    <Modal
      onClose={onClose}
      label="Choose Icon"
      panelStyle={{
        background: "var(--bg-surface)",
        border: "1px solid var(--border-color)",
        width: 600,
        maxHeight: "80vh",
        display: "flex",
        flexDirection: "column",
      }}
    >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "var(--space-md) var(--space-lg)",
            borderBottom: "1px solid var(--border-color)",
          }}
        >
          <span style={{ fontWeight: "var(--font-weight-semibold)", fontSize: "var(--font-size-lg)" }}>Choose Icon</span>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              color: "var(--text-muted)",
              cursor: "pointer",
              padding: "var(--space-xs)",
            }}
          >
            <X size={16} />
          </button>
        </div>

        {/* Search */}
        <div style={{ padding: "var(--space-sm) var(--space-lg)" }}>
          <input
            type="text"
            placeholder="Search icons..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
            style={{
              width: "100%",
              padding: "var(--space-sm) var(--space-md)",
              borderRadius: "var(--radius-lg)",
              border: "1px solid var(--border-color)",
              background: "var(--bg-base)",
              color: "var(--text-primary)",
              fontSize: "var(--font-size-base)",
            }}
          />
        </div>

        {/* Category tabs */}
        <div
          style={{
            display: "flex",
            gap: "var(--space-2xs)",
            padding: "0 var(--space-lg) var(--space-sm)",
            overflowX: "auto",
            flexShrink: 0,
          }}
        >
          {categories.map((cat) => (
            <button
              key={cat}
              onClick={() => setActiveCategory(cat)}
              style={{
                padding: "var(--space-xs) var(--space-sm)",
                borderRadius: "var(--border-radius)",
                fontSize: "var(--font-size-xs)",
                whiteSpace: "nowrap",
                color:
                  activeCategory === cat
                    ? "var(--accent)"
                    : "var(--text-muted)",
                background:
                  activeCategory === cat
                    ? "var(--accent-dim)"
                    : "transparent",
                border: "none",
                cursor: "pointer",
              }}
            >
              {cat}
            </button>
          ))}
        </div>

        {/* Icon grid */}
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "0 var(--space-lg) var(--space-lg)",
          }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(48px, 1fr))",
              gap: "var(--space-xs)",
            }}
          >
            {filteredIcons.slice(0, 300).map((name) => {
              const Comp = getIconComponent(name);
              if (!Comp) return null;
              const isSelected = currentValue === name;
              return (
                <button
                  key={name}
                  onClick={() => onSelect(name)}
                  title={name}
                  style={{
                    width: 48,
                    height: 48,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    borderRadius: "var(--radius-lg)",
                    border: isSelected
                      ? "2px solid var(--accent)"
                      : "1px solid transparent",
                    background: isSelected
                      ? "var(--accent-dim)"
                      : "var(--bg-base)",
                    cursor: "pointer",
                    color: "var(--text-primary)",
                  }}
                >
                  <Comp size={20} />
                </button>
              );
            })}
          </div>
          {filteredIcons.length === 0 && (
            <div
              style={{
                textAlign: "center",
                lineHeight: "var(--line-relaxed)",
                padding: "var(--space-xl)",
                color: "var(--text-muted)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              No icons match "{search}"
            </div>
          )}
          {filteredIcons.length > 300 && (
            <div
              style={{
                textAlign: "center",
                lineHeight: "var(--line-relaxed)",
                padding: "var(--space-sm)",
                color: "var(--text-muted)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              Showing 300 of {filteredIcons.length}. Refine your search
            </div>
          )}
        </div>
    </Modal>
  );
}
