/**
 * The schematic surfaces: a layout the plugin declares, with no unit behind it.
 *
 * Grid (Stream Deck, X-Keys), strip (MIDI fader bank) and custom (arbitrary
 * x/y controls) draw the geometry from the layout and hand a click back as a
 * control id; PageTabs is the page row above them. A grid that requires a
 * device does not come through here — DeckWorkbench draws the real unit.
 */
import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { ElementIcon } from "../../ui-builder/ElementIcon";
import type { ButtonAssignment, SurfaceLayout } from "./types";

export function GridSurface({
  layout,
  currentPage,
  selectedControl,
  onSelectControl,
  getAssignment,
  flashIndex = null,
}: {
  layout: SurfaceLayout;
  currentPage: number;
  selectedControl: string | null;
  onSelectControl: (id: string) => void;
  getAssignment: (index: number, page?: number) => ButtonAssignment | undefined;
  flashIndex?: number | null;
}) {
  const rows = layout.rows ?? 3;
  const cols = layout.columns ?? 5;
  const keySize = layout.key_size_px ?? 72;
  const spacing = layout.key_spacing_px ?? 4;

  return (
    <div
      style={{
        display: "inline-grid",
        gridTemplateColumns: `repeat(${cols}, ${keySize}px)`,
        gridTemplateRows: `repeat(${rows}, ${keySize}px)`,
        gap: spacing,
        padding: "var(--space-md)",
        background: "var(--bg-base)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
      }}
    >
      {Array.from({ length: rows * cols }, (_, i) => {
        const assignment = getAssignment(i, currentPage);
        const isSelected = selectedControl === String(i);
        const hasAssignment = !!assignment?.label || !!assignment?.icon || !!assignment?.bindings?.press?.length;
        const bgColor = assignment?.bg_color;

        return (
          <button
            key={i}
            onClick={() => onSelectControl(String(i))}
            style={{
              width: keySize,
              height: keySize,
              borderRadius: 6,
              background: isSelected
                ? "var(--accent-dim)"
                : bgColor || (hasAssignment ? "var(--bg-elevated)" : "var(--bg-surface)"),
              border: isSelected
                ? "2px solid var(--accent)"
                : "1px solid var(--border-color)",
              boxShadow: flashIndex === i ? "0 0 0 3px #f59e0b" : undefined,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 2,
              cursor: "pointer",
              transition: "all var(--transition-fast)",
              overflow: "hidden",
              padding: 4,
              color: assignment?.text_color || "var(--text-secondary)",
            }}
            title={
              hasAssignment
                ? `Button ${i + 1}: ${assignment?.label || (Array.isArray(assignment?.bindings?.press) && assignment?.bindings?.press[0]?.action) || "configured"}`
                : `Button ${i + 1} (unassigned)`
            }
          >
            {!hasAssignment && (
              <div
                style={{
                  fontSize: 10,
                  color: "var(--text-muted)",
                  opacity: 0.3,
                }}
              >
                {i + 1}
              </div>
            )}
            {assignment?.icon && (
              <ElementIcon
                name={assignment.icon}
                size={assignment.label ? Math.floor(keySize * 0.35) : Math.floor(keySize * 0.5)}
                color={assignment?.text_color || "var(--text-secondary)"}
              />
            )}
            {assignment?.label && (
              <div
                style={{
                  fontSize: 9,
                  color: assignment?.text_color || "var(--text-secondary)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  maxWidth: keySize - 8,
                  textAlign: "center",
                }}
              >
                {assignment.label}
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}

// ──── Strip Surface (MIDI fader bank) ────

export function StripSurface({
  layout,
  selectedControl,
  onSelectControl,
  getAssignment,
}: {
  layout: SurfaceLayout;
  selectedControl: string | null;
  onSelectControl: (id: string) => void;
  getAssignment: (index: number, page?: number) => ButtonAssignment | undefined;
}) {
  const controls = layout.controls ?? [];
  const count = controls.length || (layout.columns ?? 8);

  return (
    <div
      style={{
        display: "flex",
        gap: "var(--space-sm)",
        padding: "var(--space-md)",
        background: "var(--bg-base)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
      }}
    >
      {Array.from({ length: count }, (_, i) => {
        const ctrl = controls[i];
        const controlType = ctrl?.type ?? "fader";
        const isSelected = selectedControl === String(i);
        const assignment = getAssignment(i, 0);

        if (controlType === "fader") {
          return (
            <FaderControl
              key={i}
              label={ctrl?.label ?? `Ch ${i + 1}`}
              selected={isSelected}
              onClick={() => onSelectControl(String(i))}
              assignment={assignment}
            />
          );
        }

        return (
          <button
            key={i}
            onClick={() => onSelectControl(String(i))}
            style={{
              width: 50,
              height: 50,
              borderRadius: 6,
              background: isSelected ? "var(--accent-dim)" : "var(--bg-surface)",
              border: isSelected ? "2px solid var(--accent)" : "1px solid var(--border-color)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              cursor: "pointer",
              fontSize: 10,
              color: assignment?.label ? "var(--text-primary)" : "var(--text-muted)",
            }}
          >
            {assignment?.label ?? i + 1}
          </button>
        );
      })}
    </div>
  );
}

// ──── Fader Control ────

function FaderControl({
  label,
  selected,
  onClick,
  assignment,
}: {
  label: string;
  selected: boolean;
  onClick: () => void;
  assignment: ButtonAssignment | undefined;
}) {
  return (
    <div
      onClick={onClick}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: "var(--space-xs)",
        padding: "var(--space-sm)",
        borderRadius: "var(--border-radius)",
        background: selected ? "var(--accent-dim)" : "transparent",
        border: selected ? "2px solid var(--accent)" : "1px solid transparent",
        cursor: "pointer",
        width: 50,
      }}
    >
      <div
        style={{
          width: 8,
          height: 120,
          background: "var(--bg-surface)",
          borderRadius: 4,
          border: "1px solid var(--border-color)",
          position: "relative",
        }}
      >
        <div
          style={{
            position: "absolute",
            bottom: "30%",
            left: -4,
            width: 16,
            height: 12,
            background: assignment?.bindings?.feedback ? "var(--accent-bg)" : "var(--text-muted)",
            borderRadius: 2,
          }}
        />
      </div>
      <div style={{ fontSize: 9, color: "var(--text-muted)", textAlign: "center" }}>
        {assignment?.label ?? label}
      </div>
    </div>
  );
}

// ──── Custom Surface (arbitrary positioned controls) ────

export function CustomSurface({
  layout,
  selectedControl,
  onSelectControl,
  getAssignment,
}: {
  layout: SurfaceLayout;
  selectedControl: string | null;
  onSelectControl: (id: string) => void;
  getAssignment: (index: number, page?: number) => ButtonAssignment | undefined;
}) {
  const controls = layout.controls ?? [];
  const width = layout.width_px ?? 600;
  const height = layout.height_px ?? 300;

  return (
    <div
      style={{
        position: "relative",
        width,
        height,
        background: "var(--bg-base)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
      }}
    >
      {controls.map((ctrl, i) => {
        const isSelected = selectedControl === String(i);
        const assignment = getAssignment(i, 0);
        const ctrlWidth = ctrl.width ?? 50;
        const ctrlHeight = ctrl.height ?? 50;

        if (ctrl.type === "fader") {
          return (
            <div
              key={ctrl.id ?? i}
              onClick={() => onSelectControl(String(i))}
              style={{
                position: "absolute",
                left: ctrl.x ?? 0,
                top: ctrl.y ?? 0,
                width: ctrlWidth,
                height: ctrlHeight,
                cursor: "pointer",
              }}
            >
              <FaderControl
                label={ctrl.label ?? `Fader ${i + 1}`}
                selected={isSelected}
                onClick={() => {}}
                assignment={assignment}
              />
            </div>
          );
        }

        if (ctrl.type === "encoder") {
          return (
            <div
              key={ctrl.id ?? i}
              onClick={() => onSelectControl(String(i))}
              style={{
                position: "absolute",
                left: ctrl.x ?? 0,
                top: ctrl.y ?? 0,
                width: ctrlWidth,
                height: ctrlHeight,
                borderRadius: "50%",
                background: isSelected ? "var(--accent-dim)" : "var(--bg-surface)",
                border: isSelected ? "2px solid var(--accent)" : "1px solid var(--border-color)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                cursor: "pointer",
                fontSize: 10,
                color: "var(--text-muted)",
              }}
            >
              {ctrl.label ?? "Enc"}
            </div>
          );
        }

        // Default: button
        return (
          <button
            key={ctrl.id ?? i}
            onClick={() => onSelectControl(String(i))}
            style={{
              position: "absolute",
              left: ctrl.x ?? 0,
              top: ctrl.y ?? 0,
              width: ctrlWidth,
              height: ctrlHeight,
              borderRadius: 6,
              background: isSelected ? "var(--accent-dim)" : "var(--bg-surface)",
              border: isSelected ? "2px solid var(--accent)" : "1px solid var(--border-color)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              cursor: "pointer",
              fontSize: 10,
              color: assignment?.label ? "var(--text-primary)" : "var(--text-muted)",
            }}
          >
            {assignment?.label ?? ctrl.label ?? i + 1}
          </button>
        );
      })}
    </div>
  );
}

export function PageTabs({
  currentPage,
  maxPages,
  onChange,
  label,
  onRename,
}: {
  currentPage: number;
  maxPages: number;
  onChange: (page: number) => void;
  label?: string;
  onRename?: (name: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const commit = () => {
    setEditing(false);
    onRename?.(draft);
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-xs)",
        marginBottom: "var(--space-sm)",
      }}
    >
      <button
        onClick={() => onChange(Math.max(0, currentPage - 1))}
        disabled={currentPage === 0}
        style={{
          padding: "var(--space-xs)",
          borderRadius: "var(--border-radius)",
          background: "var(--bg-hover)",
          opacity: currentPage === 0 ? 0.3 : 1,
          cursor: currentPage === 0 ? "default" : "pointer",
        }}
      >
        <ChevronLeft size={14} />
      </button>
      {editing ? (
        <input
          autoFocus
          value={draft}
          placeholder={`Page ${currentPage + 1}`}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") setEditing(false);
          }}
          style={{
            width: 110, padding: "2px 6px", textAlign: "center",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            background: "var(--bg-surface)", color: "var(--text-primary)",
            fontSize: "var(--font-size-sm)",
          }}
        />
      ) : (
        <span
          onDoubleClick={() => {
            if (!onRename) return;
            setDraft(label && label !== `Page ${currentPage + 1}` ? label : "");
            setEditing(true);
          }}
          title={onRename ? "Double-click to rename this page" : undefined}
          style={{
            fontSize: "var(--font-size-sm)", color: "var(--text-secondary)",
            minWidth: 60, textAlign: "center",
            cursor: onRename ? "text" : "default",
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            maxWidth: 140,
          }}
        >
          {label ?? `Page ${currentPage + 1}`}
        </span>
      )}
      <button
        onClick={() => onChange(Math.min(maxPages - 1, currentPage + 1))}
        disabled={currentPage >= maxPages - 1}
        style={{
          padding: "var(--space-xs)",
          borderRadius: "var(--border-radius)",
          background: "var(--bg-hover)",
          opacity: currentPage >= maxPages - 1 ? 0.3 : 1,
          cursor: currentPage >= maxPages - 1 ? "default" : "pointer",
        }}
      >
        <ChevronRight size={14} />
      </button>
    </div>
  );
}
