/**
 * The Theme Studio's picker: every theme as a card, built-in ones first, the
 * open one outlined (with a dot while it has unsaved edits), and Import below.
 * A card is a small mock panel drawn in the theme's own colours, font and
 * corners, so a theme is picked by how it looks; a colour the theme does not
 * set is drawn as the panel draws it (`CSS_VAR_FALLBACKS`), but one set empty
 * stays empty. Hovering a card lifts it and offers Duplicate; the column keeps
 * which card is hovered, so leaving one card never un-hovers another.
 */
import { useState, type CSSProperties } from "react";
import { Copy, Upload } from "lucide-react";
import type { ThemeSummary } from "../../api/restClient";
import { CSS_VAR_FALLBACKS } from "./colorUtils";
import { DEFAULT_FONT } from "./quickAdjust";

/**
 * Mini panel mockup rendered with the theme's actual variables. Shows the
 * theme's font, button colors, surface, accent, and page bg in one glance —
 * so users pick by feel, not by trying to imagine 4 abstract color dots
 * stitched into a real interface.
 */
function ThemeCardPreview({ theme }: { theme: ThemeSummary }) {
  const v = (theme.variables || {}) as Record<string, string | number | undefined>;
  const color = (key: string) => String(v[key] ?? CSS_VAR_FALLBACKS[key]);
  const bg = color("panel_bg");
  const text = color("panel_text");
  const accent = color("accent");
  const buttonBg = color("button_bg");
  const buttonText = color("button_text");
  const buttonBorder = color("button_border");
  const surface = color("surface");
  const surfaceBorder = color("surface_border");
  const fontFamily = String(v.font_family ?? DEFAULT_FONT);
  const radius = Number(v.border_radius ?? 8);
  // Mini elements use a smaller corner — heavy radius on tiny shapes looks bulbous.
  const miniRadius = Math.min(radius, 6);

  return (
    <div
      style={{
        background: bg,
        padding: "10px 12px",
        fontFamily,
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div style={{ color: text, fontSize: 11, fontWeight: 600, letterSpacing: 0.2 }}>
        Sample Panel
      </div>
      <div style={{ display: "flex", gap: 4 }}>
        <div
          style={{
            background: buttonBg,
            color: buttonText,
            border: `1px solid ${buttonBorder}`,
            borderRadius: miniRadius,
            padding: "3px 8px",
            fontSize: 10,
            flex: 1,
            textAlign: "center",
            fontFamily,
          }}
        >
          Button
        </div>
        <div
          style={{
            background: accent,
            color: buttonText,
            borderRadius: miniRadius,
            padding: "3px 8px",
            fontSize: 10,
            flex: 1,
            textAlign: "center",
            fontFamily,
            fontWeight: 600,
          }}
        >
          Active
        </div>
      </div>
      <div
        style={{
          background: surface,
          border: `1px solid ${surfaceBorder}`,
          borderRadius: 3,
          height: 5,
          position: "relative",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            background: accent,
            width: "62%",
            height: "100%",
            opacity: 0.85,
          }}
        />
      </div>
    </div>
  );
}

/** A card's frame: outlined when its theme is open, lifted while hovered unless it is. */
function cardStyle(selected: boolean, lifted: boolean): CSSProperties {
  return {
    position: "relative",
    borderRadius: 6,
    border: selected ? "2px solid var(--accent)" : "1px solid var(--border-color)",
    background: "var(--bg-surface)",
    cursor: "pointer",
    marginBottom: 8,
    overflow: "hidden",
    transition: "transform 0.12s, box-shadow 0.12s",
    transform: lifted ? "translateY(-1px)" : "none",
    boxShadow: lifted ? "0 4px 12px rgba(0,0,0,0.18)" : "none",
  };
}

const DESCRIPTION: CSSProperties = {
  fontSize: 10,
  color: "var(--text-muted)",
  marginTop: 2,
  lineHeight: 1.35,
  overflow: "hidden",
  textOverflow: "ellipsis",
  display: "-webkit-box",
  WebkitLineClamp: 2,
  WebkitBoxOrient: "vertical",
};

const UNSAVED_DOT: CSSProperties = {
  width: 6,
  height: 6,
  borderRadius: "50%",
  background: "#ffa726",
  display: "inline-block",
  flexShrink: 0,
};

/** The theme's name (with the unsaved dot) and its description, under the mockup. */
function CardCaption({ theme, selected, dirty }: { theme: ThemeSummary; selected: boolean; dirty: boolean }) {
  return (
    <div
      style={{
        padding: "6px 10px 8px",
        background: selected ? "var(--accent-dim, rgba(138,180,147,0.12))" : "var(--bg-surface)",
        borderTop: "1px solid var(--border-color)",
      }}
    >
      <div
        style={{
          fontSize: 12,
          fontWeight: selected ? 700 : 600,
          color: "var(--text-primary)",
          display: "flex",
          alignItems: "center",
          gap: 5,
        }}
      >
        {theme.name}
        {dirty && <span title="Unsaved changes" style={UNSAVED_DOT} />}
      </div>
      {theme.description && <div style={DESCRIPTION}>{theme.description}</div>}
    </div>
  );
}

function DuplicateButton({ theme, busy, onDuplicate }: {
  theme: ThemeSummary;
  busy: boolean;
  onDuplicate: (id: string, name: string) => void;
}) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onDuplicate(theme.id, theme.name);
      }}
      disabled={busy}
      title={`Duplicate "${theme.name}" as a custom theme`}
      style={{
        position: "absolute",
        top: 6,
        right: 6,
        width: 24,
        height: 24,
        padding: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(0,0,0,0.6)",
        border: "1px solid rgba(255,255,255,0.3)",
        borderRadius: 4,
        cursor: busy ? "wait" : "pointer",
        color: "#fff",
        backdropFilter: "blur(4px)",
      }}
    >
      <Copy size={13} />
    </button>
  );
}

