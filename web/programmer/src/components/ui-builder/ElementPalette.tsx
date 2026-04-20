import { useState } from "react";
import { useDraggable } from "@dnd-kit/core";
import {
  MousePointerClick,
  SlidersHorizontal,
  ChevronDown,
  TextCursorInput,
  Type,
  Circle,
  Image,
  Square,
  ArrowRight,
  Camera,
  Gauge,
  BarChart3,
  SlidersVertical,
  Group,
  Clock,
  Grid3X3,
  LayoutGrid,
  List,
  Puzzle,
} from "lucide-react";
import { ELEMENT_TYPES, type ElementTypeInfo } from "./uiBuilderHelpers";
import { usePluginStore } from "../../store/pluginStore";

const ICONS: Record<string, React.ReactNode> = {
  button: <MousePointerClick size={16} />,
  slider: <SlidersHorizontal size={16} />,
  fader: <SlidersVertical size={16} />,
  select: <ChevronDown size={16} />,
  text_input: <TextCursorInput size={16} />,
  keypad: <Grid3X3 size={16} />,
  label: <Type size={16} />,
  status_led: <Circle size={16} />,
  image: <Image size={16} />,
  clock: <Clock size={16} />,
  group: <Group size={16} />,
  spacer: <Square size={16} />,
  gauge: <Gauge size={16} />,
  level_meter: <BarChart3 size={16} />,
  matrix: <LayoutGrid size={16} />,
  list: <List size={16} />,
  page_nav: <ArrowRight size={16} />,
  camera_preset: <Camera size={16} />,
};

const CATEGORIES = [
  { key: "controls" as const, label: "Controls" },
  { key: "display" as const, label: "Display" },
  { key: "data" as const, label: "Data" },
  { key: "navigation" as const, label: "Navigation" },
];

export function ElementPalette({ disabled, onAdd }: { disabled?: boolean; onAdd?: (type: string) => void }) {
  const panelElements = usePluginStore((s) => s.extensions.panel_elements);
  const [search, setSearch] = useState("");

  const filteredTypes = search
    ? ELEMENT_TYPES.filter(
        (t) =>
          t.label.toLowerCase().includes(search.toLowerCase()) ||
          t.type.toLowerCase().includes(search.toLowerCase()) ||
          t.description.toLowerCase().includes(search.toLowerCase())
      )
    : ELEMENT_TYPES;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-md)",
        padding: "var(--space-md)",
        overflow: "auto",
      }}
    >
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
          textTransform: "uppercase",
          letterSpacing: "0.5px",
          fontWeight: 600,
        }}
      >
        Elements
      </div>

      {/* Search (11.7) */}
      <input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search elements..."
        style={{
          padding: "var(--space-xs) var(--space-sm)",
          fontSize: "var(--font-size-sm)",
          borderRadius: "var(--border-radius)",
          border: "1px solid var(--border-color)",
          background: "var(--bg-surface)",
          color: "var(--text-primary)",
        }}
      />

      {CATEGORIES.map((cat) => {
        const items = filteredTypes.filter((t) => t.category === cat.key);
        if (items.length === 0) return null;
        return (
          <div key={cat.key}>
            <div
              style={{
                fontSize: 11,
                color: "var(--text-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
                marginBottom: "var(--space-xs)",
              }}
            >
              {cat.label}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              {items.map((info) => (
                <PaletteItem
                  key={info.type}
                  info={info}
                  disabled={disabled}
                  onAdd={onAdd}
                />
              ))}
            </div>
          </div>
        );
      })}

      {/* Plugin elements */}
      {panelElements.length > 0 && (
        <div>
          <div
            style={{
              fontSize: 11,
              color: "var(--text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.5px",
              marginBottom: "var(--space-xs)",
            }}
          >
            Plugins
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {panelElements.map((ext) => (
              <PaletteItem
                key={`plugin-${ext.plugin_id}-${ext.type}`}
                info={{
                  type: `plugin:${ext.plugin_id}:${ext.type}`,
                  label: ext.label,
                  category: "controls",
                  description: `Plugin element from ${ext.plugin_id}`,
                }}
                disabled={disabled}
                icon={<Puzzle size={16} />}
                onAdd={onAdd}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function PaletteItem({
  info,
  disabled,
  icon,
  onAdd,
}: {
  info: ElementTypeInfo;
  disabled?: boolean;
  icon?: React.ReactNode;
  onAdd?: (type: string) => void;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `palette-${info.type}`,
    data: { source: "palette", elementType: info.type },
    disabled,
  });

  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-sm)",
        padding: "6px 8px",
        borderRadius: "var(--border-radius)",
        cursor: disabled ? "default" : "grab",
        opacity: isDragging ? 0.4 : disabled ? 0.4 : 1,
        fontSize: "var(--font-size-sm)",
        color: "var(--text-primary)",
        transition: "background var(--transition-fast)",
        background: "transparent",
        userSelect: "none",
      }}
      title={info.description ? `${info.label} — ${info.description}\nClick to add, or drag to place` : `Click to add ${info.label}, or drag to place`}
      onClick={() => { if (!disabled && onAdd) onAdd(info.type); }}
      onMouseEnter={(e) => {
        if (!disabled) {
          (e.currentTarget as HTMLElement).style.background =
            "var(--bg-hover)";
        }
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.background = "transparent";
      }}
    >
      <span style={{ color: "var(--text-muted)", display: "flex", flexShrink: 0 }}>
        {icon || ICONS[info.type] || <Square size={16} />}
      </span>
      <div style={{ minWidth: 0 }}>
        <div>{info.label}</div>
        {info.description && (
          <div style={{ fontSize: 10, color: "var(--text-muted)", lineHeight: 1.2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {info.description}
          </div>
        )}
      </div>
    </div>
  );
}

