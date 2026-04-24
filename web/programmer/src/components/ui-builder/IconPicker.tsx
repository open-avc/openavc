import { useState, useMemo } from "react";
import { X } from "lucide-react";
import * as LucideIcons from "lucide-react";

// AV-relevant icon categories
const ICON_CATEGORIES: Record<string, string[]> = {
  "Power & System": [
    "power", "power-off", "plug", "zap", "shield", "lock", "unlock", "settings",
    "settings-2", "cog", "wrench", "toggle-left", "toggle-right",
  ],
  "Audio": [
    "volume", "volume-1", "volume-2", "volume-x", "mic", "mic-off", "headphones",
    "speaker", "music", "music-2", "music-3", "audio-lines",
  ],
  "Video": [
    "monitor", "tv", "tv-2", "projector", "camera", "video", "video-off", "film",
    "screen-share", "screen-share-off", "airplay", "cast", "presentation",
  ],
  "Playback": [
    "play", "pause", "square", "skip-forward", "skip-back", "rewind",
    "fast-forward", "repeat", "repeat-1", "shuffle", "circle-play",
    "circle-pause", "circle-stop",
  ],
  "Navigation": [
    "arrow-up", "arrow-down", "arrow-left", "arrow-right",
    "chevron-up", "chevron-down", "chevron-left", "chevron-right",
    "chevrons-up", "chevrons-down", "chevrons-left", "chevrons-right",
    "home", "menu", "grid-3x3", "layout-grid", "maximize", "minimize",
    "move", "corner-up-left", "corner-up-right",
  ],
  "Lighting": [
    "sun", "moon", "lamp", "lamp-desk", "lamp-floor", "lightbulb",
    "sunrise", "sunset", "eye", "eye-off", "sun-dim",
  ],
  "Communication": [
    "phone", "phone-off", "phone-call", "wifi", "wifi-off", "bluetooth",
    "radio", "signal", "satellite", "globe",
  ],
  "Climate": [
    "thermometer", "thermometer-sun", "thermometer-snowflake",
    "fan", "wind", "cloud", "droplets", "snowflake",
  ],
  "Security": [
    "shield", "shield-check", "key", "scan", "fingerprint",
    "alarm-clock", "siren", "lock", "unlock", "camera",
  ],
  "General": [
    "check", "x", "alert-triangle", "info", "help-circle", "clock",
    "calendar", "bell", "bell-off", "bookmark", "star", "heart",
    "thumbs-up", "thumbs-down", "plus", "minus", "hash",
    "circle", "square", "triangle", "diamond",
  ],
};

// Build a flat list of all icon names from lucide-react.
// lucide-react exports an `icons` map (Record<PascalCase, Component>).
// Individual icon exports are forwardRef objects (typeof "object", not "function"),
// so we use the `icons` map which is the canonical enumeration.
function getAllIconNames(): string[] {
  const iconsMap = (LucideIcons as Record<string, unknown>).icons as Record<string, unknown> | undefined;
  if (iconsMap && typeof iconsMap === "object") {
    return Object.keys(iconsMap)
      .map((key) =>
        key
          .replace(/([a-z0-9])([A-Z])/g, "$1-$2")
          .replace(/([A-Z])([A-Z][a-z])/g, "$1-$2")
          .toLowerCase(),
      )
      .sort();
  }
  // Fallback: iterate all exports, accept functions and objects (forwardRef)
  const names: string[] = [];
  for (const key of Object.keys(LucideIcons)) {
    if (key === "default" || key === "createLucideIcon" || key === "icons") continue;
    const val = (LucideIcons as Record<string, unknown>)[key];
    if (!val || (typeof val !== "function" && typeof val !== "object")) continue;
    const kebab = key
      .replace(/([a-z0-9])([A-Z])/g, "$1-$2")
      .replace(/([A-Z])([A-Z][a-z])/g, "$1-$2")
      .toLowerCase();
    names.push(kebab);
  }
  return names.sort();
}

const ALL_ICONS = getAllIconNames();

function getIconComponent(kebabName: string): React.ComponentType<{ size?: number; color?: string }> | null {
  // Convert kebab-case to PascalCase
  const pascal = kebabName
    .split("-")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join("");
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
    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
      <div
        onClick={() => setOpen(true)}
        style={{
          width: 28,
          height: 28,
          borderRadius: 4,
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
          padding: "3px 8px",
          borderRadius: 3,
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
            padding: "2px 4px",
            fontSize: 10,
            color: "var(--text-muted)",
            borderRadius: 3,
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
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Choose Icon"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 10000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(0,0,0,0.6)",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        style={{
          background: "var(--bg-surface)",
          border: "1px solid var(--border-color)",
          borderRadius: "var(--border-radius)",
          width: 600,
          maxHeight: "80vh",
          display: "flex",
          flexDirection: "column",
          boxShadow: "var(--shadow-lg)",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "12px 16px",
            borderBottom: "1px solid var(--border-color)",
          }}
        >
          <span style={{ fontWeight: 600, fontSize: 14 }}>Choose Icon</span>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              color: "var(--text-muted)",
              cursor: "pointer",
              padding: 4,
            }}
          >
            <X size={16} />
          </button>
        </div>

        {/* Search */}
        <div style={{ padding: "8px 16px" }}>
          <input
            type="text"
            placeholder="Search icons..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
            style={{
              width: "100%",
              padding: "6px 10px",
              borderRadius: 6,
              border: "1px solid var(--border-color)",
              background: "var(--bg-base)",
              color: "var(--text-primary)",
              fontSize: 13,
            }}
          />
        </div>

        {/* Category tabs */}
        <div
          style={{
            display: "flex",
            gap: 2,
            padding: "0 16px 8px",
            overflowX: "auto",
            flexShrink: 0,
          }}
        >
          {categories.map((cat) => (
            <button
              key={cat}
              onClick={() => setActiveCategory(cat)}
              style={{
                padding: "3px 8px",
                borderRadius: 3,
                fontSize: 11,
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
            padding: "0 16px 16px",
          }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(48px, 1fr))",
              gap: 4,
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
                    borderRadius: 6,
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
                padding: 24,
                color: "var(--text-muted)",
                fontSize: 13,
              }}
            >
              No icons match "{search}"
            </div>
          )}
          {filteredIcons.length > 300 && (
            <div
              style={{
                textAlign: "center",
                padding: 8,
                color: "var(--text-muted)",
                fontSize: 11,
              }}
            >
              Showing 300 of {filteredIcons.length} — refine your search
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