interface ThemeCardProps {
  theme: ThemeSummary;
  selected: boolean;
  hovered: boolean;
  /** The open theme has unsaved edits. */
  dirty: boolean;
  busy: boolean;
  onPick: (id: string) => void;
  onHover: (id: string) => void;
  onLeave: (id: string) => void;
  onDuplicate: (id: string, name: string) => void;
}

function ThemeCard({ theme, selected, hovered, dirty, busy, onPick, onHover, onLeave, onDuplicate }: ThemeCardProps) {
  return (
    <div
      onClick={() => onPick(theme.id)}
      onMouseEnter={() => onHover(theme.id)}
      onMouseLeave={() => onLeave(theme.id)}
      style={cardStyle(selected, hovered && !selected)}
    >
      <ThemeCardPreview theme={theme} />
      <CardCaption theme={theme} selected={selected} dirty={selected && dirty} />
      {hovered && <DuplicateButton theme={theme} busy={busy} onDuplicate={onDuplicate} />}
    </div>
  );
}

const GROUP_HEADING: CSSProperties = {
  fontSize: 10,
  color: "var(--text-muted)",
  fontWeight: 600,
  textTransform: "uppercase",
};

interface ThemePickerColumnProps {
  themes: ThemeSummary[];
  currentThemeId: string;
  busy: boolean;
  isDirty: boolean;
  onPick: (id: string) => void;
  onDuplicate: (id: string, name: string) => void;
  onImport: () => void;
}

export function ThemePickerColumn({
  themes,
  currentThemeId,
  busy,
  isDirty,
  onPick,
  onDuplicate,
  onImport,
}: ThemePickerColumnProps) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const builtins = themes.filter((t) => t.source === "builtin");
  const customs = themes.filter((t) => t.source !== "builtin");
  const leave = (id: string) => setHoveredId((current) => (current === id ? null : current));

  const card = (t: ThemeSummary) => (
    <ThemeCard
      key={t.id}
      theme={t}
      selected={t.id === currentThemeId}
      hovered={hoveredId === t.id}
      dirty={isDirty}
      busy={busy}
      onPick={onPick}
      onHover={setHoveredId}
      onLeave={leave}
      onDuplicate={onDuplicate}
    />
  );

  return (
    <div
      style={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        borderRight: "1px solid var(--border-color)",
        background: "var(--bg-surface)",
      }}
    >
      <div
        style={{
          padding: "10px 12px",
          fontSize: 10,
          color: "var(--text-muted)",
          textTransform: "uppercase",
          letterSpacing: "0.5px",
          borderBottom: "1px solid var(--border-color)",
        }}
      >
        Themes
      </div>
      <div style={{ flex: 1, overflow: "auto", padding: 8 }}>
        {builtins.length > 0 && (
          <>
            <div style={{ ...GROUP_HEADING, padding: "4px 4px" }}>Built-in</div>
            {builtins.map(card)}
          </>
        )}
        {customs.length > 0 && (
          <>
            <div style={{ ...GROUP_HEADING, padding: "8px 4px 4px" }}>Custom</div>
            {customs.map(card)}
          </>
        )}
        {customs.length === 0 && builtins.length > 0 && (
          <div
            style={{
              padding: "8px",
              fontSize: 11,
              color: "var(--text-muted)",
              textAlign: "center",
              fontStyle: "italic",
            }}
          >
            No custom themes yet. Hover any theme above and click <Copy size={10} /> to duplicate it.
          </div>
        )}
      </div>
      <div
        style={{
          padding: 8,
          borderTop: "1px solid var(--border-color)",
        }}
      >
        <button
          onClick={onImport}
          style={{
            width: "100%",
            padding: "6px 10px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 6,
            background: "var(--bg-hover)",
            border: "1px solid var(--border-color)",
            borderRadius: 4,
            cursor: "pointer",
            fontSize: 12,
            color: "var(--text-secondary)",
          }}
        >
          <Upload size={12} /> Import .avctheme
        </button>
      </div>
    </div>
  );
}
