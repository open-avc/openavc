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
  LayoutTemplate,
} from "lucide-react";
import { ELEMENT_TYPES, ELEMENT_TEMPLATES, type ElementTypeInfo } from "./uiBuilderHelpers";
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

export function ElementPalette({ disabled }: { disabled?: boolean }) {
  const panelElements = usePluginStore((s) => s.extensions.panel_elements);

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

      {CATEGORIES.map((cat) => {
        const items = ELEMENT_TYPES.filter((t) => t.category === cat.key);
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
                />
              ))}
            </div>
          </div>
        );
      })}

      {/* Templates (multi-element presets) */}
      {ELEMENT_TEMPLATES.length > 0 && (
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
            Templates
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {ELEMENT_TEMPLATES.map((tpl) => (
              <TemplatePaletteItem
                key={tpl.id}
                template={tpl}
                disabled={disabled}
              />
            ))}
          </div>
        </div>
      )}

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
}: {
  info: ElementTypeInfo;
  disabled?: boolean;
  icon?: React.ReactNode;
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
      title={info.description ? `${info.label} — ${info.description}` : `Drag to add ${info.label}`}
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
      <span style={{ color: "var(--text-muted)", display: "flex" }}>
        {icon || ICONS[info.type] || <Square size={16} />}
      </span>
      {info.label}
    </div>
  );
}

function TemplatePaletteItem({
  template,
  disabled,
}: {
  template: { id: string; label: string; description: string };
  disabled?: boolean;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `template-${template.id}`,
    data: { source: "template", templateId: template.id },
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
      title={`${template.label} — ${template.description}`}
      onMouseEnter={(e) => {
        if (!disabled) {
          (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)";
        }
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.background = "transparent";
      }}
    >
      <span style={{ color: "var(--accent)", display: "flex" }}>
        <LayoutTemplate size={16} />
      </span>
      {template.label}
    </div>
  );
}
