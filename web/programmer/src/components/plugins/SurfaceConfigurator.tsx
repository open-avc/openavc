/**
 * Surface Configurator — visual editor for control surface plugins.
 *
 * Renders physical hardware layouts (button grids, fader strips, custom layouts,
 * routing matrices) and lets users configure each control (assign macros, icons,
 * feedback keys).
 *
 * Layout types:
 *   grid   — Regular grid (Stream Deck, X-Keys). Row/col positioning.
 *   strip  — Single row/column (MIDI fader bank). Index positioning.
 *   custom — Arbitrary positioned controls. x/y/width/height positioning.
 *   matrix — Routing matrix (Dante, NDI). Dynamic rows/cols from state.
 */
import { useState, useCallback, useRef } from "react";
import { X, Plus, Trash2, ChevronLeft, ChevronRight } from "lucide-react";
import { useConnectionStore } from "../../store/connectionStore";
import { useProjectStore } from "../../store/projectStore";
import { ButtonBindingEditor } from "../shared/ButtonBindingEditor";
import type { ButtonBindings } from "../shared/ButtonBindingEditor";
import { InlineColorPicker } from "../shared/InlineColorPicker";
import { IconPicker } from "../ui-builder/IconPicker";
import { ElementIcon } from "../ui-builder/ElementRenderers/ElementIcon";
import * as api from "../../api/restClient";

// ──── Types ────

interface SurfaceLayout {
  type: "grid" | "strip" | "custom" | "matrix";
  rows?: number;
  columns?: number;
  key_size_px?: number;
  key_spacing_px?: number;
  width_px?: number;
  height_px?: number;
  controls?: ControlDef[];
  supports_pages?: boolean;
  max_pages?: number;
  rows_label?: string;
  columns_label?: string;
  rows_state_pattern?: string;
  columns_state_pattern?: string;
  cell_type?: string;
  cell_state_pattern?: string;
}

interface ControlDef {
  id?: string;
  type: "button" | "fader" | "encoder" | "indicator" | "route";
  position?: [number, number]; // [row, col] for grid
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  has_display?: boolean;
  min?: number;
  max?: number;
  label?: string;
  detents?: number;
}

interface ButtonAssignment {
  index?: number;
  page?: number;
  label?: string;
  icon?: string;
  bg_color?: string;
  text_color?: string;
  // Legacy fields (backward compat)
  macro_id?: string;
  feedback_key?: string;
  // New: same binding format as web UI buttons
  bindings?: ButtonBindings;
}

interface SurfaceConfiguratorProps {
  layout: SurfaceLayout;
  pluginId: string;
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
}

// ──── Main Component ────

export function SurfaceConfigurator({
  layout,
  pluginId,
  config,
  onConfigChange,
}: SurfaceConfiguratorProps) {
  const [selectedControl, setSelectedControl] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(0);

  const buttons = (config.buttons as ButtonAssignment[] | undefined) ?? [];

  const getAssignment = useCallback(
    (index: number, page: number = 0): ButtonAssignment | undefined => {
      return buttons.find((b) => b.index === index && (b.page ?? 0) === page);
    },
    [buttons]
  );

  const updateAssignment = useCallback(
    (index: number, page: number, updates: Partial<ButtonAssignment>) => {
      const existing = buttons.filter(
        (b) => !(b.index === index && (b.page ?? 0) === page)
      );
      const current = buttons.find(
        (b) => b.index === index && (b.page ?? 0) === page
      );
      const updated = { index, page, ...(current ?? {}), ...updates };
      onConfigChange({ ...config, buttons: [...existing, updated] });
    },
    [buttons, config, onConfigChange]
  );

  const clearAssignment = useCallback(
    (index: number, page: number) => {
      const filtered = buttons.filter(
        (b) => !(b.index === index && (b.page ?? 0) === page)
      );
      onConfigChange({ ...config, buttons: filtered });
    },
    [buttons, config, onConfigChange]
  );

  switch (layout.type) {
    case "grid":
      return (
        <div style={{ display: "flex", gap: "var(--space-lg)" }}>
          <div style={{ flex: "0 0 auto" }}>
            {/* Page tabs */}
            {layout.supports_pages && (
              <PageTabs
                currentPage={currentPage}
                maxPages={layout.max_pages ?? 10}
                onChange={setCurrentPage}
              />
            )}
            <GridSurface
              layout={layout}
              currentPage={currentPage}
              selectedControl={selectedControl}
              onSelectControl={setSelectedControl}
              getAssignment={getAssignment}
            />
          </div>
          {selectedControl !== null && (
            <ControlAssignmentPanel
              controlId={selectedControl}
              assignment={getAssignment(
                parseInt(selectedControl),
                currentPage
              )}
              onUpdate={(updates) =>
                updateAssignment(parseInt(selectedControl), currentPage, updates)
              }
              onClear={() =>
                clearAssignment(parseInt(selectedControl), currentPage)
              }
              onClose={() => setSelectedControl(null)}
            />
          )}
        </div>
      );

    case "strip":
      return (
        <div style={{ display: "flex", gap: "var(--space-lg)" }}>
          <StripSurface
            layout={layout}
            selectedControl={selectedControl}
            onSelectControl={setSelectedControl}
            getAssignment={getAssignment}
          />
          {selectedControl !== null && (
            <ControlAssignmentPanel
              controlId={selectedControl}
              assignment={getAssignment(parseInt(selectedControl), 0)}
              onUpdate={(updates) =>
                updateAssignment(parseInt(selectedControl), 0, updates)
              }
              onClear={() => clearAssignment(parseInt(selectedControl), 0)}
              onClose={() => setSelectedControl(null)}
            />
          )}
        </div>
      );

    case "custom":
      return (
        <div style={{ display: "flex", gap: "var(--space-lg)" }}>
          <CustomSurface
            layout={layout}
            selectedControl={selectedControl}
            onSelectControl={setSelectedControl}
            getAssignment={getAssignment}
          />
          {selectedControl !== null && (
            <ControlAssignmentPanel
              controlId={selectedControl}
              assignment={getAssignment(parseInt(selectedControl), 0)}
              onUpdate={(updates) =>
                updateAssignment(parseInt(selectedControl), 0, updates)
              }
              onClear={() => clearAssignment(parseInt(selectedControl), 0)}
              onClose={() => setSelectedControl(null)}
            />
          )}
        </div>
      );

    case "matrix":
      return <RoutingMatrix layout={layout} pluginId={pluginId} />;

    default:
      return (
        <div style={{ color: "var(--text-muted)", padding: "var(--space-lg)" }}>
          Unknown surface type: {layout.type}
        </div>
      );
  }
}

// ──── Grid Surface (Stream Deck, X-Keys) ────

function GridSurface({
  layout,
  currentPage,
  selectedControl,
  onSelectControl,
  getAssignment,
}: {
  layout: SurfaceLayout;
  currentPage: number;
  selectedControl: string | null;
  onSelectControl: (id: string) => void;
  getAssignment: (index: number, page?: number) => ButtonAssignment | undefined;
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
        const hasAssignment = !!assignment?.macro_id || !!assignment?.label || !!assignment?.icon || !!assignment?.bindings?.press;
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
                ? `Button ${i + 1}: ${assignment?.label || assignment?.bindings?.press?.action || assignment?.macro_id || "configured"}`
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

function StripSurface({
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
              index={i}
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
  index,
  label,
  selected,
  onClick,
  assignment,
}: {
  index: number;
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
            background: assignment?.feedback_key ? "var(--accent)" : "var(--text-muted)",
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

function CustomSurface({
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
                index={i}
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

// ──── Routing Matrix ────

function RoutingMatrix({
  layout,
  pluginId,
}: {
  layout: SurfaceLayout;
  pluginId: string;
}) {
  const liveState = useConnectionStore((s) => s.liveState);

  // Get row/column labels from state
  const rowPrefix = (layout.rows_state_pattern ?? "").replace("*", "");
  const colPrefix = (layout.columns_state_pattern ?? "").replace("*", "");

  const rowKeys = Object.keys(liveState)
    .filter((k) => k.startsWith(rowPrefix))
    .sort();
  const colKeys = Object.keys(liveState)
    .filter((k) => k.startsWith(colPrefix))
    .sort();

  // Extract short names
  const rowNames = rowKeys.map((k) => k.slice(rowPrefix.length));
  const colNames = colKeys.map((k) => k.slice(colPrefix.length));

  const getCellState = (row: string, col: string): boolean => {
    const pattern = layout.cell_state_pattern ?? "";
    const key = pattern.replace("{row}", row).replace("{col}", col);
    return Boolean(liveState[key]);
  };

  const handleCellClick = async (row: string, col: string) => {
    const actionId = getCellState(row, col) ? "unroute" : "route";
    await api.emitContextAction(pluginId, actionId, { row, col });
  };

  if (rowNames.length === 0 && colNames.length === 0) {
    return (
      <div style={{ color: "var(--text-muted)", padding: "var(--space-lg)", fontSize: "var(--font-size-sm)" }}>
        No routing data available. The plugin needs to populate state keys matching the configured patterns.
      </div>
    );
  }

  return (
    <div style={{ overflow: "auto" }}>
      {layout.columns_label && (
        <div style={{ textAlign: "center", fontSize: "var(--font-size-sm)", color: "var(--text-muted)", marginBottom: "var(--space-xs)" }}>
          {layout.columns_label}
        </div>
      )}
      <table style={{ borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <th style={{ padding: "var(--space-xs) var(--space-sm)", fontSize: 10, color: "var(--text-muted)" }}>
              {layout.rows_label ?? ""}
            </th>
            {colNames.map((col) => (
              <th
                key={col}
                style={{
                  padding: "var(--space-xs)",
                  fontSize: 10,
                  color: "var(--text-muted)",
                  fontWeight: 400,
                  writingMode: "vertical-lr",
                  transform: "rotate(180deg)",
                  maxHeight: 80,
                }}
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rowNames.map((row) => (
            <tr key={row}>
              <td
                style={{
                  padding: "var(--space-xs) var(--space-sm)",
                  fontSize: 10,
                  color: "var(--text-muted)",
                  whiteSpace: "nowrap",
                }}
              >
                {row}
              </td>
              {colNames.map((col) => {
                const active = getCellState(row, col);
                return (
                  <td key={col} style={{ padding: 1 }}>
                    <button
                      onClick={() => handleCellClick(row, col)}
                      style={{
                        width: 24,
                        height: 24,
                        borderRadius: 3,
                        background: active ? "var(--accent)" : "var(--bg-surface)",
                        border: "1px solid var(--border-color)",
                        cursor: "pointer",
                        transition: "background var(--transition-fast)",
                      }}
                      title={`${row} → ${col}: ${active ? "Routed" : "Unrouted"}`}
                    />
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ──── Control Assignment Panel ────

function ControlAssignmentPanel({
  controlId,
  assignment,
  onUpdate,
  onClear,
  onClose,
}: {
  controlId: string;
  assignment: ButtonAssignment | undefined;
  onUpdate: (updates: Partial<ButtonAssignment>) => void;
  onClear: () => void;
  onClose: () => void;
}) {
  const project = useProjectStore((s) => s.project);

  // Read bindings (with backward compat for old macro_id/feedback_key)
  const currentBindings: ButtonBindings = assignment?.bindings ?? {};
  if (!currentBindings.press && assignment?.macro_id) {
    currentBindings.press = { action: "macro", macro: assignment.macro_id };
  }
  if (!currentBindings.feedback && assignment?.feedback_key) {
    currentBindings.feedback = {
      source: "state", key: assignment.feedback_key,
      condition: { equals: true },
      style_active: { bg_color: "#0f3460" },
      style_inactive: {},
    };
  }

  return (
    <div
      style={{
        width: 300,
        flexShrink: 0,
        background: "var(--bg-surface)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
        padding: "var(--space-md)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-lg)",
        maxHeight: "100%",
        overflow: "auto",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h4 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600 }}>
          Button {parseInt(controlId) + 1}
        </h4>
        <button onClick={onClose} style={{ color: "var(--text-muted)", cursor: "pointer" }}>
          <X size={14} />
        </button>
      </div>

      {/* Icon */}
      <div>
        <label style={panelLabelStyle}>Icon</label>
        <IconPicker
          value={assignment?.icon ?? ""}
          onChange={(icon) => onUpdate({ icon: icon || undefined })}
        />
      </div>

      {/* Default Colors */}
      <div>
        <label style={panelLabelStyle}>Default Colors</label>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
            <span style={panelHintStyle}>Background</span>
            <InlineColorPicker
              value={assignment?.bg_color ?? ""}
              onChange={(c) => onUpdate({ bg_color: c || undefined })}
            />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
            <span style={panelHintStyle}>Text</span>
            <InlineColorPicker
              value={assignment?.text_color ?? ""}
              onChange={(c) => onUpdate({ text_color: c || undefined })}
            />
          </div>
        </div>
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
          Feedback colors override these when active.
        </div>
      </div>

      {/* Shared binding editor — same component the web UI Builder uses */}
      {project ? (
        <ButtonBindingEditor
          bindings={currentBindings}
          label={assignment?.label ?? ""}
          project={project}
          onBindingsChange={(newBindings) =>
            onUpdate({ bindings: newBindings, macro_id: undefined, feedback_key: undefined })
          }
          onLabelChange={(label) => onUpdate({ label: label || undefined })}
          showLabel={true}
        />
      ) : (
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Loading project...</div>
      )}

      {/* Clear All */}
      <button
        onClick={onClear}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: "var(--space-xs)",
          padding: "var(--space-sm)",
          borderRadius: "var(--border-radius)",
          background: "transparent",
          border: "1px solid var(--border-color)",
          color: "var(--color-error)",
          fontSize: "var(--font-size-sm)",
          cursor: "pointer",
        }}
      >
        <Trash2 size={12} />
        Clear Assignment
      </button>
    </div>
  );
}

// ──── Page Tabs ────

function PageTabs({
  currentPage,
  maxPages,
  onChange,
}: {
  currentPage: number;
  maxPages: number;
  onChange: (page: number) => void;
}) {
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
      <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", minWidth: 60, textAlign: "center" }}>
        Page {currentPage + 1}
      </span>
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

// ──── Shared Styles ────

const panelLabelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 12,
  fontWeight: 600,
  color: "var(--text-secondary)",
  marginBottom: "var(--space-xs)",
};

const panelHintStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  width: 56,
  flexShrink: 0,
};
