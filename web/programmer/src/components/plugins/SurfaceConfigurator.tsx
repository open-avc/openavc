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
import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { X, Trash2, ChevronLeft, ChevronRight, Usb, Pin, Play, MoreHorizontal } from "lucide-react";
import { CopyButton } from "../shared/CopyButton";
import { showInfo } from "../../store/toastStore";
import { CollapsibleSection } from "../driver-builder/CollapsibleSection";
import { useConnectionStore } from "../../store/connectionStore";
import { useProjectStore } from "../../store/projectStore";
import { ButtonBindingEditor } from "../shared/ButtonBindingEditor";
import type { ButtonBindings } from "../shared/ButtonBindingEditor";
import { ConditionGroupEditor, type ConditionGroup } from "../shared/ConditionGroupEditor";
import { VisibilityProperties } from "../ui-builder/PropertySections/VisibilityProperties";
import { InlineColorPicker } from "../shared/InlineColorPicker";
import { VariableKeyPicker } from "../shared/VariableKeyPicker";
import { ActionPicker } from "../ui-builder/BindingEditor/ActionPicker";
import { IconPicker } from "../ui-builder/IconPicker";
import { ElementIcon } from "../ui-builder/ElementIcon";
import type { ProjectConfig } from "../../api/types";
import * as api from "../../api/restClient";
import { BASE } from "../../api/base";

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
  presets?: boolean;
  // Device-backed surfaces (declared by the plugin): the editor renders only
  // real units (connected hardware or virtual units). With none present, a
  // connect / add-virtual empty state shows instead of the static layout.
  requires_device?: boolean;
  device_label?: string;
  virtual_models?: string[];
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

interface MeterConfig {
  min?: number;
  max?: number;
  color?: string;
  thresholds?: { above?: number; color?: string }[];
}

// Conditional styling shared by zones and info items (same schema as key
// feedback; the runtime resolves all of them through one path).
interface DisplayFeedback {
  key?: string;
  condition?: { equals?: string };
  style_active?: { bg_color?: string; text_color?: string };
  style_inactive?: { bg_color?: string; text_color?: string };
}

interface ButtonAssignment {
  index?: number;
  page?: number;
  label?: string;
  icon?: string;
  bg_color?: string;
  text_color?: string;
  // Live display: label/value from state, optional meter bar
  label_source?: string;
  value_source?: string;
  unit?: string;
  meter?: MeterConfig | boolean;
  // Same binding format as web UI buttons
  bindings?: ButtonBindings;
}

interface DialAdjust {
  key?: string;
  step?: number;
  min?: number;
  max?: number;
  fader?: boolean;
}

interface DialAssignment {
  index?: number;
  label?: string;
  icon?: string;
  unit?: string;
  meter?: MeterConfig | boolean;
  adjust?: DialAdjust;
  cw?: Record<string, unknown>[];
  ccw?: Record<string, unknown>[];
  press?: Record<string, unknown>[];
  long_press?: Record<string, unknown>[];
  hold_threshold_ms?: number;
  pressed_adjust?: DialAdjust;
  pressed_cw?: Record<string, unknown>[];
  pressed_ccw?: Record<string, unknown>[];
  // The dial's strip zone is its touch surface
  touch?: Record<string, unknown>[];
  long_touch?: Record<string, unknown>[];
  fader?: boolean;
}

interface TouchZone {
  label?: string;
  label_source?: string;
  value_source?: string;
  unit?: string;
  icon?: string;
  meter?: MeterConfig | boolean;
  feedback?: DisplayFeedback;
  bg_color?: string;
  text_color?: string;
  x?: number;
  w?: number;
  touch?: Record<string, unknown>[];
  long_touch?: Record<string, unknown>[];
  drag_adjust?: DialAdjust;
}

interface SurfaceConfiguratorProps {
  layout: SurfaceLayout;
  pluginId: string;
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
  onRequestConfigRefresh?: () => void;
}

// ──── Main Component ────

export function SurfaceConfigurator({
  layout: staticLayout,
  pluginId,
  config,
  onConfigChange,
  onRequestConfigRefresh,
}: SurfaceConfiguratorProps) {
  const [selectedControl, setSelectedControl] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(0);

  const liveState = useConnectionStore((s) => s.liveState);
  const statePrefix = `plugin.${pluginId}.`;
  const deckSerials = String(liveState[`${statePrefix}deck_serials`] ?? "")
    .split(",")
    .filter(Boolean);
  // Units the project remembers even when they aren't connected: anything
  // with its own layout or a friendly name stays visible (dimmed) so a
  // saved layout is never stranded behind a dead or unplugged unit.
  const decksMap =
    (config.decks as Record<string, Record<string, unknown>> | undefined) ?? {};
  const deckNames = (config.deck_names as Record<string, string> | undefined) ?? {};
  const rememberedSerials = [
    ...new Set([...Object.keys(decksMap), ...Object.keys(deckNames)]),
  ].filter((s) => !deckSerials.includes(s));
  const knownSerials = [...deckSerials, ...rememberedSerials];

  // Flat-config assignment helpers for the simple layout types (strip,
  // custom, static grid). Device-backed grids manage their own state inside
  // DeckWorkbench.
  const buttons = (config.buttons as ButtonAssignment[] | undefined) ?? [];
  const supportsPages = !!staticLayout.supports_pages;
  const maxPages = staticLayout.max_pages ?? 10;
  const allowedActions = supportsPages
    ? ["macro", "device.command", "state.set", "navigate"]
    : ["macro", "device.command", "state.set"];
  const navigateOptions = supportsPages
    ? [
        { value: "__next_page__", label: "Next Page" },
        { value: "__prev_page__", label: "Previous Page" },
        ...Array.from({ length: maxPages }, (_, p) => ({
          value: String(p),
          label: `Page ${p + 1}`,
        })),
      ]
    : undefined;

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

  switch (staticLayout.type) {
    case "grid":
      // Device-backed surface: the workbench (live canvas + inspector rail).
      // With no unit at all — connected or remembered — an honest empty
      // state instead of an editable grid for hardware that isn't there.
      if (staticLayout.requires_device) {
        if (knownSerials.length === 0) {
          return (
            <NoDeviceState
              layout={staticLayout}
              config={config}
              onConfigChange={onConfigChange}
            />
          );
        }
        return (
          <DeckWorkbench
            pluginId={pluginId}
            staticLayout={staticLayout}
            config={config}
            onConfigChange={onConfigChange}
          />
        );
      }
      // Static-grid plugins (no requires_device): the classic schematic grid
      // with the declared geometry and max_pages cap.
      return (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-lg)" }}>
          <div style={{ display: "flex", gap: "var(--space-lg)" }}>
            <div style={{ flex: "0 0 auto" }}>
              {supportsPages && (
                <PageTabs
                  currentPage={currentPage}
                  maxPages={maxPages}
                  onChange={setCurrentPage}
                />
              )}
              <GridSurface
                layout={staticLayout}
                currentPage={currentPage}
                selectedControl={selectedControl}
                onSelectControl={setSelectedControl}
                getAssignment={getAssignment}
              />
            </div>
            {selectedControl !== null && (
              <ControlAssignmentPanel
                controlId={selectedControl}
                allowedActions={allowedActions}
                navigateOptions={navigateOptions}
                assignment={getAssignment(parseInt(selectedControl), currentPage)}
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
          {supportsPages && (
            <CollapsibleSection
              title="Page automation"
              subtitle="Jump to a button page when system state changes"
              defaultOpen={false}
            >
              <AutoPageEditor
                layout={staticLayout}
                config={config}
                onConfigChange={onConfigChange}
              />
            </CollapsibleSection>
          )}
        </div>
      );

    case "strip":
      return (
        <div style={{ display: "flex", gap: "var(--space-lg)" }}>
          <StripSurface
            layout={staticLayout}
            selectedControl={selectedControl}
            onSelectControl={setSelectedControl}
            getAssignment={getAssignment}
          />
          {selectedControl !== null && (
            <ControlAssignmentPanel
              controlId={selectedControl}
              allowedActions={allowedActions}
              navigateOptions={navigateOptions}
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
            layout={staticLayout}
            selectedControl={selectedControl}
            onSelectControl={setSelectedControl}
            getAssignment={getAssignment}
          />
          {selectedControl !== null && (
            <ControlAssignmentPanel
              controlId={selectedControl}
              allowedActions={allowedActions}
              navigateOptions={navigateOptions}
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
      return <RoutingMatrix layout={staticLayout} pluginId={pluginId} config={config} onRequestConfigRefresh={onRequestConfigRefresh} />;

    default:
      return (
        <div style={{ color: "var(--text-muted)", padding: "var(--space-lg)" }}>
          Unknown surface type: {staticLayout.type}
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
        const hasAssignment = !!assignment?.label || !!assignment?.icon || !!assignment?.bindings?.press;
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
  config,
  onRequestConfigRefresh,
}: {
  layout: SurfaceLayout;
  pluginId: string;
  config: Record<string, unknown>;
  onRequestConfigRefresh?: () => void;
}) {
  const liveState = useConnectionStore((s) => s.liveState);
  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [newPresetName, setNewPresetName] = useState("");
  const [presetDropdownOpen, setPresetDropdownOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const triggerBtnRef = useRef<HTMLButtonElement>(null);
  const [dropdownPos, setDropdownPos] = useState<{ top?: number; bottom?: number; left: number; width: number }>({ left: 0, width: 180 });

  // Close preset dropdown on click outside or scroll
  useEffect(() => {
    if (!presetDropdownOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setPresetDropdownOpen(false);
      }
    };
    const handleScroll = (e: Event) => {
      if (dropdownRef.current && dropdownRef.current.contains(e.target as Node)) return;
      setPresetDropdownOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("scroll", handleScroll, true);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("scroll", handleScroll, true);
    };
  }, [presetDropdownOpen]);

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

  // Preset support
  const showPresets = layout.presets === true;
  const presets = (config?._presets as Record<string, unknown[]>) ?? {};
  const presetNames = Object.keys(presets);
  const activePreset = String(liveState[`plugin.${pluginId}.active_preset`] ?? "");
  const isDirty = Boolean(liveState[`plugin.${pluginId}.preset_dirty`]);

  const handleRecallPreset = async (name: string) => {
    setPresetDropdownOpen(false);
    await api.emitContextAction(pluginId, "recall_preset", { preset_name: name });
  };

  const handleSavePreset = async () => {
    const name = newPresetName.trim();
    if (!name) return;
    await api.emitContextAction(pluginId, "save_preset", { name });
    setNewPresetName("");
    setShowSaveDialog(false);
    onRequestConfigRefresh?.();
  };

  const handleUpdatePreset = async () => {
    if (!activePreset) return;
    await api.emitContextAction(pluginId, "update_preset", { name: activePreset });
    onRequestConfigRefresh?.();
  };

  const handleDeletePreset = async (name: string) => {
    await api.emitContextAction(pluginId, "delete_preset", { name });
    setConfirmDelete(null);
    onRequestConfigRefresh?.();
  };

  const hasData = rowNames.length > 0 || colNames.length > 0;

  const btnStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: "var(--space-xs)",
    padding: "var(--space-xs) var(--space-sm)",
    borderRadius: "var(--border-radius)",
    background: "var(--bg-hover)",
    fontSize: "var(--font-size-sm)",
    cursor: "pointer",
    whiteSpace: "nowrap",
  };

  return (
    <div>
      {/* Preset toolbar */}
      {showPresets && (
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          marginBottom: "var(--space-md)",
          flexWrap: "wrap",
        }}>
          {/* Preset dropdown */}
          <div ref={dropdownRef}>
            <button
              ref={triggerBtnRef}
              onClick={() => {
                if (!presetDropdownOpen && triggerBtnRef.current) {
                  const rect = triggerBtnRef.current.getBoundingClientRect();
                  const spaceBelow = window.innerHeight - rect.bottom;
                  const spaceAbove = rect.top;
                  const flipUp = spaceBelow < 220 && spaceAbove > spaceBelow;
                  setDropdownPos(flipUp
                    ? { bottom: window.innerHeight - rect.top + 4, left: rect.left, width: Math.max(rect.width, 180) }
                    : { top: rect.bottom + 4, left: rect.left, width: Math.max(rect.width, 180) });
                }
                setPresetDropdownOpen(!presetDropdownOpen);
              }}
              style={{
                ...btnStyle,
                border: "1px solid var(--border-color)",
                background: "var(--bg-surface)",
                minWidth: 150,
              }}
            >
              <span style={{ flex: 1, textAlign: "left" }}>
                {activePreset || "No preset"}
                {activePreset && isDirty && (
                  <span style={{ color: "var(--color-warning, #f59e0b)", marginLeft: 4, fontSize: 11 }}>
                    (modified)
                  </span>
                )}
              </span>
              <ChevronRight size={14} style={{ transform: presetDropdownOpen ? "rotate(90deg)" : "rotate(0)", transition: "transform 0.15s" }} />
            </button>
            {presetDropdownOpen && (
              <div style={{
                position: "fixed",
                top: dropdownPos.top,
                bottom: dropdownPos.bottom,
                left: dropdownPos.left,
                minWidth: dropdownPos.width,
                background: "var(--bg-surface)",
                border: "1px solid var(--border-color)",
                borderRadius: "var(--border-radius)",
                boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
                zIndex: 9999,
                maxHeight: 200,
                overflow: "auto",
              }}>
                {presetNames.length === 0 && (
                  <div style={{ padding: "var(--space-sm) var(--space-md)", color: "var(--text-muted)", fontSize: 12 }}>
                    No presets saved yet
                  </div>
                )}
                {presetNames.map((name) => (
                  <button
                    key={name}
                    onClick={() => handleRecallPreset(name)}
                    style={{
                      display: "block",
                      width: "100%",
                      textAlign: "left",
                      padding: "var(--space-sm) var(--space-md)",
                      background: name === activePreset ? "var(--bg-hover)" : "transparent",
                      fontSize: "var(--font-size-sm)",
                      cursor: "pointer",
                    }}
                  >
                    {name}
                    {name === activePreset && <span style={{ color: "var(--text-muted)", marginLeft: 6, fontSize: 11 }}>(active)</span>}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Save as New */}
          {hasData && !showSaveDialog && (
            <button onClick={() => setShowSaveDialog(true)} style={btnStyle}>
              Save as New...
            </button>
          )}
          {showSaveDialog && (
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
              <input
                value={newPresetName}
                onChange={(e) => setNewPresetName(e.target.value)}
                placeholder="Preset name"
                onKeyDown={(e) => e.key === "Enter" && handleSavePreset()}
                autoFocus
                style={{
                  padding: "var(--space-xs) var(--space-sm)",
                  borderRadius: "var(--border-radius)",
                  border: "1px solid var(--border-color)",
                  background: "var(--bg-surface)",
                  color: "var(--text-primary)",
                  fontSize: "var(--font-size-sm)",
                  width: 140,
                }}
              />
              <button onClick={handleSavePreset} style={{ ...btnStyle, background: "var(--accent-bg)", color: "white" }}>Save</button>
              <button onClick={() => { setShowSaveDialog(false); setNewPresetName(""); }} style={btnStyle}>Cancel</button>
            </div>
          )}

          {/* Update existing */}
          {activePreset && isDirty && (
            <button onClick={handleUpdatePreset} style={{ ...btnStyle, background: "var(--accent-bg)", color: "white" }}>
              Update "{activePreset}"
            </button>
          )}

          {/* Delete */}
          {activePreset && !confirmDelete && (
            <button
              onClick={() => setConfirmDelete(activePreset)}
              style={{ ...btnStyle, color: "var(--text-muted)" }}
              title="Delete preset"
            >
              <Trash2 size={14} />
            </button>
          )}
          {confirmDelete && (
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", fontSize: 12 }}>
              <span style={{ color: "var(--color-error, #ef4444)" }}>Delete "{confirmDelete}"?</span>
              <button onClick={() => handleDeletePreset(confirmDelete)} style={{ ...btnStyle, fontSize: 12 }}>Yes</button>
              <button onClick={() => setConfirmDelete(null)} style={{ ...btnStyle, fontSize: 12 }}>No</button>
            </div>
          )}
        </div>
      )}

      {/* Empty state */}
      {!hasData && (
        <div style={{
          padding: "var(--space-xl)",
          textAlign: "center",
          color: "var(--text-muted)",
        }}>
          <div style={{ fontSize: "var(--font-size-base)", fontWeight: 500, marginBottom: "var(--space-sm)" }}>
            Routing Matrix
          </div>
          <div style={{ fontSize: "var(--font-size-sm)", maxWidth: 420, margin: "0 auto", lineHeight: 1.5 }}>
            The routing matrix will appear here once the plugin connects and discovers
            devices. Click crosspoints to route audio between transmitters and receivers.
            {showPresets && " Save your routing configuration as presets to recall them later."}
          </div>
        </div>
      )}

      {/* Matrix table */}
      {hasData && (
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
                            background: active ? "var(--accent-bg)" : "var(--bg-surface)",
                            border: "1px solid var(--border-color)",
                            cursor: "pointer",
                            transition: "background var(--transition-fast)",
                          }}
                          title={`${row} \u2192 ${col}: ${active ? "Routed" : "Unrouted"}`}
                        />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ──── Control Assignment Panel ────

interface ArrangeOps {
  page: number;
  maxPages: number;
  totalKeys: number;
  pageLabel: (p: number) => string;
  clipboardReady: boolean;
  onCopy: () => void;
  onPaste: () => void;
  onMove: (to: { index: number; page: number }) => void;
  onSwap: (to: { index: number; page: number }) => void;
}

function ControlAssignmentPanel({
  controlId,
  assignment,
  onUpdate,
  onClear,
  onClose,
  allowedActions,
  navigateOptions,
  colorOnly = false,
  keyCount = 0,
  arrange,
  pageName,
  locked,
  onToggleLock,
  lockShadowCount = 0,
  onPress,
  visualDeck = true,
}: {
  controlId: string;
  assignment: ButtonAssignment | undefined;
  onUpdate: (updates: Partial<ButtonAssignment>) => void;
  onClear: () => void;
  onClose: () => void;
  allowedActions?: string[];
  navigateOptions?: { value: string; label: string }[];
  // Touch keys have no LCD: only the background color (RGB glow) applies.
  colorOnly?: boolean;
  keyCount?: number;
  arrange?: ArrangeOps;
  // Workbench extras: page context in the title, the lock toggle, and a
  // real press (simulate_input) button.
  pageName?: string;
  locked?: boolean;
  onToggleLock?: (locked: boolean) => void;
  lockShadowCount?: number;
  onPress?: () => void;
  // False for display-less decks (foot pedals): hide everything visual.
  visualDeck?: boolean;
}) {
  const project = useProjectStore((s) => s.project);
  const [arrangeMode, setArrangeMode] = useState<"move" | "swap" | null>(null);
  const [targetPage, setTargetPage] = useState(0);
  const [targetKey, setTargetKey] = useState(0);
  const [moreOpen, setMoreOpen] = useState(false);

  const currentBindings: ButtonBindings = assignment?.bindings ?? {};
  const controlIndex = parseInt(controlId);
  const keyNoun = colorOnly
    ? `Touch Key ${controlIndex - keyCount + 1}`
    : onToggleLock
      ? `Key ${controlIndex + 1}`
      : `Button ${controlIndex + 1}`;
  const title = locked
    ? `${keyNoun} — every page`
    : pageName
      ? `${keyNoun} — ${pageName}`
      : keyNoun;

  const whatItShows = !visualDeck ? (
    <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
      This model has no display — the label only names the switch in the
      editor, and there is nothing for colors or feedback to change.
    </div>
  ) : (
    <div>
      <label style={panelLabelStyle}>{colorOnly ? "Key Color" : "What It Shows"}</label>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
        {!colorOnly && (
          <IconPicker
            value={assignment?.icon ?? ""}
            onChange={(icon) => onUpdate({ icon: icon || undefined })}
          />
        )}
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
          <span style={panelHintStyle}>Background</span>
          <InlineColorPicker
            value={assignment?.bg_color ?? ""}
            onChange={(c) => onUpdate({ bg_color: c || undefined })}
          />
        </div>
        {!colorOnly && (
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
            <span style={panelHintStyle}>Text</span>
            <InlineColorPicker
              value={assignment?.text_color ?? ""}
              onChange={(c) => onUpdate({ text_color: c || undefined })}
            />
          </div>
        )}
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
        {colorOnly
          ? "This key has no display — it glows with this color. Feedback colors override it when active; labels and icons don't apply."
          : "Feedback colors override these when active."}
      </div>
      {!colorOnly && (
        <div style={{ marginTop: "var(--space-md)", display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
          <div>
            <label style={panelHintStyle}>Live value from state (optional)</label>
            <VariableKeyPicker
              value={assignment?.value_source ?? ""}
              onChange={(key) => onUpdate({ value_source: key || undefined })}
              placeholder="Show a state key's live value..."
            />
          </div>
          {assignment?.value_source && (
            <div style={{ display: "flex", gap: "var(--space-sm)" }}>
              <div style={{ width: 80 }}>
                <label style={panelHintStyle}>Unit</label>
                <input
                  type="text"
                  value={assignment?.unit ?? ""}
                  placeholder="dB, %"
                  onChange={(e) => onUpdate({ unit: e.target.value || undefined })}
                  style={fieldInputStyle}
                />
              </div>
              <div style={{ flex: 1 }}>
                <MeterFields
                  meter={assignment?.meter}
                  onChange={(meter) => onUpdate({ meter })}
                />
              </div>
            </div>
          )}
          <div>
            <label style={panelHintStyle}>Label from state (optional)</label>
            <VariableKeyPicker
              value={assignment?.label_source ?? ""}
              onChange={(key) => onUpdate({ label_source: key || undefined })}
              placeholder="Live label overriding the static one..."
            />
          </div>
        </div>
      )}
    </div>
  );

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
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "var(--space-sm)" }}>
        <h4 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {title}
        </h4>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", flexShrink: 0 }}>
          {onPress && (
            <button
              onClick={onPress}
              title="Press this key for real (same as pushing it on the deck)"
              style={{
                display: "flex", alignItems: "center", gap: 4,
                padding: "2px 8px", borderRadius: "var(--border-radius)",
                background: "var(--bg-hover)", color: "var(--text-secondary)",
                fontSize: 11, cursor: "pointer",
              }}
            >
              <Play size={11} /> Press
            </button>
          )}
          <button onClick={onClose} style={{ color: "var(--text-muted)", cursor: "pointer" }}>
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Lock: keep this key identical on every page */}
      {onToggleLock && (
        <div>
          <label
            style={{
              display: "flex", alignItems: "center", gap: "var(--space-sm)",
              fontSize: "var(--font-size-sm)", cursor: "pointer",
              color: "var(--text-primary)",
            }}
          >
            <input
              type="checkbox"
              checked={!!locked}
              onChange={(e) => onToggleLock(e.target.checked)}
              style={{ accentColor: "var(--accent)" }}
            />
            <Pin size={13} style={{ color: locked ? "var(--accent)" : "var(--text-muted)" }} />
            Same on every page
          </label>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
            Locked keys keep this assignment on every page. Great for page
            switchers.
            {!locked && lockShadowCount > 0 && (
              <>
                {" "}
                <span style={{ color: "var(--color-warning, #f59e0b)" }}>
                  {lockShadowCount} page{lockShadowCount === 1 ? " has" : "s have"} something
                  on this key; that stays hidden while it's locked.
                </span>
              </>
            )}
          </div>
        </div>
      )}

      {/* Shared binding editor — same component the web UI Builder uses.
          Surface order: what it does first, then label, press style, feedback. */}
      {project ? (
        <ButtonBindingEditor
          bindings={currentBindings}
          label={assignment?.label ?? ""}
          project={project}
          onBindingsChange={(newBindings) =>
            onUpdate({ bindings: newBindings })
          }
          onLabelChange={(label) => onUpdate({ label: label || undefined })}
          showLabel={!colorOnly}
          showToggleLabels={!colorOnly && visualDeck}
          showFeedback={visualDeck}
          allowedActions={allowedActions}
          navigateOptions={navigateOptions}
          surfaceOrder
        />
      ) : (
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Loading project...</div>
      )}

      {whatItShows}

      {/* More: visibility + arrange, tucked away */}
      <div style={{ border: "1px solid var(--border-color)", borderRadius: "var(--border-radius)", overflow: "hidden" }}>
        <button
          onClick={() => setMoreOpen(!moreOpen)}
          style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            width: "100%", padding: "6px 10px", fontSize: "var(--font-size-sm)",
            background: "var(--bg-surface)", textAlign: "left", cursor: "pointer",
          }}
        >
          <span style={{ fontWeight: 500 }}>More</span>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
            visibility{locked ? "" : " · arrange"}
          </span>
        </button>
        {moreOpen && (
          <div style={{ padding: "var(--space-sm)", borderTop: "1px solid var(--border-color)", display: "flex", flexDirection: "column", gap: "var(--space-lg)" }}>

      {/* Visibility — hide this button based on system state */}
      <div>
        <label style={panelLabelStyle}>Visibility</label>
        <VisibilityProperties
          element={{ bindings: currentBindings as unknown as Record<string, unknown> }}
          onChange={(patch) =>
            onUpdate({
              bindings: patch.bindings as unknown as ButtonBindings,
            })
          }
        />
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
          A hidden button shows as a blank key and ignores presses.
        </div>
      </div>

      {/* Arrange: copy/paste/move/swap (page keys only — locked keys are
          everywhere already) */}
      {arrange && !locked && (
        <div>
          <label style={panelLabelStyle}>Arrange</label>
          <div style={{ display: "flex", gap: "var(--space-xs)", flexWrap: "wrap" }}>
            <button onClick={arrange.onCopy} disabled={!assignment} style={arrangeBtnStyle(!assignment)}>
              Copy
            </button>
            <button
              onClick={arrange.onPaste}
              disabled={!arrange.clipboardReady}
              title={arrange.clipboardReady ? "Paste the copied assignment here" : "Copy an assignment first"}
              style={arrangeBtnStyle(!arrange.clipboardReady)}
            >
              Paste
            </button>
            <button
              onClick={() => setArrangeMode(arrangeMode === "move" ? null : "move")}
              disabled={!assignment}
              style={arrangeBtnStyle(!assignment, arrangeMode === "move")}
            >
              Move to...
            </button>
            <button
              onClick={() => setArrangeMode(arrangeMode === "swap" ? null : "swap")}
              disabled={!assignment}
              style={arrangeBtnStyle(!assignment, arrangeMode === "swap")}
            >
              Swap with...
            </button>
          </div>
          {arrangeMode && (
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", marginTop: "var(--space-sm)" }}>
              <select
                value={targetPage}
                onChange={(e) => setTargetPage(Number(e.target.value))}
                style={{
                  padding: "4px 6px", borderRadius: "var(--border-radius)",
                  border: "1px solid var(--border-color)",
                  background: "var(--bg-surface)", color: "var(--text-primary)",
                  fontSize: "var(--font-size-sm)", flex: 1,
                }}
              >
                {Array.from({ length: arrange.maxPages }, (_, p) => (
                  <option key={p} value={p}>{arrange.pageLabel(p)}</option>
                ))}
              </select>
              <select
                value={targetKey}
                onChange={(e) => setTargetKey(Number(e.target.value))}
                style={{
                  padding: "4px 6px", borderRadius: "var(--border-radius)",
                  border: "1px solid var(--border-color)",
                  background: "var(--bg-surface)", color: "var(--text-primary)",
                  fontSize: "var(--font-size-sm)", width: 96,
                }}
              >
                {Array.from({ length: arrange.totalKeys }, (_, k) => (
                  <option key={k} value={k}>Key {k + 1}</option>
                ))}
              </select>
              <button
                onClick={() => {
                  const to = { index: targetKey, page: targetPage };
                  if (arrangeMode === "move") arrange.onMove(to);
                  else arrange.onSwap(to);
                  setArrangeMode(null);
                }}
                style={{
                  padding: "4px 10px", borderRadius: "var(--border-radius)",
                  background: "var(--accent-bg)", color: "white",
                  fontSize: "var(--font-size-sm)", cursor: "pointer",
                }}
              >
                Go
              </button>
            </div>
          )}
          {arrangeMode === "move" && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
              Moving replaces whatever is at the target.
            </div>
          )}
        </div>
      )}
          </div>
        )}
      </div>

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

const arrangeBtnStyle = (disabled: boolean, active = false): React.CSSProperties => ({
  padding: "4px 10px",
  borderRadius: "var(--border-radius)",
  border: active ? "1px solid var(--accent)" : "1px solid var(--border-color)",
  background: active ? "var(--accent-dim)" : "var(--bg-hover)",
  color: disabled ? "var(--text-muted)" : "var(--text-secondary)",
  fontSize: "var(--font-size-sm)",
  cursor: disabled ? "default" : "pointer",
  opacity: disabled ? 0.5 : 1,
});

// ──── No Device State ────
//
// Shown by device-backed surfaces (layout.requires_device) when no unit is
// connected: a plain explanation of how a unit appears, plus the add-virtual
// path when the plugin declares virtual models. Replaces the old behavior of
// rendering the static fallback grid as if hardware were attached.

function addVirtualUnit(
  config: Record<string, unknown>,
  model: string
): { next: Record<string, unknown>; serial: string } {
  const entries =
    (config.virtual_decks as { model?: string; serial?: string }[] | undefined) ?? [];
  const serial = `VIRT-${Date.now().toString(36).toUpperCase()}`;
  return {
    next: { ...config, virtual_decks: [...entries, { model, serial }] },
    serial,
  };
}

function NoDeviceState({
  layout,
  config,
  onConfigChange,
}: {
  layout: SurfaceLayout;
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
}) {
  const noun = layout.device_label || "device";
  const models = layout.virtual_models ?? [];
  const [model, setModel] = useState(models[0] ?? "");
  const [pending, setPending] = useState(false);

  // If the save fails silently, don't leave the button dead forever.
  useEffect(() => {
    if (!pending) return;
    const timer = setTimeout(() => setPending(false), 10000);
    return () => clearTimeout(timer);
  }, [pending]);

  const add = () => {
    if (!model || pending) return;
    onConfigChange(addVirtualUnit(config, model).next);
    setPending(true);
  };

  return (
    <div
      style={{
        maxWidth: 460,
        margin: "var(--space-xl) auto",
        padding: "var(--space-xl)",
        border: "1px solid var(--border-color)",
        borderRadius: "var(--border-radius)",
        background: "var(--bg-surface)",
        textAlign: "center",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: "var(--space-md)",
      }}
    >
      <Usb size={40} strokeWidth={1.2} style={{ color: "var(--text-muted)" }} />
      <div style={{ fontWeight: 600 }}>No {noun} detected</div>
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
          lineHeight: 1.6,
        }}
      >
        Connect a {noun} by USB and it appears here automatically, ready to
        set up.
      </div>
      {models.length > 0 && (
        <>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-sm)",
              width: "100%",
              color: "var(--text-muted)",
              fontSize: 11,
            }}
          >
            <span style={{ flex: 1, borderTop: "1px solid var(--border-color)" }} />
            or
            <span style={{ flex: 1, borderTop: "1px solid var(--border-color)" }} />
          </div>
          <div style={{ display: "flex", gap: "var(--space-sm)" }}>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              style={{
                padding: "var(--space-xs) var(--space-sm)",
                borderRadius: "var(--border-radius)",
                border: "1px solid var(--border-color)",
                background: "var(--bg-surface)",
                color: "var(--text-primary)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              {models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            <button
              onClick={add}
              disabled={pending}
              style={{
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "var(--border-radius)",
                background: "var(--accent-bg)",
                color: "var(--text-on-accent)",
                fontSize: "var(--font-size-sm)",
                fontWeight: 500,
                cursor: pending ? "default" : "pointer",
                opacity: pending ? 0.6 : 1,
              }}
            >
              {pending ? "Starting..." : `Add virtual ${noun}`}
            </button>
          </div>
          <div
            style={{
              fontSize: 11,
              color: "var(--text-muted)",
              lineHeight: 1.6,
              maxWidth: 360,
            }}
          >
            {pending
              ? `Saving... the virtual ${noun} appears here in a few seconds.`
              : `A virtual ${noun} works exactly like plugged-in hardware: build and test the layout now, and a real ${noun} picks it up the moment it's connected.`}
          </div>
        </>
      )}
    </div>
  );
}

// ──── Action List Editor (ordered list of surface actions) ────

function ActionListEditor({
  actions,
  onChange,
  project,
  allowedActions,
  navigateOptions,
  addLabel,
}: {
  actions: Record<string, unknown>[];
  onChange: (actions: Record<string, unknown>[]) => void;
  project: ProjectConfig;
  allowedActions?: string[];
  navigateOptions?: { value: string; label: string }[];
  addLabel: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      {actions.map((act, i) => (
        <div
          key={i}
          style={{
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            padding: "var(--space-sm)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-xs)" }}>
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Action {i + 1}</span>
            <button
              onClick={() => onChange(actions.filter((_, j) => j !== i))}
              style={{
                padding: "2px 6px", borderRadius: "var(--border-radius)",
                fontSize: 11, color: "var(--color-error)",
                background: "transparent", border: "1px solid var(--border-color)",
                cursor: "pointer",
              }}
            >
              Remove
            </button>
          </div>
          <ActionPicker
            value={act}
            project={project}
            onChange={(v) => onChange(actions.map((a, j) => (j === i ? v : a)))}
            allowedActions={allowedActions}
            navigateOptions={navigateOptions}
          />
        </div>
      ))}
      <button
        onClick={() => onChange([...actions, { action: "" }])}
        style={{
          display: "flex", alignItems: "center", justifyContent: "center", gap: 4,
          padding: "5px 10px", borderRadius: "var(--border-radius)",
          border: "1px dashed var(--border-color)", background: "transparent",
          color: "var(--text-muted)", fontSize: 12, cursor: "pointer",
        }}
      >
        + {addLabel}
      </button>
    </div>
  );
}

// ──── Dial Assignment Panel ────

function DialAssignmentPanel({
  dialIndex,
  dial,
  onUpdate,
  onClear,
  onClose,
  allowedActions,
  navigateOptions,
  onSimulate,
  onOpenStrip,
}: {
  dialIndex: number;
  dial: DialAssignment | undefined;
  onUpdate: (updates: Partial<DialAssignment>) => void;
  onClear: () => void;
  onClose: () => void;
  allowedActions?: string[];
  navigateOptions?: { value: string; label: string }[];
  // Workbench extra: fire real dial input (simulate_input) to test it.
  onSimulate?: (payload: Record<string, unknown>) => void;
  // Jump to the whole-strip zone editor.
  onOpenStrip?: () => void;
}) {
  const project = useProjectStore((s) => s.project);
  const adjust = dial?.adjust ?? {};
  const pressedAdjust = dial?.pressed_adjust ?? {};

  const updateAdjust = (patch: Partial<DialAdjust>) => {
    const next = { ...adjust, ...patch };
    // Strip empty fields so a cleared adjust disappears from the config
    if (!next.key) {
      onUpdate({ adjust: undefined });
    } else {
      onUpdate({ adjust: next });
    }
  };
  const updatePressedAdjust = (patch: Partial<DialAdjust>) => {
    const next = { ...pressedAdjust, ...patch };
    if (!next.key) {
      onUpdate({ pressed_adjust: undefined });
    } else {
      onUpdate({ pressed_adjust: next });
    }
  };

  const numberField = (
    label: string,
    field: "step" | "min" | "max",
    placeholder: string
  ) => (
    <div style={{ flex: 1 }}>
      <label style={panelHintStyle}>{label}</label>
      <input
        type="number"
        value={adjust[field] ?? ""}
        placeholder={placeholder}
        onChange={(e) => {
          const raw = e.target.value;
          updateAdjust({ [field]: raw === "" ? undefined : Number(raw) });
        }}
        style={{
          width: "100%", padding: "4px 6px",
          borderRadius: "var(--border-radius)",
          border: "1px solid var(--border-color)",
          background: "var(--bg-surface)", color: "var(--text-primary)",
          fontSize: "var(--font-size-sm)",
        }}
      />
    </div>
  );

  if (!project) {
    return (
      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Loading project...</div>
    );
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
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h4 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600 }}>
          Dial {dialIndex + 1}
        </h4>
        <button onClick={onClose} style={{ color: "var(--text-muted)", cursor: "pointer" }}>
          <X size={14} />
        </button>
      </div>

      {/* Try it: real input through the same path as the hardware */}
      {onSimulate && (
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
          <span style={{ fontSize: 11, color: "var(--text-muted)", flex: 1 }}>Try it</span>
          <button
            onClick={() => onSimulate({ type: "dial_turn", index: dialIndex, amount: -1 })}
            title="Turn counter-clockwise"
            style={dialTestBtnStyle}
          >
            &#8634;
          </button>
          <button
            onClick={() => onSimulate({ type: "dial_push", index: dialIndex })}
            title="Press the dial"
            style={dialTestBtnStyle}
          >
            <Play size={11} />
          </button>
          <button
            onClick={() => {
              onSimulate({ type: "dial_push", index: dialIndex, pressed: true });
              setTimeout(
                () => onSimulate({ type: "dial_push", index: dialIndex, pressed: false }),
                700
              );
            }}
            title="Long-press the dial"
            style={dialTestBtnStyle}
          >
            Long
          </button>
          <button
            onClick={() => onSimulate({ type: "dial_turn", index: dialIndex, amount: 1 })}
            title="Turn clockwise"
            style={dialTestBtnStyle}
          >
            &#8635;
          </button>
        </div>
      )}

      {/* Label */}
      <div>
        <label style={panelLabelStyle}>Label</label>
        <input
          type="text"
          value={dial?.label ?? ""}
          placeholder="Shown on the touchscreen"
          onChange={(e) => onUpdate({ label: e.target.value || undefined })}
          style={{
            width: "100%", padding: "var(--space-sm) var(--space-md)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            background: "var(--bg-surface)", color: "var(--text-primary)",
            fontSize: "var(--font-size-sm)",
          }}
        />
      </div>

      {/* Readout — what the strip shows under this dial */}
      <div>
        <label style={panelLabelStyle}>Readout</label>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
          <div style={{ display: "flex", gap: "var(--space-sm)" }}>
            <div style={{ flex: 1 }}>
              <label style={panelHintStyle}>Icon (optional)</label>
              <IconPicker
                value={dial?.icon ?? ""}
                onChange={(icon) => onUpdate({ icon: icon || undefined })}
              />
            </div>
            <div style={{ width: 80 }}>
              <label style={panelHintStyle}>Unit</label>
              <input
                type="text"
                value={dial?.unit ?? ""}
                placeholder="dB, %"
                onChange={(e) => onUpdate({ unit: e.target.value || undefined })}
                style={fieldInputStyle}
              />
            </div>
          </div>
          <MeterFields
            meter={dial?.meter}
            bounds={dial?.adjust}
            allowAuto
            onChange={(meter) => onUpdate({ meter })}
          />
        </div>
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
          The strip under this dial shows the label, icon, live value, and
          level bar.
        </div>
      </div>

      {/* Adjust-a-value */}
      <div>
        <label style={panelLabelStyle}>Turning Adjusts a Value</label>
        <VariableKeyPicker
          value={adjust.key ?? ""}
          onChange={(key) => updateAdjust({ key })}
          placeholder="Pick a variable to adjust..."
        />
        {adjust.key && (
          <div style={{ display: "flex", gap: "var(--space-sm)", marginTop: "var(--space-sm)" }}>
            {numberField("Step", "step", "1")}
            {numberField("Min", "min", "none")}
            {numberField("Max", "max", "none")}
          </div>
        )}
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
          Each detent adds or subtracts the step, clamped to min/max. Use a
          variable, then have a macro or trigger watch it to drive a device.
          The live value shows on the touchscreen under this dial.
        </div>
      </div>

      {/* Push-and-turn: fine adjust while the dial is held */}
      <div>
        <label style={panelLabelStyle}>Push + Turn Adjusts (Fine)</label>
        <VariableKeyPicker
          value={pressedAdjust.key ?? ""}
          onChange={(key) => updatePressedAdjust({ key })}
          placeholder="Pick a variable for fine adjust..."
        />
        {pressedAdjust.key && (
          <div style={{ display: "flex", gap: "var(--space-sm)", marginTop: "var(--space-sm)" }}>
            {(["step", "min", "max"] as const).map((field) => (
              <div key={field} style={{ flex: 1 }}>
                <label style={panelHintStyle}>
                  {field[0].toUpperCase() + field.slice(1)}
                </label>
                <input
                  type="number"
                  value={pressedAdjust[field] ?? ""}
                  placeholder={field === "step" ? "1" : "none"}
                  onChange={(e) =>
                    updatePressedAdjust({
                      [field]: e.target.value === "" ? undefined : Number(e.target.value),
                    })
                  }
                  style={fieldInputStyle}
                />
              </div>
            ))}
          </div>
        )}
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
          Turning while the dial is held uses this instead — a smaller step
          for fine trim. A push that turned never fires the press actions.
        </div>
      </div>

      {/* Turn / press actions */}
      <div>
        <label style={panelLabelStyle}>Clockwise Turn Actions</label>
        <ActionListEditor
          actions={dial?.cw ?? []}
          onChange={(cw) => onUpdate({ cw: cw.length ? cw : undefined })}
          project={project}
          allowedActions={allowedActions}
          navigateOptions={navigateOptions}
          addLabel="Add clockwise action"
        />
      </div>
      <div>
        <label style={panelLabelStyle}>Counter-Clockwise Turn Actions</label>
        <ActionListEditor
          actions={dial?.ccw ?? []}
          onChange={(ccw) => onUpdate({ ccw: ccw.length ? ccw : undefined })}
          project={project}
          allowedActions={allowedActions}
          navigateOptions={navigateOptions}
          addLabel="Add counter-clockwise action"
        />
      </div>
      <div>
        <label style={panelLabelStyle}>Press Actions</label>
        <ActionListEditor
          actions={dial?.press ?? []}
          onChange={(press) => onUpdate({ press: press.length ? press : undefined })}
          project={project}
          allowedActions={allowedActions}
          navigateOptions={navigateOptions}
          addLabel="Add press action"
        />
      </div>
      <div>
        <label style={panelLabelStyle}>Long-Press Actions</label>
        <ActionListEditor
          actions={dial?.long_press ?? []}
          onChange={(long_press) =>
            onUpdate({ long_press: long_press.length ? long_press : undefined })
          }
          project={project}
          allowedActions={allowedActions}
          navigateOptions={navigateOptions}
          addLabel="Add long-press action"
        />
        {(dial?.long_press?.length ?? 0) > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", marginTop: "var(--space-xs)" }}>
            <span style={panelHintStyle}>Hold threshold (ms)</span>
            <input
              type="number"
              value={dial?.hold_threshold_ms ?? ""}
              placeholder="500"
              onChange={(e) =>
                onUpdate({
                  hold_threshold_ms:
                    e.target.value === "" ? undefined : Number(e.target.value),
                })
              }
              style={{ ...fieldInputStyle, width: 90 }}
            />
          </div>
        )}
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
          With a long-press set, a quick push fires Press on release; holding
          past the threshold fires this instead.
        </div>
      </div>

      {/* Touch — the dial's strip zone is its touch surface */}
      <div>
        <label style={panelLabelStyle}>Touch (the readout on the strip)</label>
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: "var(--space-xs)" }}>
          Tapping this dial's readout presses the dial; long-tapping runs the
          long-press. Override either below.
        </div>
        <label style={panelHintStyle}>Tap actions (override)</label>
        <ActionListEditor
          actions={dial?.touch ?? []}
          onChange={(touch) => onUpdate({ touch: touch.length ? touch : undefined })}
          project={project}
          allowedActions={allowedActions}
          navigateOptions={navigateOptions}
          addLabel="Add tap action"
        />
        <label style={{ ...panelHintStyle, marginTop: "var(--space-xs)", display: "block" }}>
          Long-tap actions (override)
        </label>
        <ActionListEditor
          actions={dial?.long_touch ?? []}
          onChange={(long_touch) =>
            onUpdate({ long_touch: long_touch.length ? long_touch : undefined })
          }
          project={project}
          allowedActions={allowedActions}
          navigateOptions={navigateOptions}
          addLabel="Add long-tap action"
        />
        <label
          style={{
            display: "flex", alignItems: "center", gap: "var(--space-sm)",
            fontSize: "var(--font-size-sm)", cursor: "pointer",
            color: "var(--text-primary)", marginTop: "var(--space-sm)",
          }}
        >
          <input
            type="checkbox"
            checked={!!dial?.fader}
            onChange={(e) => onUpdate({ fader: e.target.checked || undefined })}
            disabled={adjust.min === undefined || adjust.max === undefined}
            style={{ accentColor: "var(--accent)" }}
          />
          Touch fader
        </label>
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
          {adjust.min === undefined || adjust.max === undefined
            ? "Set Min and Max on the adjust to enable: touching the readout will jump straight to that position."
            : "Touching the readout sets the value to the touched position (replaces the tap-presses-the-dial default)."}
        </div>
        {onOpenStrip && (
          <button
            onClick={onOpenStrip}
            style={{
              marginTop: "var(--space-sm)", fontSize: 11,
              color: "var(--accent)", background: "transparent",
              cursor: "pointer", padding: 0, textAlign: "left",
            }}
          >
            Customize the whole strip…
          </button>
        )}
      </div>

      {/* Clear */}
      <button
        onClick={onClear}
        style={{
          display: "flex", alignItems: "center", justifyContent: "center",
          gap: "var(--space-xs)", padding: "var(--space-sm)",
          borderRadius: "var(--border-radius)", background: "transparent",
          border: "1px solid var(--border-color)", color: "var(--color-error)",
          fontSize: "var(--font-size-sm)", cursor: "pointer",
        }}
      >
        <Trash2 size={12} />
        Clear Assignment
      </button>
    </div>
  );
}

// ──── Touchscreen Zones Editor ────

const fieldInputStyle: React.CSSProperties = {
  width: "100%", padding: "4px 6px",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-surface)", color: "var(--text-primary)",
  fontSize: "var(--font-size-sm)",
};

// Build the zones the runtime generates for the current dials — the
// "start from the current zones" seed when taking over the strip.
function defaultZonesFromDials(
  dials: DialAssignment[],
  dialCount: number
): TouchZone[] {
  return Array.from({ length: dialCount }, (_, i) => {
    const dial = dials.find((d) => d.index === i);
    const adjust = dial?.adjust?.key ? { ...dial.adjust } : undefined;
    if (adjust && dial?.fader) adjust.fader = true;
    return {
      label: dial?.label || undefined,
      icon: dial?.icon || undefined,
      unit: dial?.unit || undefined,
      meter: dial?.meter,
      value_source: dial?.adjust?.key || undefined,
      touch: dial?.touch ?? dial?.press,
      long_touch: dial?.long_touch ?? dial?.long_press,
      drag_adjust: adjust,
    } as TouchZone;
  });
}

// Meter (level bar) fields. Zones and dial readouts auto-enable when their
// adjust declares min+max (mirrors the runtime), so they get a tri-state;
// keys are plain on/off.
function MeterFields({
  meter,
  bounds,
  onChange,
  allowAuto = false,
}: {
  meter: MeterConfig | boolean | undefined;
  bounds?: DialAdjust;
  onChange: (meter: MeterConfig | boolean | undefined) => void;
  allowAuto?: boolean;
}) {
  const autoAvailable =
    allowAuto && bounds?.min !== undefined && bounds?.max !== undefined;
  const mode =
    meter === false || (meter === undefined && !allowAuto)
      ? "off"
      : meter === undefined
        ? "auto"
        : "on";
  const cfg: MeterConfig = typeof meter === "object" && meter !== null ? meter : {};
  const update = (patch: Partial<MeterConfig>) => {
    const next = { ...cfg, ...patch };
    (Object.keys(next) as (keyof MeterConfig)[]).forEach((k) => {
      if (next[k] === undefined) delete next[k];
    });
    onChange(next);
  };
  const thresholds = cfg.thresholds ?? [];

  return (
    <div>
      <label style={panelHintStyle}>Level bar (meter)</label>
      <select
        value={mode}
        onChange={(e) => {
          const v = e.target.value;
          onChange(v === "on" ? {} : v === "off" ? (allowAuto ? false : undefined) : undefined);
        }}
        style={fieldInputStyle}
      >
        {allowAuto && (
          <option value="auto">
            {autoAvailable ? "Automatic (from the adjust range)" : "Automatic (needs an adjust range)"}
          </option>
        )}
        <option value="on">On</option>
        <option value="off">Off</option>
      </select>
      {mode === "on" && (
        <div style={{ marginTop: "var(--space-xs)", display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
          <div style={{ display: "flex", gap: "var(--space-sm)" }}>
            {(["min", "max"] as const).map((field) => (
              <div key={field} style={{ flex: 1 }}>
                <label style={panelHintStyle}>{field === "min" ? "Min" : "Max"}</label>
                <input
                  type="number"
                  value={cfg[field] ?? ""}
                  placeholder={field === "min" ? "0" : "100"}
                  onChange={(e) =>
                    update({ [field]: e.target.value === "" ? undefined : Number(e.target.value) })
                  }
                  style={fieldInputStyle}
                />
              </div>
            ))}
            <div>
              <label style={panelHintStyle}>Color</label>
              <InlineColorPicker
                value={cfg.color ?? ""}
                onChange={(c) => update({ color: c || undefined })}
              />
            </div>
          </div>
          {thresholds.map((rule, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
              <span style={panelHintStyle}>Above</span>
              <input
                type="number"
                value={rule.above ?? ""}
                onChange={(e) =>
                  update({
                    thresholds: thresholds.map((r, j) =>
                      j === i
                        ? { ...r, above: e.target.value === "" ? undefined : Number(e.target.value) }
                        : r
                    ),
                  })
                }
                style={{ ...fieldInputStyle, width: 70 }}
              />
              <InlineColorPicker
                value={rule.color ?? ""}
                onChange={(c) =>
                  update({
                    thresholds: thresholds.map((r, j) =>
                      j === i ? { ...r, color: c || undefined } : r
                    ),
                  })
                }
              />
              <button
                onClick={() =>
                  update({
                    thresholds: thresholds.filter((_, j) => j !== i).length
                      ? thresholds.filter((_, j) => j !== i)
                      : undefined,
                  })
                }
                title="Remove this color rule"
                style={{ color: "var(--text-muted)", cursor: "pointer" }}
              >
                <X size={12} />
              </button>
            </div>
          ))}
          {thresholds.length < 3 && (
            <button
              onClick={() => update({ thresholds: [...thresholds, {}] })}
              style={{
                alignSelf: "flex-start", fontSize: 11, color: "var(--text-muted)",
                border: "1px dashed var(--border-color)", borderRadius: "var(--border-radius)",
                padding: "2px 8px", background: "transparent", cursor: "pointer",
              }}
            >
              + Color above a level
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// Simple conditional styling for zones / info items (the runtime accepts the
// full key-feedback schema; this edits the common active/inactive pair).
function ZoneFeedbackFields({
  feedback,
  onChange,
}: {
  feedback: DisplayFeedback | undefined;
  onChange: (fb: DisplayFeedback | undefined) => void;
}) {
  const fb = feedback ?? {};
  const update = (patch: Partial<DisplayFeedback>) => {
    const next = { ...fb, ...patch };
    if (!next.key) {
      onChange(undefined);
    } else {
      onChange(next);
    }
  };
  const colorPair = (
    label: string,
    styleKey: "style_active" | "style_inactive"
  ) => (
    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
      <span style={panelHintStyle}>{label}</span>
      <InlineColorPicker
        value={fb[styleKey]?.bg_color ?? ""}
        onChange={(c) =>
          update({ [styleKey]: { ...(fb[styleKey] ?? {}), bg_color: c || undefined } })
        }
      />
      <InlineColorPicker
        value={fb[styleKey]?.text_color ?? ""}
        onChange={(c) =>
          update({ [styleKey]: { ...(fb[styleKey] ?? {}), text_color: c || undefined } })
        }
      />
    </div>
  );
  return (
    <div>
      <label style={panelHintStyle}>Colors from state (optional)</label>
      <VariableKeyPicker
        value={fb.key ?? ""}
        onChange={(key) => update({ key: key || undefined })}
        placeholder="Watch a state key..."
      />
      {fb.key && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", marginTop: "var(--space-xs)" }}>
            <span style={{ ...panelHintStyle, whiteSpace: "nowrap" }}>Active when equals</span>
            <input
              type="text"
              value={fb.condition?.equals ?? ""}
              placeholder="any truthy value"
              onChange={(e) =>
                update({
                  condition: e.target.value === "" ? undefined : { equals: e.target.value },
                })
              }
              style={{ ...fieldInputStyle, flex: 1 }}
            />
          </div>
          <div style={{ display: "flex", gap: "var(--space-md)", marginTop: "var(--space-xs)", flexWrap: "wrap" }}>
            {colorPair("Active", "style_active")}
            {colorPair("Inactive", "style_inactive")}
          </div>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
            Each pair is background then text.
          </div>
        </>
      )}
    </div>
  );
}

function TouchscreenZonesEditor({
  config,
  onConfigChange,
  allowedActions,
  navigateOptions,
  initialExpanded = null,
  dials = [],
  dialCount = 0,
  onSimulate,
}: {
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
  allowedActions?: string[];
  navigateOptions?: { value: string; label: string }[];
  // The workbench canvas opens the editor on the zone that was clicked.
  initialExpanded?: number | null;
  // For seeding custom zones from the current per-dial readouts.
  dials?: DialAssignment[];
  dialCount?: number;
  // Fire real touch input (simulate_input) to test a zone.
  onSimulate?: (payload: Record<string, unknown>) => void;
}) {
  const project = useProjectStore((s) => s.project);
  const touchscreen =
    (config.touchscreen as { zones?: TouchZone[]; idle?: string } | undefined) ?? {};
  const zones = touchscreen.zones ?? [];
  const [expandedZone, setExpandedZone] = useState<number | null>(initialExpanded);

  const setZones = (next: TouchZone[]) => {
    onConfigChange({
      ...config,
      touchscreen: { ...touchscreen, zones: next },
    });
  };
  const updateZone = (i: number, patch: Partial<TouchZone>) =>
    setZones(zones.map((z, j) => (j === i ? { ...z, ...patch } : z)));
  const removeZone = (i: number) => {
    setZones(zones.filter((_, j) => j !== i));
    setExpandedZone(null);
  };
  // Center x of a zone in strip pixels, for the test buttons.
  const zoneCenter = (i: number) => {
    const slot = 800 / Math.max(1, zones.length);
    const z = zones[i] ?? {};
    const x = typeof z.x === "number" ? z.x : i * slot;
    const w = typeof z.w === "number" ? z.w : slot;
    return Math.round(x + w / 2);
  };

  if (!project) return null;

  if (zones.length === 0) {
    const anyDialConfigured = dials.some(
      (d) => d.label || d.icon || d.adjust?.key || d.press?.length || d.cw?.length
    );
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
          {anyDialConfigured
            ? "The strip is showing one readout per dial (label, live value, meter). Tapping a readout presses its dial. Take over the strip with custom zones when you want your own layout — meters, status panels, wider faders."
            : dialCount > 0
              ? "Nothing is set up yet, so the strip shows a clock. Configure a dial (click one in the picture) to get a live readout here, or take over the strip with custom zones."
              : "Add zones to put live values, meters, and touch actions on the strip."}
        </div>
        <div style={{ display: "flex", gap: "var(--space-sm)", flexWrap: "wrap" }}>
          {dialCount > 0 && (
            <button
              onClick={() => {
                setZones(defaultZonesFromDials(dials, dialCount));
                setExpandedZone(0);
              }}
              style={{
                padding: "5px 10px", borderRadius: "var(--border-radius)",
                background: "var(--accent-bg)", color: "white",
                fontSize: 12, cursor: "pointer",
              }}
              title="Copy the current per-dial readouts into editable zones"
            >
              Customize zones — start from the current ones
            </button>
          )}
          <button
            onClick={() => {
              setZones([{}]);
              setExpandedZone(0);
            }}
            style={{
              padding: "5px 10px", borderRadius: "var(--border-radius)",
              border: "1px dashed var(--border-color)", background: "transparent",
              color: "var(--text-muted)", fontSize: 12, cursor: "pointer",
            }}
          >
            Start empty
          </button>
        </div>
        <div>
          <label style={panelHintStyle}>When nothing is configured</label>
          <select
            value={touchscreen.idle === "blank" ? "blank" : "clock"}
            onChange={(e) =>
              onConfigChange({
                ...config,
                touchscreen: {
                  ...touchscreen,
                  idle: e.target.value === "blank" ? "blank" : undefined,
                },
              })
            }
            style={fieldInputStyle}
          >
            <option value="clock">Show a clock</option>
            <option value="blank">Stay blank</option>
          </select>
        </div>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 560 }}>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
        Custom zones own the whole strip (the per-dial readouts are replaced).
        Zones split it evenly unless given pixel bounds; tapping a zone runs
        its actions.
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
        {zones.map((zone, i) => {
          const isExpanded = expandedZone === i;
          return (
            <div
              key={i}
              style={{
                border: "1px solid var(--border-color)",
                borderRadius: "var(--border-radius)",
                overflow: "hidden",
              }}
            >
              <button
                onClick={() => setExpandedZone(isExpanded ? null : i)}
                style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  width: "100%", padding: "6px 10px", fontSize: "var(--font-size-sm)",
                  background: "var(--bg-surface)", textAlign: "left", cursor: "pointer",
                }}
              >
                <span style={{ fontWeight: 500 }}>
                  Zone {i + 1}{zone.label ? ` — ${zone.label}` : ""}
                </span>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  {zone.value_source || "no value"}
                </span>
              </button>
              {isExpanded && (
                <div style={{
                  padding: "var(--space-sm)",
                  borderTop: "1px solid var(--border-color)",
                  display: "flex", flexDirection: "column", gap: "var(--space-sm)",
                }}>
                  {onSimulate && (
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                      <span style={{ fontSize: 11, color: "var(--text-muted)", flex: 1 }}>Try it</span>
                      <button
                        onClick={() => onSimulate({ type: "touch", x: zoneCenter(i) })}
                        title="Tap this zone for real"
                        style={dialTestBtnStyle}
                      >
                        Tap
                      </button>
                      <button
                        onClick={() =>
                          onSimulate({ type: "touch", x: zoneCenter(i), touch_type: "long" })
                        }
                        title="Long-press this zone for real"
                        style={dialTestBtnStyle}
                      >
                        Long
                      </button>
                      <button
                        onClick={() =>
                          onSimulate({
                            type: "touch", x: zoneCenter(i) - 40,
                            x_out: zoneCenter(i) + 40, touch_type: "drag",
                          })
                        }
                        title="Swipe right across this zone"
                        style={dialTestBtnStyle}
                      >
                        Swipe →
                      </button>
                      <button
                        onClick={() =>
                          onSimulate({
                            type: "touch", x: zoneCenter(i) + 40,
                            x_out: zoneCenter(i) - 40, touch_type: "drag",
                          })
                        }
                        title="Swipe left across this zone"
                        style={dialTestBtnStyle}
                      >
                        ← Swipe
                      </button>
                    </div>
                  )}
                  <div>
                    <label style={panelHintStyle}>Label</label>
                    <input
                      type="text"
                      value={zone.label ?? ""}
                      onChange={(e) => updateZone(i, { label: e.target.value || undefined })}
                      placeholder="Text shown in the zone"
                      style={{
                        width: "100%", padding: "4px 6px",
                        borderRadius: "var(--border-radius)",
                        border: "1px solid var(--border-color)",
                        background: "var(--bg-surface)", color: "var(--text-primary)",
                        fontSize: "var(--font-size-sm)",
                      }}
                    />
                  </div>
                  <div>
                    <label style={panelHintStyle}>Label from state (optional, overrides Label)</label>
                    <VariableKeyPicker
                      value={zone.label_source ?? ""}
                      onChange={(key) => updateZone(i, { label_source: key || undefined })}
                      placeholder="Pick a state key for the label..."
                    />
                  </div>
                  <div>
                    <label style={panelHintStyle}>Show value from state</label>
                    <VariableKeyPicker
                      value={zone.value_source ?? ""}
                      onChange={(key) => updateZone(i, { value_source: key || undefined })}
                      placeholder="Pick a state key to display..."
                    />
                  </div>
                  <div style={{ display: "flex", gap: "var(--space-sm)" }}>
                    <div style={{ flex: 1 }}>
                      <label style={panelHintStyle}>Icon (optional)</label>
                      <IconPicker
                        value={zone.icon ?? ""}
                        onChange={(icon) => updateZone(i, { icon: icon || undefined })}
                      />
                    </div>
                    <div style={{ width: 80 }}>
                      <label style={panelHintStyle}>Unit</label>
                      <input
                        type="text"
                        value={zone.unit ?? ""}
                        placeholder="dB, %"
                        onChange={(e) => updateZone(i, { unit: e.target.value || undefined })}
                        style={fieldInputStyle}
                      />
                    </div>
                  </div>
                  <MeterFields
                    meter={zone.meter}
                    bounds={zone.drag_adjust}
                    allowAuto
                    onChange={(meter) => updateZone(i, { meter })}
                  />
                  <ZoneFeedbackFields
                    feedback={zone.feedback}
                    onChange={(feedback) => updateZone(i, { feedback })}
                  />
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                      <span style={panelHintStyle}>Background</span>
                      <InlineColorPicker
                        value={zone.bg_color ?? ""}
                        onChange={(c) => updateZone(i, { bg_color: c || undefined })}
                      />
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                      <span style={panelHintStyle}>Text</span>
                      <InlineColorPicker
                        value={zone.text_color ?? ""}
                        onChange={(c) => updateZone(i, { text_color: c || undefined })}
                      />
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: "var(--space-sm)" }}>
                    {([["Position (px, optional)", "x"], ["Width (px, optional)", "w"]] as const).map(
                      ([fieldLabel, field]) => (
                        <div key={field} style={{ flex: 1 }}>
                          <label style={panelHintStyle}>{fieldLabel}</label>
                          <input
                            type="number"
                            value={zone[field] ?? ""}
                            placeholder="auto"
                            onChange={(e) =>
                              updateZone(i, {
                                [field]: e.target.value === "" ? undefined : Number(e.target.value),
                              })
                            }
                            style={{
                              width: "100%", padding: "4px 6px",
                              borderRadius: "var(--border-radius)",
                              border: "1px solid var(--border-color)",
                              background: "var(--bg-surface)", color: "var(--text-primary)",
                              fontSize: "var(--font-size-sm)",
                            }}
                          />
                        </div>
                      )
                    )}
                  </div>
                  <div>
                    <label style={panelHintStyle}>Tap actions</label>
                    <ActionListEditor
                      actions={zone.touch ?? []}
                      onChange={(touch) => updateZone(i, { touch: touch.length ? touch : undefined })}
                      project={project}
                      allowedActions={allowedActions}
                      navigateOptions={navigateOptions}
                      addLabel="Add tap action"
                    />
                  </div>
                  <div>
                    <label style={panelHintStyle}>Long-press actions (optional — falls back to tap)</label>
                    <ActionListEditor
                      actions={zone.long_touch ?? []}
                      onChange={(long_touch) =>
                        updateZone(i, { long_touch: long_touch.length ? long_touch : undefined })
                      }
                      project={project}
                      allowedActions={allowedActions}
                      navigateOptions={navigateOptions}
                      addLabel="Add long-press action"
                    />
                  </div>
                  <div>
                    <label style={panelHintStyle}>Swipe adjusts a value (optional)</label>
                    <VariableKeyPicker
                      value={zone.drag_adjust?.key ?? ""}
                      onChange={(key) =>
                        updateZone(i, {
                          drag_adjust: key ? { ...(zone.drag_adjust ?? {}), key } : undefined,
                        })
                      }
                      placeholder="Pick a variable to adjust by swiping..."
                    />
                    {zone.drag_adjust?.key && (
                      <div style={{ display: "flex", gap: "var(--space-sm)", marginTop: "var(--space-xs)" }}>
                        {(["step", "min", "max"] as const).map((field) => (
                          <div key={field} style={{ flex: 1 }}>
                            <label style={panelHintStyle}>
                              {field[0].toUpperCase() + field.slice(1)}
                            </label>
                            <input
                              type="number"
                              value={zone.drag_adjust?.[field] ?? ""}
                              placeholder={field === "step" ? "1" : "none"}
                              onChange={(e) =>
                                updateZone(i, {
                                  drag_adjust: {
                                    ...(zone.drag_adjust ?? {}),
                                    [field]: e.target.value === "" ? undefined : Number(e.target.value),
                                  },
                                })
                              }
                              style={{
                                width: "100%", padding: "4px 6px",
                                borderRadius: "var(--border-radius)",
                                border: "1px solid var(--border-color)",
                                background: "var(--bg-surface)", color: "var(--text-primary)",
                                fontSize: "var(--font-size-sm)",
                              }}
                            />
                          </div>
                        ))}
                      </div>
                    )}
                    {zone.drag_adjust?.key && (
                      <div style={{ marginTop: "var(--space-xs)" }}>
                        <label
                          style={{
                            display: "flex", alignItems: "center", gap: "var(--space-sm)",
                            fontSize: "var(--font-size-sm)", cursor: "pointer",
                            color: "var(--text-primary)",
                          }}
                        >
                          <input
                            type="checkbox"
                            checked={!!zone.drag_adjust?.fader}
                            onChange={(e) =>
                              updateZone(i, {
                                drag_adjust: {
                                  ...(zone.drag_adjust ?? {}),
                                  fader: e.target.checked || undefined,
                                },
                              })
                            }
                            disabled={
                              zone.drag_adjust?.min === undefined ||
                              zone.drag_adjust?.max === undefined
                            }
                            style={{ accentColor: "var(--accent)" }}
                          />
                          Touch fader
                        </label>
                        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
                          {zone.drag_adjust?.min === undefined || zone.drag_adjust?.max === undefined
                            ? "Set Min and Max to enable: taps and swipes will jump straight to the touched position."
                            : "Taps and swipes set the value to the touched position (replaces the tap actions for this zone)."}
                        </div>
                      </div>
                    )}
                  </div>
                  <button
                    onClick={() => removeZone(i)}
                    style={{
                      display: "flex", alignItems: "center", justifyContent: "center",
                      gap: "var(--space-xs)", padding: "var(--space-xs)",
                      borderRadius: "var(--border-radius)", background: "transparent",
                      border: "1px solid var(--border-color)", color: "var(--color-error)",
                      fontSize: "var(--font-size-sm)", cursor: "pointer",
                    }}
                  >
                    <Trash2 size={12} />
                    Remove Zone
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <button
        onClick={() => {
          setZones([...zones, {}]);
          setExpandedZone(zones.length);
        }}
        style={{
          marginTop: "var(--space-sm)",
          display: "flex", alignItems: "center", justifyContent: "center", gap: 4,
          padding: "5px 10px", borderRadius: "var(--border-radius)",
          border: "1px dashed var(--border-color)", background: "transparent",
          color: "var(--text-muted)", fontSize: 12, cursor: "pointer",
        }}
      >
        + Add custom zone
      </button>
    </div>
  );
}

// ──── Info Strip Editor (secondary info screen) ────

interface InfoItem {
  label?: string;
  source?: string;
  key?: string;
  text?: string;
  icon?: string;
  unit?: string;
  meter?: MeterConfig | boolean;
  feedback?: DisplayFeedback;
  items?: InfoItem[];
}

// One info-screen display element: heading + live value (or static text)
// + icon/unit/meter/feedback.
function InfoItemFields({
  item,
  onChange,
  showTextMode = true,
}: {
  item: InfoItem;
  onChange: (item: InfoItem) => void;
  showTextMode?: boolean;
}) {
  const update = (patch: Partial<InfoItem>) => onChange({ ...item, ...patch });
  const isText = item.source === "text";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      {showTextMode && (
        <div>
          <label style={panelHintStyle}>Shows</label>
          <select
            value={isText ? "text" : "state"}
            onChange={(e) =>
              update(
                e.target.value === "text"
                  ? { source: "text" }
                  : { source: "state" }
              )
            }
            style={fieldInputStyle}
          >
            <option value="state">A live state value</option>
            <option value="text">Static text</option>
          </select>
        </div>
      )}
      {isText ? (
        <div>
          <label style={panelHintStyle}>Text</label>
          <input
            type="text"
            value={item.text ?? ""}
            onChange={(e) => update({ text: e.target.value })}
            placeholder="Text shown on the screen"
            style={fieldInputStyle}
          />
        </div>
      ) : (
        <div>
          <label style={panelHintStyle}>State key</label>
          <VariableKeyPicker
            value={item.key ?? ""}
            onChange={(key) => update({ key })}
            placeholder="Pick a state key to display..."
          />
        </div>
      )}
      <div>
        <label style={panelHintStyle}>Heading (optional, shown above)</label>
        <input
          type="text"
          value={item.label ?? ""}
          onChange={(e) => update({ label: e.target.value || undefined })}
          placeholder="e.g. Room Temp"
          style={fieldInputStyle}
        />
      </div>
      <div style={{ display: "flex", gap: "var(--space-sm)" }}>
        <div style={{ flex: 1 }}>
          <label style={panelHintStyle}>Icon (optional)</label>
          <IconPicker
            value={item.icon ?? ""}
            onChange={(icon) => update({ icon: icon || undefined })}
          />
        </div>
        <div style={{ width: 80 }}>
          <label style={panelHintStyle}>Unit</label>
          <input
            type="text"
            value={item.unit ?? ""}
            placeholder="dB, %"
            onChange={(e) => update({ unit: e.target.value || undefined })}
            style={fieldInputStyle}
          />
        </div>
      </div>
      {!isText && (
        <>
          <MeterFields
            meter={item.meter}
            onChange={(meter) => update({ meter })}
          />
          <ZoneFeedbackFields
            feedback={item.feedback}
            onChange={(feedback) => update({ feedback })}
          />
        </>
      )}
    </div>
  );
}

function InfoStripEditor({
  config,
  onConfigChange,
}: {
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
}) {
  const infoStrip = (config.info_strip as InfoItem | undefined) ?? undefined;

  // Mode mirrors the runtime: no config (or source "clock") = clock.
  const mode = !infoStrip
    ? "clock"
    : infoStrip.source === "blank"
      ? "blank"
      : infoStrip.source === "clock"
        ? "clock"
        : Array.isArray(infoStrip.items) && infoStrip.items.length > 0
          ? "items"
          : infoStrip.source === "text"
            ? "text"
            : infoStrip.key || infoStrip.label || infoStrip.icon
              ? "state"
              : "clock";

  const setInfoStrip = (value: InfoItem | undefined) => {
    if (value === undefined) {
      const { info_strip: _drop, ...rest } = config;
      onConfigChange(rest);
    } else {
      onConfigChange({ ...config, info_strip: value });
    }
  };

  const items = infoStrip?.items ?? [];

  return (
    <div style={{ maxWidth: 560 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)", maxWidth: 320 }}>
        <div>
          <label style={panelHintStyle}>Show</label>
          <select
            value={mode}
            onChange={(e) => {
              const next = e.target.value;
              if (next === "clock") setInfoStrip(undefined);
              else if (next === "blank") setInfoStrip({ source: "blank" });
              else if (next === "text") setInfoStrip({ source: "text", text: infoStrip?.text ?? "" });
              else if (next === "items") setInfoStrip({ items: [{}, {}] });
              else setInfoStrip({ source: "state", key: infoStrip?.key ?? "" });
            }}
            style={fieldInputStyle}
          >
            <option value="clock">A clock (default)</option>
            <option value="state">A live state value</option>
            <option value="text">Static text</option>
            <option value="items">Two items side by side</option>
            <option value="blank">Nothing (blank)</option>
          </select>
        </div>

        {(mode === "state" || mode === "text") && infoStrip && (
          <InfoItemFields
            item={infoStrip}
            onChange={(item) => setInfoStrip({ ...item })}
            showTextMode={false}
          />
        )}

        {mode === "items" &&
          [0, 1].map((i) => (
            <div
              key={i}
              style={{
                border: "1px solid var(--border-color)",
                borderRadius: "var(--border-radius)",
                padding: "var(--space-sm)",
              }}
            >
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", marginBottom: "var(--space-xs)" }}>
                {i === 0 ? "Left item" : "Right item"}
              </div>
              <InfoItemFields
                item={items[i] ?? {}}
                onChange={(item) => {
                  const next = [items[0] ?? {}, items[1] ?? {}];
                  next[i] = item;
                  setInfoStrip({ ...(infoStrip ?? {}), items: next });
                }}
              />
            </div>
          ))}
      </div>
    </div>
  );
}

// ──── Page Tabs ────

function PageTabs({
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

// ──── Auto-Page Rules ────

interface AutoPageRule {
  page?: number;
  when?: ConditionGroup;
}

function AutoPageEditor({
  layout,
  config,
  onConfigChange,
}: {
  layout: SurfaceLayout;
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
}) {
  const rules = (config.auto_page as AutoPageRule[] | undefined) ?? [];
  const maxPages = layout.max_pages ?? 10;
  const pageNames = (config.page_names as Record<string, string> | undefined) ?? {};

  const setRules = (next: AutoPageRule[]) => {
    onConfigChange({ ...config, auto_page: next });
  };
  const updateRule = (i: number, patch: Partial<AutoPageRule>) =>
    setRules(rules.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const removeRule = (i: number) => setRules(rules.filter((_, j) => j !== i));
  const moveRule = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= rules.length) return;
    const next = [...rules];
    [next[i], next[j]] = [next[j], next[i]];
    setRules(next);
  };

  const reorderBtn: React.CSSProperties = {
    padding: "2px 5px", borderRadius: "var(--border-radius)", fontSize: 9,
    color: "var(--text-muted)", background: "transparent",
    border: "1px solid var(--border-color)", cursor: "pointer", lineHeight: 1,
  };

  return (
    <div style={{ maxWidth: 560 }}>
      {rules.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
          No rules yet. Example: switch to an "Off Hours" page when the room
          powers down.
        </div>
      ) : (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
          Rules are checked in order; the first match wins.
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
        {rules.map((rule, i) => (
          <div
            key={i}
            style={{
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              padding: "var(--space-sm)",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-xs)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
              <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>Switch to</span>
              <select
                value={rule.page ?? 0}
                onChange={(e) => updateRule(i, { page: Number(e.target.value) })}
                style={{
                  padding: "4px 8px",
                  borderRadius: "var(--border-radius)",
                  border: "1px solid var(--border-color)",
                  background: "var(--bg-primary)",
                  color: "var(--text-primary)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                {Array.from({ length: maxPages }, (_, p) => (
                  <option key={p} value={p}>{pageNames[String(p)] || `Page ${p + 1}`}</option>
                ))}
              </select>
              <div style={{ marginLeft: "auto", display: "flex", gap: 4, alignItems: "center" }}>
                {i > 0 && (
                  <button onClick={() => moveRule(i, -1)} title="Move up" style={reorderBtn}>&#9650;</button>
                )}
                {i < rules.length - 1 && (
                  <button onClick={() => moveRule(i, 1)} title="Move down" style={reorderBtn}>&#9660;</button>
                )}
                <button
                  onClick={() => removeRule(i)}
                  title="Remove rule"
                  style={{
                    padding: "2px 6px", borderRadius: "var(--border-radius)", fontSize: 11,
                    color: "var(--color-error)", background: "transparent",
                    border: "1px solid var(--border-color)", cursor: "pointer",
                  }}
                >
                  &times;
                </button>
              </div>
            </div>
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>when</span>
            <ConditionGroupEditor
              value={rule.when}
              onChange={(when) => updateRule(i, { when })}
              required
              anyHint="Switches to this page when any condition is true."
              allHint="Switches to this page when all conditions are true."
            />
          </div>
        ))}
      </div>

      <button
        onClick={() => setRules([...rules, { page: 0, when: { key: "", operator: "truthy" } }])}
        style={{
          marginTop: "var(--space-sm)",
          display: "flex", alignItems: "center", justifyContent: "center", gap: 4,
          padding: "5px 10px", borderRadius: "var(--border-radius)",
          border: "1px dashed var(--border-color)", background: "transparent",
          color: "var(--text-muted)", fontSize: 12, cursor: "pointer",
        }}
      >
        + Add paging rule
      </button>
    </div>
  );
}

// ──── Brightness Rules + Idle Dim ────

interface BrightnessRule {
  level?: number;
  when?: ConditionGroup;
}

function BrightnessEditor({
  config,
  onConfigChange,
  baseBrightness,
  onBaseBrightnessChange,
}: {
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
  // Base level lives in the flat plugin settings (config.brightness). Only
  // passed when editing the main config — a customized deck overrides it
  // via the "This deck's settings" row instead.
  baseBrightness?: number;
  onBaseBrightnessChange?: (value: number | undefined) => void;
}) {
  const rules = (config.auto_brightness as BrightnessRule[] | undefined) ?? [];
  const idleDim = config.idle_dim as { after_seconds?: number; level?: number } | undefined;

  const setRules = (next: BrightnessRule[]) => {
    onConfigChange({ ...config, auto_brightness: next });
  };
  const updateRule = (i: number, patch: Partial<BrightnessRule>) =>
    setRules(rules.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const removeRule = (i: number) => setRules(rules.filter((_, j) => j !== i));
  const moveRule = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= rules.length) return;
    const next = [...rules];
    [next[i], next[j]] = [next[j], next[i]];
    setRules(next);
  };

  const setIdleDim = (next: { after_seconds?: number; level?: number } | undefined) => {
    if (next) {
      onConfigChange({ ...config, idle_dim: next });
    } else {
      const { idle_dim: _drop, ...rest } = config;
      onConfigChange(rest);
    }
  };

  const numInputStyle: React.CSSProperties = {
    width: 64, padding: "4px 6px",
    borderRadius: "var(--border-radius)",
    border: "1px solid var(--border-color)",
    background: "var(--bg-surface)", color: "var(--text-primary)",
    fontSize: "var(--font-size-sm)",
  };

  const reorderBtn: React.CSSProperties = {
    padding: "2px 5px", borderRadius: "var(--border-radius)", fontSize: 9,
    color: "var(--text-muted)", background: "transparent",
    border: "1px solid var(--border-color)", cursor: "pointer", lineHeight: 1,
  };

  return (
    <div style={{ maxWidth: 560 }}>
      {/* Base level (flat plugin setting) */}
      {onBaseBrightnessChange && (
        <label
          style={{
            display: "flex", alignItems: "center", gap: "var(--space-sm)",
            fontSize: "var(--font-size-sm)", color: "var(--text-secondary)",
            marginBottom: "var(--space-sm)",
          }}
        >
          Base brightness
          <input
            type="number" min={0} max={100}
            value={baseBrightness ?? ""}
            placeholder="70"
            onChange={(e) =>
              onBaseBrightnessChange(
                e.target.value === ""
                  ? undefined
                  : Math.max(0, Math.min(100, Number(e.target.value)))
              )
            }
            style={numInputStyle}
          />
          <span style={{ fontSize: 12 }}>% applies when no rule below matches.</span>
        </label>
      )}

      {/* Idle dim */}
      <label style={{
        display: "flex", alignItems: "center", gap: "var(--space-sm)",
        fontSize: "var(--font-size-sm)", color: "var(--text-secondary)",
        marginBottom: "var(--space-sm)", cursor: "pointer", flexWrap: "wrap",
      }}>
        <input
          type="checkbox"
          checked={!!idleDim}
          onChange={(e) =>
            setIdleDim(e.target.checked ? { after_seconds: 300, level: 10 } : undefined)
          }
        />
        Dim when idle
        {idleDim && (
          <span style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", fontSize: 12 }}>
            to
            <input
              type="number" min={0} max={100}
              value={idleDim.level ?? 10}
              onChange={(e) => setIdleDim({ ...idleDim, level: Number(e.target.value) })}
              style={numInputStyle}
            />
            % after
            <input
              type="number" min={5} step={5}
              value={idleDim.after_seconds ?? 300}
              onChange={(e) => setIdleDim({ ...idleDim, after_seconds: Number(e.target.value) })}
              style={numInputStyle}
            />
            seconds without input — any press, turn, or tap wakes it.
          </span>
        )}
      </label>

      {/* State-driven rules */}
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
        {rules.map((rule, i) => (
          <div
            key={i}
            style={{
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              padding: "var(--space-sm)",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-xs)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
              <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>Set brightness to</span>
              <input
                type="number" min={0} max={100}
                value={rule.level ?? 70}
                onChange={(e) => updateRule(i, { level: Number(e.target.value) })}
                style={numInputStyle}
              />
              <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>%</span>
              <div style={{ marginLeft: "auto", display: "flex", gap: 4, alignItems: "center" }}>
                {i > 0 && (
                  <button onClick={() => moveRule(i, -1)} title="Move up" style={reorderBtn}>&#9650;</button>
                )}
                {i < rules.length - 1 && (
                  <button onClick={() => moveRule(i, 1)} title="Move down" style={reorderBtn}>&#9660;</button>
                )}
                <button
                  onClick={() => removeRule(i)}
                  title="Remove rule"
                  style={{
                    padding: "2px 6px", borderRadius: "var(--border-radius)", fontSize: 11,
                    color: "var(--color-error)", background: "transparent",
                    border: "1px solid var(--border-color)", cursor: "pointer",
                  }}
                >
                  &times;
                </button>
              </div>
            </div>
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>when</span>
            <ConditionGroupEditor
              value={rule.when}
              onChange={(when) => updateRule(i, { when })}
              required
              anyHint="Applies this brightness when any condition is true."
              allHint="Applies this brightness when all conditions are true."
            />
          </div>
        ))}
      </div>

      <button
        onClick={() => setRules([...rules, { level: 30, when: { key: "", operator: "truthy" } }])}
        style={{
          marginTop: "var(--space-sm)",
          display: "flex", alignItems: "center", justifyContent: "center", gap: 4,
          padding: "5px 10px", borderRadius: "var(--border-radius)",
          border: "1px dashed var(--border-color)", background: "transparent",
          color: "var(--text-muted)", fontSize: 12, cursor: "pointer",
        }}
      >
        + Add brightness rule
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

const dialTestBtnStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  width: 28,
  height: 24,
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  color: "var(--text-secondary)",
  fontSize: 14,
  cursor: "pointer",
};

// ──── Deck Workbench (device-backed surfaces) ────
//
// The one home for a device-backed surface: a live picture of the unit
// with a persistent inspector rail. Click a control to edit it, Shift+click
// to press it; with nothing selected the rail shows the deck itself. The
// editor page and the hardware page stay in lockstep both ways.

type WorkbenchSelection =
  | { kind: "deck" }
  | { kind: "key"; index: number }
  | { kind: "dial"; index: number }
  | { kind: "strip"; zone: number | null }
  | { kind: "screen" };

const SURFACE_ACTIONS = ["macro", "device.command", "state.set", "navigate"];

// The per-unit config sections an own layout replaces (mirrors the runtime).
const DECK_SECTION_KEYS = [
  "buttons", "global_buttons", "auto_page", "dials", "touchscreen",
  "info_strip", "auto_brightness", "idle_dim", "page_names",
];

function actionList(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) {
    return value.filter((a) => a && typeof a === "object") as Record<string, unknown>[];
  }
  if (value && typeof value === "object") return [value as Record<string, unknown>];
  return [];
}

function forEachNavigateTarget(
  view: Record<string, unknown>,
  fn: (page: unknown) => void
) {
  const scan = (value: unknown) => {
    for (const action of actionList(value)) {
      if (action.action === "navigate") fn(action.page);
      for (const nested of ["off_action", "hold_action"]) {
        const sub = action[nested] as Record<string, unknown> | undefined;
        if (sub && typeof sub === "object" && sub.action === "navigate") {
          fn(sub.page);
        }
      }
    }
  };
  const buttons = (view.buttons as ButtonAssignment[] | undefined) ?? [];
  for (const b of buttons) scan(b?.bindings?.press);
  const globals = (view.global_buttons as ButtonAssignment[] | undefined) ?? [];
  for (const b of globals) scan(b?.bindings?.press);
  const dials = (view.dials as DialAssignment[] | undefined) ?? [];
  for (const d of dials) {
    scan(d?.cw);
    scan(d?.ccw);
    scan(d?.press);
  }
  const zones =
    ((view.touchscreen as { zones?: TouchZone[] } | undefined)?.zones) ?? [];
  for (const z of zones) {
    scan(z?.touch);
    scan(z?.long_touch);
  }
}

// Mirrors the runtime: pages exist by being used — 1 + the highest page
// index referenced by entries, names, paging rules, or navigate targets.
function effectivePageCount(view: Record<string, unknown>): number {
  let highest = 0;
  const note = (value: unknown) => {
    if (typeof value !== "string" && typeof value !== "number") return;
    const n = Number(value);
    if (Number.isInteger(n) && n > highest) highest = n;
  };
  const buttons = (view.buttons as ButtonAssignment[] | undefined) ?? [];
  for (const b of buttons) note(b?.page ?? 0);
  const rules = (view.auto_page as { page?: unknown }[] | undefined) ?? [];
  for (const r of rules) if (r && typeof r === "object") note(r.page);
  const names = (view.page_names as Record<string, string> | undefined) ?? {};
  for (const k of Object.keys(names)) note(k);
  forEachNavigateTarget(view, note);
  return highest + 1;
}

function hasAnyNavigate(view: Record<string, unknown>): boolean {
  let found = false;
  forEachNavigateTarget(view, () => {
    found = true;
  });
  return found;
}

function DeckWorkbench({
  pluginId,
  staticLayout,
  config,
  onConfigChange,
}: {
  pluginId: string;
  staticLayout: SurfaceLayout;
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
}) {
  const liveState = useConnectionStore((s) => s.liveState);
  const statePrefix = `plugin.${pluginId}.`;

  const deckSerials = String(liveState[`${statePrefix}deck_serials`] ?? "")
    .split(",")
    .filter(Boolean);
  const decksMap =
    (config.decks as Record<string, Record<string, unknown>> | undefined) ?? {};
  const deckNames = (config.deck_names as Record<string, string> | undefined) ?? {};
  const deckSettings =
    (config.deck_settings as Record<string, { brightness?: number }> | undefined) ?? {};
  const rememberedSerials = [
    ...new Set([...Object.keys(decksMap), ...Object.keys(deckNames)]),
  ].filter((s) => !deckSerials.includes(s));
  const knownSerials = [...deckSerials, ...rememberedSerials];

  const [selectedSerial, setSelectedSerial] = useState<string | null>(null);
  const activeSerial =
    selectedSerial && knownSerials.includes(selectedSerial)
      ? selectedSerial
      : knownSerials[0];
  const sp = `${statePrefix}${activeSerial}.`;
  const connected = Boolean(liveState[`${sp}connected`]);
  const model = String(liveState[`${sp}model`] ?? "");
  const rows = Number(liveState[`${sp}rows`] ?? 0);
  const columns = Number(liveState[`${sp}columns`] ?? 0);
  const keyCount = Number(liveState[`${sp}key_count`] ?? 0);
  const touchKeyCount = Number(liveState[`${sp}touch_key_count`] ?? 0);
  const dialCount = Number(liveState[`${sp}dial_count`] ?? 0);
  const hasTouchscreen = Boolean(liveState[`${sp}has_touchscreen`]);
  const hasInfoScreen = Boolean(liveState[`${sp}has_info_screen`]);
  const isVisual = liveState[`${sp}visual`] === undefined
    ? true
    : Boolean(liveState[`${sp}visual`]);
  const isVirtual = Boolean(liveState[`${sp}virtual`]);
  const renderVersion = Number(liveState[`${sp}render_version`] ?? 0);
  const deckPage = Number(liveState[`${sp}current_page`] ?? 0);
  // Geometry can outlive a disconnect within a session; a ghost with no
  // geometry at all can't draw a canvas (its layout is still kept).
  const hasGeometry = rows > 0 && columns > 0 && keyCount > 0;

  const isOwn = activeSerial ? decksMap[activeSerial] !== undefined : false;
  const viewConfig: Record<string, unknown> =
    isOwn && activeSerial ? decksMap[activeSerial] : config;
  const onViewChange = useCallback(
    (next: Record<string, unknown>) => {
      if (isOwn && activeSerial) {
        onConfigChange({ ...config, decks: { ...decksMap, [activeSerial]: next } });
      } else {
        onConfigChange(next);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isOwn, activeSerial, config, onConfigChange]
  );

  // ── Pages (emergent) ──
  const pageCount = effectivePageCount(viewConfig);
  const [selection, setSelection] = useState<WorkbenchSelection>({ kind: "deck" });
  const [editorPage, setEditorPage] = useState(0);
  const [draftPage, setDraftPage] = useState(false);
  const totalPages = pageCount + (draftPage ? 1 : 0);

  // A draft page becomes real the moment something references it.
  useEffect(() => {
    if (draftPage && editorPage < pageCount) setDraftPage(false);
  }, [draftPage, editorPage, pageCount]);
  useEffect(() => {
    if (editorPage > totalPages - 1) setEditorPage(totalPages - 1);
  }, [editorPage, totalPages]);

  const pageNames = (viewConfig.page_names as Record<string, string> | undefined) ?? {};
  const pageLabel = useCallback(
    (p: number) => pageNames[String(p)] || `Page ${p + 1}`,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [viewConfig.page_names]
  );
  const renamePage = useCallback(
    (p: number, name: string) => {
      const next = { ...pageNames };
      if (name.trim()) {
        next[String(p)] = name.trim();
      } else {
        delete next[String(p)];
      }
      onViewChange({ ...viewConfig, page_names: next });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pageNames, viewConfig, onViewChange]
  );

  const navigateOptions = useMemo(
    () => [
      { value: "__next_page__", label: "Next Page" },
      { value: "__prev_page__", label: "Previous Page" },
      ...Array.from({ length: totalPages }, (_, p) => ({
        value: String(p),
        label: pageLabel(p),
      })),
    ],
    [totalPages, pageLabel]
  );

  // ── Two-way page sync: the canvas can never lie ──
  const userNavAt = useRef(0);
  const onSelectPage = useCallback(
    (p: number) => {
      setEditorPage(p);
      if (p < pageCount && connected && activeSerial) {
        userNavAt.current = Date.now();
        api
          .emitContextAction(pluginId, "set_page", { serial: activeSerial, page: p })
          .catch(() => {});
      }
    },
    [pageCount, connected, activeSerial, pluginId]
  );
  useEffect(() => {
    if (!connected) return;
    if (draftPage && editorPage >= pageCount) return; // building a new page
    if (deckPage === editorPage) return;
    setEditorPage(deckPage);
    // The user's own tab clicks come right back via state — only narrate
    // flips that came from somewhere else (a page rule, a nav key, a macro).
    if (Date.now() - userNavAt.current > 2500) {
      showInfo(`${deckNames[activeSerial ?? ""] || model || "Deck"} moved to ${pageLabel(deckPage)}.`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deckPage, connected]);

  // Switching decks resets the bench to that deck's reality.
  useEffect(() => {
    setSelection({ kind: "deck" });
    setDraftPage(false);
    setEditorPage(Number(useConnectionStore.getState().liveState[`${statePrefix}${activeSerial}.current_page`] ?? 0));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSerial]);

  // ── Input echo: pressing a physical control flashes it here ──
  const lastInput = String(liveState[`${sp}last_input`] ?? "");
  const [inputFlash, setInputFlash] = useState<{ kind: string; index: number } | null>(null);
  useEffect(() => {
    if (!lastInput) return;
    const [kind, indexStr] = lastInput.split(":");
    const index = Number(indexStr);
    if (!Number.isFinite(index)) return;
    setInputFlash({ kind, index });
    const timer = setTimeout(() => setInputFlash(null), 350);
    return () => clearTimeout(timer);
  }, [lastInput]);

  // ── Live mirror (physical decks mirror only while the bench is open) ──
  useEffect(() => {
    if (!connected || isVirtual) return;
    api.emitContextAction(pluginId, "set_live_mirror", { on: true }).catch(() => {});
    return () => {
      api.emitContextAction(pluginId, "set_live_mirror", { on: false }).catch(() => {});
    };
  }, [pluginId, activeSerial, isVirtual, connected]);

  const [images, setImages] = useState<Record<string, string>>({});
  const imagesRef = useRef<Record<string, string>>({});
  imagesRef.current = images;
  useEffect(() => {
    if (!connected || !isVisual || !activeSerial) {
      setImages((prev) => {
        Object.values(prev).forEach((url) => URL.revokeObjectURL(url));
        return {};
      });
      return;
    }
    let cancelled = false;
    const items: string[] = [];
    for (let i = 0; i < keyCount; i++) items.push(`key_${i}`);
    if (hasTouchscreen) items.push("touchscreen");
    if (hasInfoScreen) items.push("screen");
    (async () => {
      const next: Record<string, string> = {};
      await Promise.all(
        items.map(async (item) => {
          try {
            const res = await fetch(
              `${BASE}/plugins/${pluginId}/ext/live/${activeSerial}/${item}?v=${renderVersion}`
            );
            if (!res.ok) return;
            next[item] = URL.createObjectURL(await res.blob());
          } catch {
            /* mirror not populated yet */
          }
        })
      );
      if (cancelled) {
        Object.values(next).forEach((url) => URL.revokeObjectURL(url));
        return;
      }
      setImages((prev) => {
        Object.values(prev).forEach((url) => URL.revokeObjectURL(url));
        return next;
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [pluginId, activeSerial, renderVersion, keyCount, hasTouchscreen, hasInfoScreen, connected, isVisual]);
  useEffect(
    () => () => {
      Object.values(imagesRef.current).forEach((url) => URL.revokeObjectURL(url));
    },
    []
  );

  const simulate = useCallback(
    (payload: Record<string, unknown>) => {
      if (!activeSerial) return;
      api
        .emitContextAction(pluginId, "simulate_input", { serial: activeSerial, ...payload })
        .catch(() => {});
    },
    [pluginId, activeSerial]
  );

  // ── Assignments (locked keys win on every page, like the runtime) ──
  const buttons = (viewConfig.buttons as ButtonAssignment[] | undefined) ?? [];
  const globalButtons =
    (viewConfig.global_buttons as ButtonAssignment[] | undefined) ?? [];
  const lockedIndexes = useMemo(
    () => new Set(globalButtons.map((b) => b.index)),
    [globalButtons]
  );
  const isLocked = useCallback(
    (index: number) => lockedIndexes.has(index),
    [lockedIndexes]
  );
  const getAssignment = useCallback(
    (index: number, page: number = 0): ButtonAssignment | undefined => {
      const locked = globalButtons.find((b) => b.index === index);
      if (locked) return locked;
      return buttons.find((b) => b.index === index && (b.page ?? 0) === page);
    },
    [buttons, globalButtons]
  );
  const shadowPageCount = useCallback(
    (index: number) =>
      new Set(
        buttons.filter((b) => b.index === index).map((b) => b.page ?? 0)
      ).size,
    [buttons]
  );

  const updateAssignment = useCallback(
    (index: number, page: number, updates: Partial<ButtonAssignment>) => {
      if (lockedIndexes.has(index)) {
        const others = globalButtons.filter((b) => b.index !== index);
        const current = globalButtons.find((b) => b.index === index);
        const updated = { index, ...(current ?? {}), ...updates };
        delete (updated as Record<string, unknown>).page;
        onViewChange({ ...viewConfig, global_buttons: [...others, updated] });
        return;
      }
      const existing = buttons.filter(
        (b) => !(b.index === index && (b.page ?? 0) === page)
      );
      const current = buttons.find(
        (b) => b.index === index && (b.page ?? 0) === page
      );
      const updated = { index, page, ...(current ?? {}), ...updates };
      onViewChange({ ...viewConfig, buttons: [...existing, updated] });
    },
    [buttons, globalButtons, lockedIndexes, viewConfig, onViewChange]
  );

  const clearAssignment = useCallback(
    (index: number, page: number) => {
      if (lockedIndexes.has(index)) {
        onViewChange({
          ...viewConfig,
          global_buttons: globalButtons.filter((b) => b.index !== index),
        });
        return;
      }
      onViewChange({
        ...viewConfig,
        buttons: buttons.filter(
          (b) => !(b.index === index && (b.page ?? 0) === page)
        ),
      });
    },
    [buttons, globalButtons, lockedIndexes, viewConfig, onViewChange]
  );

  const toggleLock = useCallback(
    (index: number, locked: boolean) => {
      if (locked) {
        // Lock: the key's current-page content becomes the deck-wide entry.
        // Page entries stay in config (hidden) so unlocking can't lose work.
        const template =
          buttons.find((b) => b.index === index && (b.page ?? 0) === editorPage) ?? {};
        const entry: ButtonAssignment = JSON.parse(JSON.stringify(template));
        delete (entry as Record<string, unknown>).page;
        entry.index = index;
        onViewChange({
          ...viewConfig,
          global_buttons: [...globalButtons.filter((b) => b.index !== index), entry],
        });
      } else {
        // Unlock: the assignment lands on the page being edited (so nothing
        // visible disappears); other pages' hidden entries come back.
        const entry = globalButtons.find((b) => b.index === index);
        const nextGlobals = globalButtons.filter((b) => b.index !== index);
        const nextButtons = buttons.filter(
          (b) => !(b.index === index && (b.page ?? 0) === editorPage)
        );
        if (entry) {
          const restored: ButtonAssignment = JSON.parse(JSON.stringify(entry));
          restored.page = editorPage;
          nextButtons.push(restored);
        }
        onViewChange({
          ...viewConfig,
          buttons: nextButtons,
          global_buttons: nextGlobals,
        });
      }
    },
    [buttons, globalButtons, editorPage, viewConfig, onViewChange]
  );

  // ── Clipboard / arrange (page entries only) ──
  const [clipboard, setClipboard] = useState<ButtonAssignment | null>(null);
  const copyAssignment = useCallback(
    (index: number, page: number) => {
      const current = getAssignment(index, page);
      if (!current) return;
      const { index: _i, page: _p, ...rest } = current;
      setClipboard(JSON.parse(JSON.stringify(rest)));
    },
    [getAssignment]
  );
  const pasteAssignment = useCallback(
    (index: number, page: number) => {
      if (!clipboard) return;
      updateAssignment(index, page, JSON.parse(JSON.stringify(clipboard)));
    },
    [clipboard, updateAssignment]
  );
  const moveAssignment = useCallback(
    (from: { index: number; page: number }, to: { index: number; page: number }) => {
      const source = buttons.find(
        (b) => b.index === from.index && (b.page ?? 0) === from.page
      );
      if (!source) return;
      const others = buttons.filter(
        (b) =>
          !(b.index === from.index && (b.page ?? 0) === from.page) &&
          !(b.index === to.index && (b.page ?? 0) === to.page)
      );
      onViewChange({
        ...viewConfig,
        buttons: [...others, { ...source, index: to.index, page: to.page }],
      });
      setSelection({ kind: "key", index: to.index });
      onSelectPage(to.page);
    },
    [buttons, viewConfig, onViewChange, onSelectPage]
  );
  const swapAssignments = useCallback(
    (a: { index: number; page: number }, b: { index: number; page: number }) => {
      const first = buttons.find(
        (x) => x.index === a.index && (x.page ?? 0) === a.page
      );
      const second = buttons.find(
        (x) => x.index === b.index && (x.page ?? 0) === b.page
      );
      const others = buttons.filter(
        (x) =>
          !(x.index === a.index && (x.page ?? 0) === a.page) &&
          !(x.index === b.index && (x.page ?? 0) === b.page)
      );
      const next = [...others];
      if (first) next.push({ ...first, index: b.index, page: b.page });
      if (second) next.push({ ...second, index: a.index, page: a.page });
      onViewChange({ ...viewConfig, buttons: next });
    },
    [buttons, viewConfig, onViewChange]
  );

  // ── Page operations ──
  const duplicatePage = useCallback(
    (fromPage: number) => {
      const target = pageCount; // a fresh page right after the last one
      const copies = buttons
        .filter((b) => (b.page ?? 0) === fromPage)
        .map((b) => ({ ...JSON.parse(JSON.stringify(b)), page: target }));
      if (copies.length === 0) return;
      onViewChange({ ...viewConfig, buttons: [...buttons, ...copies] });
      setEditorPage(target);
    },
    [buttons, pageCount, viewConfig, onViewChange]
  );
  const clearPage = useCallback(
    (page: number) => {
      onViewChange({
        ...viewConfig,
        buttons: buttons.filter((b) => (b.page ?? 0) !== page),
      });
      setSelection({ kind: "deck" });
    },
    [buttons, viewConfig, onViewChange]
  );
  // The last page can be deleted only when nothing else (a rule, a navigate
  // target) keeps it alive — content and name are removed together.
  const lastPageBlockers = useMemo(() => {
    if (pageCount <= 1) return true;
    const last = pageCount - 1;
    const rules = (viewConfig.auto_page as { page?: unknown }[] | undefined) ?? [];
    if (rules.some((r) => Number(r?.page) === last)) return true;
    let referenced = false;
    forEachNavigateTarget(viewConfig, (page) => {
      if (Number(page) === last) referenced = true;
    });
    return referenced;
  }, [viewConfig, pageCount]);
  const deleteLastPage = useCallback(() => {
    const last = pageCount - 1;
    const nextNames = { ...pageNames };
    delete nextNames[String(last)];
    onViewChange({
      ...viewConfig,
      buttons: buttons.filter((b) => (b.page ?? 0) !== last),
      page_names: nextNames,
    });
    setEditorPage(Math.max(0, last - 1));
    setSelection({ kind: "deck" });
  }, [buttons, pageNames, pageCount, viewConfig, onViewChange]);

  // ── Add page (+) with first-time locked nav keys ──
  const addPage = useCallback(() => {
    if (draftPage) {
      setEditorPage(pageCount); // already drafting — just go there
      return;
    }
    let nextView = viewConfig;
    if (pageCount === 1 && isVisual && hasGeometry && !hasAnyNavigate(viewConfig)) {
      // Both free slots scan backward from the bottom-right LCD key; only
      // slots empty on every page (and not locked) are eligible. Fewer than
      // two free slots -> skip silently, never shadow existing work.
      const free: number[] = [];
      for (let i = keyCount - 1; i >= 0 && free.length < 2; i--) {
        const used =
          lockedIndexes.has(i) || buttons.some((b) => b.index === i);
        if (!used) free.push(i);
      }
      if (free.length === 2) {
        const [nextIdx, prevIdx] = free; // rightmost = next page
        nextView = {
          ...viewConfig,
          global_buttons: [
            ...globalButtons,
            {
              index: prevIdx,
              icon: "chevron-left",
              bindings: { press: [{ action: "navigate", page: "__prev_page__" }] },
            },
            {
              index: nextIdx,
              icon: "chevron-right",
              bindings: { press: [{ action: "navigate", page: "__next_page__" }] },
            },
          ],
        };
        onViewChange(nextView);
        showInfo("Added locked page keys — move or remove them anytime.");
      }
    }
    setDraftPage(true);
    setEditorPage(effectivePageCount(nextView));
  }, [draftPage, pageCount, isVisual, hasGeometry, viewConfig, keyCount, lockedIndexes, buttons, globalButtons, onViewChange]);

  // ── Dials ──
  const dials = (viewConfig.dials as DialAssignment[] | undefined) ?? [];
  const getDial = useCallback(
    (index: number): DialAssignment | undefined => dials.find((d) => d.index === index),
    [dials]
  );
  const updateDial = useCallback(
    (index: number, updates: Partial<DialAssignment>) => {
      const others = dials.filter((d) => d.index !== index);
      const current = dials.find((d) => d.index === index);
      onViewChange({
        ...viewConfig,
        dials: [...others, { index, ...(current ?? {}), ...updates }],
      });
    },
    [dials, viewConfig, onViewChange]
  );
  const clearDial = useCallback(
    (index: number) => {
      onViewChange({ ...viewConfig, dials: dials.filter((d) => d.index !== index) });
    },
    [dials, viewConfig, onViewChange]
  );

  // ── Unit + layout operations ──
  const renameDeck = useCallback(
    (serial: string, name: string) => {
      const next = { ...deckNames };
      if (name) {
        next[serial] = name;
      } else {
        delete next[serial];
      }
      onConfigChange({ ...config, deck_names: next });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [deckNames, config, onConfigChange]
  );
  const setDeckBrightness = useCallback(
    (serial: string, level: number | undefined) => {
      const next = { ...deckSettings };
      if (level === undefined) {
        const entry = { ...(next[serial] ?? {}) };
        delete entry.brightness;
        if (Object.keys(entry).length === 0) {
          delete next[serial];
        } else {
          next[serial] = entry;
        }
      } else {
        next[serial] = { ...(next[serial] ?? {}), brightness: level };
      }
      onConfigChange({ ...config, deck_settings: next });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [deckSettings, config, onConfigChange]
  );
  const giveOwnLayout = useCallback(() => {
    if (!activeSerial) return;
    const copy: Record<string, unknown> = {};
    for (const section of DECK_SECTION_KEYS) {
      if (config[section] !== undefined) {
        copy[section] = JSON.parse(JSON.stringify(config[section]));
      }
    }
    onConfigChange({ ...config, decks: { ...decksMap, [activeSerial]: copy } });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSerial, config, decksMap, onConfigChange]);
  const useSharedLayout = useCallback(() => {
    if (!activeSerial) return;
    const next = { ...decksMap };
    delete next[activeSerial];
    onConfigChange({ ...config, decks: next });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSerial, config, decksMap, onConfigChange]);
  const moveLayoutTo = useCallback(
    (fromSerial: string, toSerial: string) => {
      const layoutToMove = decksMap[fromSerial];
      if (!layoutToMove || fromSerial === toSerial) return;
      const nextDecks = { ...decksMap };
      delete nextDecks[fromSerial];
      nextDecks[toSerial] = layoutToMove;
      const nextNames = { ...deckNames };
      if (nextNames[fromSerial] !== undefined) {
        nextNames[toSerial] = nextNames[fromSerial];
        delete nextNames[fromSerial];
      }
      const next: Record<string, unknown> = {
        ...config,
        decks: nextDecks,
        deck_names: nextNames,
      };
      const virtuals =
        (config.virtual_decks as { model?: string; serial?: string }[] | undefined) ?? [];
      if (virtuals.some((v) => v.serial === fromSerial)) {
        next.virtual_decks = virtuals.filter((v) => v.serial !== fromSerial);
      }
      onConfigChange(next);
      setSelectedSerial(toSerial);
      setSelection({ kind: "deck" });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [decksMap, deckNames, config, onConfigChange]
  );
  const forgetDeck = useCallback(
    (serial: string) => {
      const nextDecks = { ...decksMap };
      delete nextDecks[serial];
      const nextNames = { ...deckNames };
      delete nextNames[serial];
      const nextSettings = { ...deckSettings };
      delete nextSettings[serial];
      onConfigChange({
        ...config,
        decks: nextDecks,
        deck_names: nextNames,
        deck_settings: nextSettings,
      });
      setSelectedSerial(null);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [decksMap, deckNames, deckSettings, config, onConfigChange]
  );
  const removeVirtualDeck = useCallback(
    (serial: string) => {
      const virtuals =
        (config.virtual_decks as { model?: string; serial?: string }[] | undefined) ?? [];
      onConfigChange({
        ...config,
        virtual_decks: virtuals.filter((v) => v.serial !== serial),
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [config, onConfigChange]
  );
  const addVirtual = useCallback(
    (modelName: string) => {
      onConfigChange(addVirtualUnit(config, modelName).next);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [config, onConfigChange]
  );

  // ── Section summary metas ──
  const autoPageRules = (viewConfig.auto_page as unknown[] | undefined) ?? [];
  const brightnessRules =
    (viewConfig.auto_brightness as unknown[] | undefined) ?? [];
  const idleDim = viewConfig.idle_dim as
    | { after_seconds?: number; level?: number }
    | undefined;
  const brightnessAutoParts: string[] = [];
  if (idleDim) brightnessAutoParts.push(`idle dim ${idleDim.level ?? 10}%`);
  if (brightnessRules.length) {
    brightnessAutoParts.push(
      `${brightnessRules.length} rule${brightnessRules.length === 1 ? "" : "s"}`
    );
  }
  const appearanceParts: string[] = [];
  if (typeof viewConfig.button_color === "string") appearanceParts.push("button color");
  if (typeof viewConfig.text_color === "string") appearanceParts.push("text color");

  const deckDisplayName = activeSerial
    ? deckNames[activeSerial] || model || activeSerial
    : "";
  const ownerName = isOwn ? deckDisplayName : null;
  const sharedWith = deckSerials.filter((s) => decksMap[s] === undefined);

  const selectedKeyIndex = selection.kind === "key" ? selection.index : null;
  const totalKeys = keyCount + touchKeyCount;

  // Strip zone pixel bounds (mirrors the runtime: explicit x/w, else an even
  // split; default = one zone per dial). Drives canvas clicks + touch echo.
  const stripZones =
    ((viewConfig.touchscreen as { zones?: TouchZone[] } | undefined)?.zones) ?? [];
  const zoneBounds = useMemo(() => {
    const count = stripZones.length > 0 ? stripZones.length : dialCount;
    if (count <= 0) return [];
    const slot = 800 / count;
    if (stripZones.length > 0) {
      return stripZones.map((z, i) => ({
        x: typeof z.x === "number" ? z.x : i * slot,
        w: typeof z.w === "number" ? z.w : slot,
      }));
    }
    return Array.from({ length: count }, (_, i) => ({ x: i * slot, w: slot }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewConfig.touchscreen, dialCount]);

  const inspector =
    selection.kind === "key" && selectedKeyIndex !== null ? (
      <ControlAssignmentPanel
        controlId={String(selectedKeyIndex)}
        allowedActions={SURFACE_ACTIONS}
        navigateOptions={navigateOptions}
        colorOnly={touchKeyCount > 0 && selectedKeyIndex >= keyCount}
        visualDeck={isVisual}
        keyCount={keyCount}
        assignment={getAssignment(selectedKeyIndex, editorPage)}
        onUpdate={(updates) => updateAssignment(selectedKeyIndex, editorPage, updates)}
        onClear={() => clearAssignment(selectedKeyIndex, editorPage)}
        onClose={() => setSelection({ kind: "deck" })}
        pageName={pageLabel(editorPage)}
        locked={isLocked(selectedKeyIndex)}
        onToggleLock={(locked) => toggleLock(selectedKeyIndex, locked)}
        lockShadowCount={shadowPageCount(selectedKeyIndex)}
        onPress={
          connected
            ? () => simulate({ type: "key", index: selectedKeyIndex })
            : undefined
        }
        arrange={{
          page: editorPage,
          maxPages: totalPages,
          totalKeys: totalKeys > 0 ? totalKeys : (rows || 3) * (columns || 5),
          pageLabel,
          clipboardReady: clipboard !== null,
          onCopy: () => copyAssignment(selectedKeyIndex, editorPage),
          onPaste: () => pasteAssignment(selectedKeyIndex, editorPage),
          onMove: (to) => moveAssignment({ index: selectedKeyIndex, page: editorPage }, to),
          onSwap: (to) => swapAssignments({ index: selectedKeyIndex, page: editorPage }, to),
        }}
      />
    ) : selection.kind === "dial" ? (
      <DialAssignmentPanel
        dialIndex={selection.index}
        dial={getDial(selection.index)}
        allowedActions={SURFACE_ACTIONS}
        navigateOptions={navigateOptions}
        onUpdate={(updates) => updateDial(selection.index, updates)}
        onClear={() => clearDial(selection.index)}
        onClose={() => setSelection({ kind: "deck" })}
        onSimulate={connected ? simulate : undefined}
        onOpenStrip={
          hasTouchscreen
            ? () => setSelection({ kind: "strip", zone: null })
            : undefined
        }
      />
    ) : selection.kind === "strip" ? (
      <RailPanel title="Touch Strip" onClose={() => setSelection({ kind: "deck" })}>
        <TouchscreenZonesEditor
          config={viewConfig}
          onConfigChange={onViewChange}
          allowedActions={SURFACE_ACTIONS}
          navigateOptions={navigateOptions}
          initialExpanded={selection.zone}
          dials={dials}
          dialCount={dialCount}
          onSimulate={connected ? simulate : undefined}
        />
      </RailPanel>
    ) : selection.kind === "screen" ? (
      <RailPanel title="Info Screen" onClose={() => setSelection({ kind: "deck" })}>
        <InfoStripEditor config={viewConfig} onConfigChange={onViewChange} />
      </RailPanel>
    ) : (
      <DeckInspector
        serial={activeSerial ?? ""}
        name={activeSerial ? deckNames[activeSerial] ?? "" : ""}
        model={model}
        connected={connected}
        isVirtual={isVirtual}
        deckCount={knownSerials.length}
        isOwn={isOwn}
        sharedCount={sharedWith.length}
        brightness={
          activeSerial ? deckSettings[activeSerial]?.brightness : undefined
        }
        fallbackBrightness={
          typeof config.brightness === "number" ? (config.brightness as number) : 70
        }
        onRename={(name) => activeSerial && renameDeck(activeSerial, name)}
        onBrightness={(level) => activeSerial && setDeckBrightness(activeSerial, level)}
        onIdentify={
          connected && activeSerial
            ? () =>
                api
                  .emitContextAction(pluginId, "identify_deck", { serial: activeSerial })
                  .catch(() => {})
            : undefined
        }
        onGiveOwnLayout={!isOwn && knownSerials.length > 1 ? giveOwnLayout : undefined}
        onUseSharedLayout={isOwn ? useSharedLayout : undefined}
        moveTargets={
          isOwn
            ? knownSerials
                .filter((s) => s !== activeSerial)
                .map((s) => ({
                  serial: s,
                  label: deckNames[s] || String(liveState[`${statePrefix}${s}.model`] ?? s),
                  hasOwn: decksMap[s] !== undefined,
                }))
            : []
        }
        onMoveLayoutTo={(to) => activeSerial && moveLayoutTo(activeSerial, to)}
        onRemoveVirtual={
          isVirtual && activeSerial ? () => removeVirtualDeck(activeSerial) : undefined
        }
        onForget={
          !connected && activeSerial ? () => forgetDeck(activeSerial) : undefined
        }
        virtualModels={staticLayout.virtual_models ?? []}
        deviceLabel={staticLayout.device_label || "device"}
        onAddVirtual={addVirtual}
        hasTouchscreen={hasTouchscreen}
        customZoneCount={stripZones.length}
        onOpenStrip={() => setSelection({ kind: "strip", zone: null })}
      />
    );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-lg)" }}>
      {/* Known decks (connected + remembered) — selection cards */}
      {knownSerials.length > 1 && (
        <DeckCards
          serials={knownSerials}
          connectedSerials={deckSerials}
          activeSerial={activeSerial ?? ""}
          statePrefix={statePrefix}
          deckNames={deckNames}
          decksMap={decksMap}
          liveState={liveState}
          onSelect={(serial) => {
            setSelectedSerial(serial);
          }}
        />
      )}

      <div style={{ display: "flex", gap: "var(--space-lg)", alignItems: "flex-start" }}>
        <div style={{ flex: "1 1 auto", minWidth: 0 }}>
          {/* Page tabs + the always-visible editing scope */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-md)",
              flexWrap: "wrap",
              marginBottom: "var(--space-sm)",
            }}
          >
            <PageTabsRow
              totalPages={totalPages}
              pageCount={pageCount}
              currentPage={editorPage}
              pageLabel={pageLabel}
              onSelect={onSelectPage}
              onAdd={addPage}
              onRename={(p, name) => renamePage(p, name)}
              onDuplicate={() => duplicatePage(editorPage)}
              onClearPage={() => clearPage(editorPage)}
              canDelete={editorPage === pageCount - 1 && !lastPageBlockers}
              onDelete={deleteLastPage}
              hasContent={buttons.some((b) => (b.page ?? 0) === editorPage)}
            />
            {knownSerials.length > 1 && (
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                {isOwn ? (
                  <>
                    Editing <strong style={{ color: "var(--text-secondary)" }}>{ownerName}'s own layout</strong> — other decks aren't affected.
                  </>
                ) : (
                  <>
                    Editing the <strong style={{ color: "var(--text-secondary)" }}>shared layout</strong> — shown on every deck without its own.
                  </>
                )}
              </span>
            )}
          </div>

          {draftPage && editorPage >= pageCount && (
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
              New page — it's created (and reachable from the deck) as soon as
              you put something on it.
            </div>
          )}

          {hasGeometry ? (
            <BezelCanvas
              name={deckDisplayName}
              model={model}
              connected={connected}
              isVirtual={isVirtual}
              rows={rows}
              columns={columns}
              keyCount={keyCount}
              touchKeyCount={touchKeyCount}
              dialCount={dialCount}
              hasTouchscreen={hasTouchscreen}
              hasInfoScreen={hasInfoScreen}
              images={draftPage && editorPage >= pageCount ? {} : images}
              liveImagesValid={connected && isVisual && !(draftPage && editorPage >= pageCount)}
              touchKeyColors={Array.from({ length: touchKeyCount }, (_, i) =>
                String(liveState[`${sp}touch_key.${keyCount + i}`] ?? "")
              )}
              selection={selection}
              lockedIndexes={lockedIndexes}
              inputFlash={inputFlash}
              currentPage={editorPage}
              getAssignment={getAssignment}
              getDial={getDial}
              customZoneCount={stripZones.length}
              zoneBounds={zoneBounds}
              onSelect={setSelection}
              onSimulate={connected ? simulate : undefined}
            />
          ) : (
            <div
              style={{
                padding: "var(--space-xl)",
                border: "1px dashed var(--border-color)",
                borderRadius: "var(--border-radius)",
                color: "var(--text-muted)",
                fontSize: "var(--font-size-sm)",
                textAlign: "center",
                lineHeight: 1.6,
              }}
            >
              {deckDisplayName} is not connected.
              <br />
              Reconnect it to edit — its layout is kept. Layout tools are in
              the panel on the right.
            </div>
          )}
        </div>

        {inspector}
      </div>

      {/* Layout-scoped extras, tucked below the bench */}
      <CollapsibleSection
        title="Page automation"
        subtitle="Jump to a page when system state changes"
        meta={
          autoPageRules.length
            ? `${autoPageRules.length} rule${autoPageRules.length === 1 ? "" : "s"}`
            : "off"
        }
        defaultOpen={false}
      >
        <AutoPageEditor
          layout={{ ...staticLayout, max_pages: totalPages }}
          config={viewConfig}
          onConfigChange={onViewChange}
        />
      </CollapsibleSection>
      <CollapsibleSection
        title="Brightness automation"
        subtitle="Idle dimming and state-driven levels (base brightness lives on the deck)"
        meta={brightnessAutoParts.length ? brightnessAutoParts.join(" · ") : "off"}
        defaultOpen={false}
      >
        <BrightnessEditor config={viewConfig} onConfigChange={onViewChange} />
      </CollapsibleSection>
      <CollapsibleSection
        title="Appearance"
        subtitle="Default key colors for this layout"
        meta={appearanceParts.length ? appearanceParts.join(" · ") : "defaults"}
        defaultOpen={false}
      >
        <AppearanceEditor
          viewConfig={viewConfig}
          onViewChange={onViewChange}
          inherits={isOwn}
        />
      </CollapsibleSection>
    </div>
  );
}

// ──── Rail Panel (inspector shell for strip / screen editors) ────

function RailPanel({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
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
        gap: "var(--space-md)",
        maxHeight: "100%",
        overflow: "auto",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h4 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600 }}>{title}</h4>
        <button onClick={onClose} style={{ color: "var(--text-muted)", cursor: "pointer" }}>
          <X size={14} />
        </button>
      </div>
      {children}
    </div>
  );
}

// ──── Deck Cards (known units: connected, virtual, remembered) ────

function DeckCards({
  serials,
  connectedSerials,
  activeSerial,
  statePrefix,
  deckNames,
  decksMap,
  liveState,
  onSelect,
}: {
  serials: string[];
  connectedSerials: string[];
  activeSerial: string;
  statePrefix: string;
  deckNames: Record<string, string>;
  decksMap: Record<string, Record<string, unknown>>;
  liveState: Record<string, unknown>;
  onSelect: (serial: string) => void;
}) {
  return (
    <div style={{ display: "flex", alignItems: "stretch", gap: "var(--space-sm)", flexWrap: "wrap" }}>
      {serials.map((serial) => {
        const isConnected = connectedSerials.includes(serial);
        const isActive = serial === activeSerial;
        const model = String(liveState[`${statePrefix}${serial}.model`] ?? "");
        const virtual = Boolean(liveState[`${statePrefix}${serial}.virtual`]);
        const own = decksMap[serial] !== undefined;
        const page = Number(liveState[`${statePrefix}${serial}.current_page`] ?? 0);
        const pageNames =
          ((own ? decksMap[serial] : undefined)?.page_names as Record<string, string> | undefined) ?? {};
        const status: string[] = [];
        if (!isConnected) {
          status.push(own ? "not connected · layout saved" : "not connected");
        } else {
          status.push(own ? "own layout" : "shared layout");
          status.push(`on ${pageNames[String(page)] || `Page ${page + 1}`}`);
        }
        return (
          <button
            key={serial}
            onClick={() => onSelect(serial)}
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-start",
              gap: 2,
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: isActive ? "var(--accent-dim)" : "var(--bg-surface)",
              border: isActive ? "2px solid var(--accent)" : "1px solid var(--border-color)",
              opacity: isConnected ? 1 : 0.55,
              cursor: "pointer",
              textAlign: "left",
            }}
          >
            <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "var(--font-size-sm)", fontWeight: isActive ? 600 : 400 }}>
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  flexShrink: 0,
                  background: isConnected ? "var(--color-success)" : "var(--text-muted)",
                }}
              />
              {deckNames[serial] || model || serial}
              {virtual && (
                <span style={{ fontSize: 9, color: "var(--text-muted)", border: "1px solid var(--border-color)", borderRadius: 3, padding: "0 4px" }}>
                  virtual
                </span>
              )}
            </span>
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
              {(deckNames[serial] && model ? `${model} · ` : "")}{status.join(" · ")}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// ──── Page Tabs Row (emergent pages: tabs + add + page menu) ────

function PageTabsRow({
  totalPages,
  pageCount,
  currentPage,
  pageLabel,
  onSelect,
  onAdd,
  onRename,
  onDuplicate,
  onClearPage,
  canDelete,
  onDelete,
  hasContent,
}: {
  totalPages: number;
  pageCount: number;
  currentPage: number;
  pageLabel: (p: number) => string;
  onSelect: (p: number) => void;
  onAdd: () => void;
  onRename: (p: number, name: string) => void;
  onDuplicate: () => void;
  onClearPage: () => void;
  canDelete: boolean;
  onDelete: () => void;
  hasContent: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const commit = () => {
    setEditing(false);
    onRename(currentPage, draft);
  };
  const startRename = () => {
    const label = pageLabel(currentPage);
    setDraft(label !== `Page ${currentPage + 1}` ? label : "");
    setEditing(true);
    setMenuOpen(false);
  };

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", flexWrap: "wrap" }}>
      {Array.from({ length: totalPages }, (_, p) => {
        const isActive = p === currentPage;
        const isDraft = p >= pageCount;
        if (isActive && editing) {
          return (
            <input
              key={p}
              autoFocus
              value={draft}
              placeholder={`Page ${p + 1}`}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commit}
              onKeyDown={(e) => {
                if (e.key === "Enter") commit();
                if (e.key === "Escape") setEditing(false);
              }}
              style={{
                width: 110,
                padding: "3px 8px",
                borderRadius: "var(--border-radius)",
                border: "1px solid var(--accent)",
                background: "var(--bg-surface)",
                color: "var(--text-primary)",
                fontSize: "var(--font-size-sm)",
              }}
            />
          );
        }
        return (
          <button
            key={p}
            onClick={() => onSelect(p)}
            onDoubleClick={isActive ? startRename : undefined}
            title={isActive ? "Double-click to rename this page" : undefined}
            style={{
              padding: "3px 12px",
              borderRadius: "var(--border-radius)",
              border: isActive ? "1px solid var(--accent)" : "1px solid var(--border-color)",
              background: isActive ? "var(--accent-dim)" : "var(--bg-surface)",
              color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
              fontSize: "var(--font-size-sm)",
              fontWeight: isActive ? 600 : 400,
              fontStyle: isDraft ? "italic" : "normal",
              opacity: isDraft && !isActive ? 0.6 : 1,
              cursor: "pointer",
              maxWidth: 160,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {pageLabel(p)}
          </button>
        );
      })}
      <button
        onClick={onAdd}
        title="Add a page"
        style={{
          padding: "3px 10px",
          borderRadius: "var(--border-radius)",
          border: "1px dashed var(--border-color)",
          background: "transparent",
          color: "var(--text-muted)",
          fontSize: "var(--font-size-sm)",
          cursor: "pointer",
        }}
      >
        +
      </button>
      <div style={{ position: "relative" }}>
        <button
          onClick={() => {
            setMenuOpen(!menuOpen);
            setConfirmClear(false);
            setConfirmDelete(false);
          }}
          title="Page actions"
          style={{
            display: "flex",
            alignItems: "center",
            padding: "4px 6px",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-hover)",
            color: "var(--text-secondary)",
            cursor: "pointer",
          }}
        >
          <MoreHorizontal size={14} />
        </button>
        {menuOpen && (
          <div
            style={{
              position: "absolute",
              top: "100%",
              left: 0,
              zIndex: 50,
              marginTop: 4,
              minWidth: 210,
              background: "var(--bg-surface)",
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <button onClick={startRename} style={pageMenuItemStyle(true)}>
              Rename page
            </button>
            <button
              onClick={() => {
                onDuplicate();
                setMenuOpen(false);
              }}
              disabled={!hasContent}
              title={hasContent ? "Copy this page's keys onto a new page" : "Nothing on this page to copy"}
              style={pageMenuItemStyle(hasContent)}
            >
              Duplicate to a new page
            </button>
            {!confirmClear ? (
              <button
                onClick={() => setConfirmClear(true)}
                disabled={!hasContent}
                style={{ ...pageMenuItemStyle(hasContent), color: hasContent ? "var(--color-error)" : undefined }}
              >
                Clear this page...
              </button>
            ) : (
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", padding: "var(--space-sm) var(--space-md)", fontSize: 12 }}>
                <span style={{ color: "var(--color-error)" }}>Remove every key?</span>
                <button
                  onClick={() => {
                    onClearPage();
                    setMenuOpen(false);
                    setConfirmClear(false);
                  }}
                  style={pageMenuConfirmStyle}
                >
                  Yes
                </button>
                <button onClick={() => setConfirmClear(false)} style={pageMenuConfirmStyle}>
                  No
                </button>
              </div>
            )}
            {!confirmDelete ? (
              <button
                onClick={() => setConfirmDelete(true)}
                disabled={!canDelete}
                title={
                  canDelete
                    ? "Remove the last page (its keys and name go with it)"
                    : "Only the last page can be deleted, and only when no rule or page key still points at it"
                }
                style={{ ...pageMenuItemStyle(canDelete), color: canDelete ? "var(--color-error)" : undefined }}
              >
                Delete page
              </button>
            ) : (
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", padding: "var(--space-sm) var(--space-md)", fontSize: 12 }}>
                <span style={{ color: "var(--color-error)" }}>Delete this page?</span>
                <button
                  onClick={() => {
                    onDelete();
                    setMenuOpen(false);
                    setConfirmDelete(false);
                  }}
                  style={pageMenuConfirmStyle}
                >
                  Yes
                </button>
                <button onClick={() => setConfirmDelete(false)} style={pageMenuConfirmStyle}>
                  No
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

const pageMenuItemStyle = (enabled: boolean): React.CSSProperties => ({
  padding: "var(--space-sm) var(--space-md)",
  textAlign: "left",
  fontSize: "var(--font-size-sm)",
  cursor: enabled ? "pointer" : "default",
  opacity: enabled ? 1 : 0.45,
  background: "transparent",
});

const pageMenuConfirmStyle: React.CSSProperties = {
  padding: "2px 8px",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  cursor: "pointer",
  fontSize: 12,
};

// ──── Bezel Canvas (the live picture of the deck) ────
//
// Drawn as the physical object: keys, side touch keys, info screen, touch
// strip, and dials in hardware order inside a dark shell, with the deck's
// identity etched on the lower edge. Cells show the deck's real rendering
// (live mirror) and fall back to a schematic before it populates. Click =
// edit, Shift+click (or the hover ▶) = press.

const CANVAS_KEY_PX = 72;
const CANVAS_GAP = 6;

function BezelCanvas({
  name,
  model,
  connected,
  isVirtual,
  rows,
  columns,
  keyCount,
  touchKeyCount,
  dialCount,
  hasTouchscreen,
  hasInfoScreen,
  images,
  liveImagesValid,
  touchKeyColors,
  selection,
  lockedIndexes,
  inputFlash,
  currentPage,
  getAssignment,
  getDial,
  customZoneCount,
  zoneBounds,
  onSelect,
  onSimulate,
}: {
  name: string;
  model: string;
  connected: boolean;
  isVirtual: boolean;
  rows: number;
  columns: number;
  keyCount: number;
  touchKeyCount: number;
  dialCount: number;
  hasTouchscreen: boolean;
  hasInfoScreen: boolean;
  images: Record<string, string>;
  liveImagesValid: boolean;
  touchKeyColors: string[];
  selection: WorkbenchSelection;
  lockedIndexes: Set<number | undefined>;
  inputFlash: { kind: string; index: number } | null;
  currentPage: number;
  getAssignment: (index: number, page?: number) => ButtonAssignment | undefined;
  getDial: (index: number) => DialAssignment | undefined;
  customZoneCount: number;
  zoneBounds: { x: number; w: number }[];
  onSelect: (sel: WorkbenchSelection) => void;
  onSimulate?: (payload: Record<string, unknown>) => void;
}) {
  const gridWidth = columns * CANVAS_KEY_PX + (columns - 1) * CANVAS_GAP;
  const [stripHover, setStripHover] = useState(false);

  return (
    <div
      style={{
        display: "inline-flex",
        flexDirection: "column",
        gap: CANVAS_GAP,
        padding: "var(--space-lg)",
        background: "#0c0c14",
        borderRadius: 16,
        border: "1px solid var(--border-color)",
      }}
    >
      {/* LCD keys */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(${columns}, ${CANVAS_KEY_PX}px)`,
          gridTemplateRows: `repeat(${rows}, ${CANVAS_KEY_PX}px)`,
          gap: CANVAS_GAP,
        }}
      >
        {Array.from({ length: keyCount }, (_, i) => (
          <KeyCell
            key={i}
            index={i}
            image={liveImagesValid ? images[`key_${i}`] : undefined}
            assignment={getAssignment(i, currentPage)}
            selected={selection.kind === "key" && selection.index === i}
            locked={lockedIndexes.has(i)}
            flashing={inputFlash?.kind === "key" && inputFlash.index === i}
            onSelect={() => onSelect({ kind: "key", index: i })}
            onPress={onSimulate ? () => onSimulate({ type: "key", index: i }) : undefined}
          />
        ))}
      </div>

      {/* Info screen flanked by the side touch keys (color-only) */}
      {(hasInfoScreen || touchKeyCount > 0) && (
        <div style={{ display: "flex", alignItems: "center", gap: CANVAS_GAP }}>
          {touchKeyCount > 0 && (
            <CanvasTouchKey
              color={touchKeyColors[0] || (getAssignment(keyCount, currentPage)?.bg_color ?? "")}
              selected={selection.kind === "key" && selection.index === keyCount}
              locked={lockedIndexes.has(keyCount)}
              flashing={inputFlash?.kind === "key" && inputFlash.index === keyCount}
              onSelect={() => onSelect({ kind: "key", index: keyCount })}
              onPress={onSimulate ? () => onSimulate({ type: "key", index: keyCount }) : undefined}
            />
          )}
          {hasInfoScreen && (
            <button
              onClick={() => onSelect({ kind: "screen" })}
              title="Info screen — click to set what it shows"
              style={{
                flex: 1,
                height: Math.max(34, Math.round((gridWidth * 0.55 * 58) / 248)),
                borderRadius: 4,
                overflow: "hidden",
                background: "#000",
                border:
                  selection.kind === "screen"
                    ? "2px solid var(--accent)"
                    : "1px solid #2a2a3a",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                cursor: "pointer",
                color: "#445",
                fontSize: 10,
              }}
            >
              {liveImagesValid && images["screen"] ? (
                <img
                  src={images["screen"]}
                  draggable={false}
                  style={{ height: "100%", display: "block" }}
                  alt=""
                />
              ) : (
                "info screen"
              )}
            </button>
          )}
          {touchKeyCount > 1 && (
            <CanvasTouchKey
              color={touchKeyColors[1] || (getAssignment(keyCount + 1, currentPage)?.bg_color ?? "")}
              selected={selection.kind === "key" && selection.index === keyCount + 1}
              locked={lockedIndexes.has(keyCount + 1)}
              flashing={inputFlash?.kind === "key" && inputFlash.index === keyCount + 1}
              onSelect={() => onSelect({ kind: "key", index: keyCount + 1 })}
              onPress={onSimulate ? () => onSimulate({ type: "key", index: keyCount + 1 }) : undefined}
            />
          )}
        </div>
      )}

      {/* Touch strip */}
      {hasTouchscreen && (
        <div
          onClick={(e) => {
            const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
            const x = Math.round(((e.clientX - rect.left) / rect.width) * 800);
            if (e.shiftKey) {
              onSimulate?.({ type: "touch", x });
              return;
            }
            const hit = zoneBounds.findIndex((b) => x >= b.x && x < b.x + b.w);
            if (customZoneCount > 0) {
              const zone =
                hit >= 0 ? hit : Math.min(
                  customZoneCount - 1,
                  Math.floor(x / (800 / customZoneCount))
                );
              onSelect({ kind: "strip", zone });
            } else if (dialCount > 0) {
              // Default zones are the dials' readouts — clicking one edits
              // that dial.
              const dial =
                hit >= 0 ? Math.min(hit, dialCount - 1)
                  : Math.min(dialCount - 1, Math.floor(x / (800 / dialCount)));
              onSelect({ kind: "dial", index: dial });
            } else {
              onSelect({ kind: "strip", zone: null });
            }
          }}
          onMouseEnter={() => setStripHover(true)}
          onMouseLeave={() => setStripHover(false)}
          title="Touch strip — click to edit, Shift+click to tap it"
          style={{
            width: gridWidth,
            height: Math.round(gridWidth / 8),
            borderRadius: 4,
            overflow: "hidden",
            background: "#000",
            border:
              selection.kind === "strip"
                ? "2px solid var(--accent)"
                : "1px solid #2a2a3a",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#445",
            fontSize: 10,
            position: "relative",
          }}
        >
          {liveImagesValid && images["touchscreen"] ? (
            <img
              src={images["touchscreen"]}
              draggable={false}
              style={{ width: "100%", height: "100%", display: "block" }}
              alt=""
            />
          ) : (
            "touch strip"
          )}
          {/* Hovering reveals the zone boundaries — the strip is editable. */}
          {stripHover && zoneBounds.length > 1 &&
            zoneBounds.slice(1).map((b, i) => (
              <div
                key={i}
                style={{
                  position: "absolute",
                  left: `${(b.x / 800) * 100}%`,
                  top: 4, bottom: 4, width: 1,
                  background: "rgba(255,255,255,0.25)",
                  pointerEvents: "none",
                }}
              />
            ))}
          {/* A real touch on the hardware flashes the touched zone. */}
          {inputFlash?.kind === "touch" && (() => {
            const b =
              zoneBounds.find(
                (zb) => inputFlash.index >= zb.x && inputFlash.index < zb.x + zb.w
              ) ?? { x: 0, w: 800 };
            return (
              <div
                style={{
                  position: "absolute",
                  left: `${(b.x / 800) * 100}%`,
                  width: `${(b.w / 800) * 100}%`,
                  top: 0, bottom: 0,
                  border: "2px solid #f59e0b",
                  borderRadius: 3,
                  pointerEvents: "none",
                }}
              />
            );
          })()}
        </div>
      )}

      {/* Dials */}
      {dialCount > 0 && (
        <div style={{ display: "flex", justifyContent: "space-around" }}>
          {Array.from({ length: dialCount }, (_, i) => {
            const dial = getDial(i);
            const isSelected = selection.kind === "dial" && selection.index === i;
            const hasAssignment =
              !!dial?.label || !!dial?.adjust?.key ||
              !!dial?.cw?.length || !!dial?.ccw?.length || !!dial?.press?.length;
            return (
              <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3 }}>
                <button
                  onClick={(e) => {
                    if (e.shiftKey) {
                      onSimulate?.({ type: "dial_push", index: i });
                      return;
                    }
                    onSelect({ kind: "dial", index: i });
                  }}
                  title={`Dial ${i + 1}${dial?.label ? ` — ${dial.label}` : ""} · click to edit, Shift+click to press`}
                  style={{
                    width: 40,
                    height: 40,
                    borderRadius: "50%",
                    background: "#16161f",
                    border: isSelected
                      ? "2px solid var(--accent)"
                      : "1px solid #2a2a3a",
                    boxShadow:
                      inputFlash?.kind === "dial" && inputFlash.index === i
                        ? "0 0 0 3px #f59e0b"
                        : undefined,
                    cursor: "pointer",
                    position: "relative",
                  }}
                >
                  <div
                    style={{
                      position: "absolute",
                      top: 4,
                      left: "50%",
                      width: 2,
                      height: 9,
                      marginLeft: -1,
                      background: hasAssignment ? "var(--accent)" : "#3a3a4e",
                      borderRadius: 1,
                    }}
                  />
                </button>
                <div style={{ fontSize: 9, color: "#667", maxWidth: 64, textAlign: "center", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {dial?.label || `Dial ${i + 1}`}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* The shell carries the unit's identity */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 6,
          marginTop: 2,
          fontSize: 10,
          color: "#556",
          userSelect: "none",
        }}
      >
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: connected ? "var(--color-success)" : "var(--text-muted)",
          }}
        />
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: Math.max(160, gridWidth - 40) }}>
          {name}
          {model && name !== model ? ` · ${model}` : ""}
          {isVirtual ? " · virtual" : ""}
          {!connected ? " · not connected" : ""}
        </span>
      </div>
    </div>
  );
}

function KeyCell({
  index,
  image,
  assignment,
  selected,
  locked,
  flashing,
  onSelect,
  onPress,
}: {
  index: number;
  image?: string;
  assignment: ButtonAssignment | undefined;
  selected: boolean;
  locked: boolean;
  flashing: boolean;
  onSelect: () => void;
  onPress?: () => void;
}) {
  const [hovered, setHovered] = useState(false);
  const hasAssignment =
    !!assignment?.label || !!assignment?.icon || !!assignment?.bindings?.press;

  return (
    <div
      style={{ position: "relative", width: CANVAS_KEY_PX, height: CANVAS_KEY_PX }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        onClick={(e) => {
          if (e.shiftKey && onPress) {
            onPress();
            return;
          }
          onSelect();
        }}
        title={`Key ${index + 1}${assignment?.label ? ` — ${assignment.label}` : ""} · click to edit${onPress ? ", Shift+click to press" : ""}`}
        style={{
          width: "100%",
          height: "100%",
          padding: 0,
          borderRadius: 8,
          overflow: "hidden",
          border: selected ? "2px solid var(--accent)" : "1px solid #2a2a3a",
          boxShadow: flashing ? "0 0 0 3px #f59e0b" : undefined,
          background: image ? "#000" : assignment?.bg_color || "#101018",
          cursor: "pointer",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 2,
          color: assignment?.text_color || "#778",
        }}
      >
        {image ? (
          <img
            src={image}
            draggable={false}
            style={{ width: "100%", height: "100%", display: "block" }}
            alt=""
          />
        ) : (
          <>
            {!hasAssignment && (
              <span style={{ fontSize: 10, color: "#33334a" }}>{index + 1}</span>
            )}
            {assignment?.icon && (
              <ElementIcon
                name={assignment.icon}
                size={assignment.label ? 22 : 30}
                color={assignment?.text_color || "#99a"}
              />
            )}
            {assignment?.label && (
              <span
                style={{
                  fontSize: 9,
                  maxWidth: CANVAS_KEY_PX - 10,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {assignment.label}
              </span>
            )}
          </>
        )}
      </button>
      {locked && (
        <span
          title="Locked — same on every page"
          style={{
            position: "absolute",
            top: 3,
            left: 3,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 14,
            height: 14,
            borderRadius: 4,
            background: "rgba(12,12,20,0.75)",
            color: "var(--accent)",
            pointerEvents: "none",
          }}
        >
          <Pin size={9} />
        </span>
      )}
      {onPress && hovered && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onPress();
          }}
          title="Press this key"
          style={{
            position: "absolute",
            top: 3,
            right: 3,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 16,
            height: 16,
            borderRadius: 4,
            background: "rgba(12,12,20,0.8)",
            color: "#cdd",
            cursor: "pointer",
          }}
        >
          <Play size={9} />
        </button>
      )}
    </div>
  );
}

function CanvasTouchKey({
  color,
  selected,
  locked,
  flashing,
  onSelect,
  onPress,
}: {
  color: string;
  selected: boolean;
  locked: boolean;
  flashing: boolean;
  onSelect: () => void;
  onPress?: () => void;
}) {
  return (
    <button
      onClick={(e) => {
        if (e.shiftKey && onPress) {
          onPress();
          return;
        }
        onSelect();
      }}
      title={`Touch key · click to edit${onPress ? ", Shift+click to tap" : ""}${locked ? " · locked" : ""}`}
      style={{
        width: 22,
        height: 44,
        borderRadius: 11,
        flexShrink: 0,
        border: selected ? "2px solid var(--accent)" : "1px solid #2a2a3a",
        boxShadow: flashing ? "0 0 0 3px #f59e0b" : undefined,
        background: color && color !== "#000000" ? color : "#101018",
        cursor: "pointer",
      }}
    />
  );
}

// ──── Deck Inspector (the rail's idle state: the unit itself) ────

function DeckInspector({
  serial,
  name,
  model,
  connected,
  isVirtual,
  deckCount,
  isOwn,
  sharedCount,
  brightness,
  fallbackBrightness,
  onRename,
  onBrightness,
  onIdentify,
  onGiveOwnLayout,
  onUseSharedLayout,
  moveTargets,
  onMoveLayoutTo,
  onRemoveVirtual,
  onForget,
  virtualModels,
  deviceLabel,
  onAddVirtual,
  hasTouchscreen = false,
  customZoneCount = 0,
  onOpenStrip,
}: {
  serial: string;
  name: string;
  model: string;
  connected: boolean;
  isVirtual: boolean;
  deckCount: number;
  isOwn: boolean;
  sharedCount: number;
  brightness?: number;
  fallbackBrightness: number;
  onRename: (name: string) => void;
  onBrightness: (level: number | undefined) => void;
  onIdentify?: () => void;
  onGiveOwnLayout?: () => void;
  onUseSharedLayout?: () => void;
  moveTargets: { serial: string; label: string; hasOwn: boolean }[];
  onMoveLayoutTo: (serial: string) => void;
  onRemoveVirtual?: () => void;
  onForget?: () => void;
  virtualModels: string[];
  deviceLabel: string;
  onAddVirtual: (model: string) => void;
  // Touch strip summary row (decks that have one).
  hasTouchscreen?: boolean;
  customZoneCount?: number;
  onOpenStrip?: () => void;
}) {
  const [confirmShared, setConfirmShared] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [confirmForget, setConfirmForget] = useState(false);
  const [moveTarget, setMoveTarget] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [addModel, setAddModel] = useState(virtualModels[0] ?? "");
  const level = typeof brightness === "number" ? brightness : fallbackBrightness;

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
      {/* Identity */}
      <div>
        <input
          value={name}
          placeholder="Name this deck"
          title="Friendly name shown everywhere (e.g. Lectern, Tech Booth)"
          onChange={(e) => onRename(e.target.value)}
          style={{
            width: "100%",
            padding: "var(--space-xs) var(--space-sm)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            background: "var(--bg-surface)",
            color: "var(--text-primary)",
            fontSize: "var(--font-size-base)",
            fontWeight: 600,
          }}
        />
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            marginTop: "var(--space-xs)",
            fontSize: 11,
            color: "var(--text-muted)",
            flexWrap: "wrap",
          }}
        >
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: connected ? "var(--color-success)" : "var(--text-muted)",
            }}
          />
          {connected ? "Connected" : "Not connected"}
          {model && <> · {model}</>}
          {isVirtual && <> · virtual</>}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 2 }}>
          <code style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
            {serial}
          </code>
          <CopyButton value={serial} title="Copy serial" />
        </div>
      </div>

      {/* Touch strip — what it's showing, and the way into the zone editor */}
      {hasTouchscreen && onOpenStrip && (
        <div>
          <label style={panelLabelStyle}>Touch Strip</label>
          <div
            style={{
              display: "flex", alignItems: "center",
              justifyContent: "space-between", gap: "var(--space-sm)",
            }}
          >
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              {customZoneCount > 0
                ? `${customZoneCount} custom zone${customZoneCount === 1 ? "" : "s"}`
                : "One readout per dial"}
            </span>
            <button
              onClick={onOpenStrip}
              style={{
                padding: "2px 10px", borderRadius: "var(--border-radius)",
                background: "var(--bg-hover)", color: "var(--text-secondary)",
                fontSize: 11, cursor: "pointer",
              }}
            >
              Customize…
            </button>
          </div>
        </div>
      )}

      {/* Brightness — a property of this unit, not of any layout */}
      {connected && (
        <div>
          <label style={panelLabelStyle}>Brightness</label>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
            <input
              type="range"
              min={0}
              max={100}
              value={level}
              onChange={(e) => onBrightness(Number(e.target.value))}
              style={{ flex: 1, accentColor: "var(--accent)" }}
            />
            <span style={{ fontSize: "var(--font-size-sm)", width: 38, textAlign: "right" }}>
              {level}%
            </span>
          </div>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
            Just this deck.{" "}
            {typeof brightness === "number" && (
              <button
                onClick={() => onBrightness(undefined)}
                style={{ color: "var(--accent)", cursor: "pointer", background: "none", fontSize: 10 }}
              >
                Use the shared level ({fallbackBrightness}%)
              </button>
            )}
          </div>
        </div>
      )}

      {/* Actions on the unit */}
      {onIdentify && (
        <button onClick={onIdentify} style={deckActionBtnStyle} title="Flash this deck's keys so you can tell which one it is">
          Identify — flash this deck
        </button>
      )}

      {/* Layout ownership (only meaningful with more than one known deck) */}
      {deckCount > 1 && (
        <div>
          <label style={panelLabelStyle}>Layout</label>
          <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", marginBottom: "var(--space-sm)" }}>
            {isOwn ? (
              <>Shows <strong>its own layout</strong>.</>
            ) : (
              <>Shows the <strong>shared layout</strong>{sharedCount > 1 ? ` (with ${sharedCount - 1} other deck${sharedCount > 2 ? "s" : ""})` : ""}.</>
            )}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
            {onGiveOwnLayout && (
              <button
                onClick={onGiveOwnLayout}
                style={deckActionBtnStyle}
                title="Starts as a copy of the shared layout; other decks keep sharing"
              >
                Give this deck its own layout
              </button>
            )}
            {onUseSharedLayout && !confirmShared && (
              <button onClick={() => setConfirmShared(true)} style={deckActionBtnStyle}>
                Use the shared layout instead
              </button>
            )}
            {onUseSharedLayout && confirmShared && (
              <InlineConfirm
                question={`Delete ${name || model || "this deck"}'s own layout and show the shared one? This can't be undone.`}
                onYes={() => {
                  onUseSharedLayout();
                  setConfirmShared(false);
                }}
                onNo={() => setConfirmShared(false)}
              />
            )}
            {isOwn && moveTargets.length > 0 && moveTarget === null && (
              <button
                onClick={() => setMoveTarget(moveTargets[0].serial)}
                style={deckActionBtnStyle}
                title="Re-key this layout (and the deck's name) onto another deck — e.g. a replacement unit"
              >
                Move this layout to another deck...
              </button>
            )}
            {moveTarget !== null && (
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
                <select
                  value={moveTarget}
                  onChange={(e) => setMoveTarget(e.target.value)}
                  style={{
                    padding: "4px 6px",
                    borderRadius: "var(--border-radius)",
                    border: "1px solid var(--border-color)",
                    background: "var(--bg-surface)",
                    color: "var(--text-primary)",
                    fontSize: 12,
                  }}
                >
                  {moveTargets.map((t) => (
                    <option key={t.serial} value={t.serial}>
                      {t.label} ({t.serial}){t.hasOwn ? " — replaces its own layout" : ""}
                    </option>
                  ))}
                </select>
                <div style={{ display: "flex", gap: "var(--space-xs)" }}>
                  <button
                    onClick={() => {
                      if (moveTarget) onMoveLayoutTo(moveTarget);
                      setMoveTarget(null);
                    }}
                    style={{ ...pageMenuConfirmStyle, background: "var(--accent-bg)", color: "var(--text-on-accent)" }}
                  >
                    Move
                  </button>
                  <button onClick={() => setMoveTarget(null)} style={pageMenuConfirmStyle}>
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Virtual / remembered-unit upkeep */}
      {(onRemoveVirtual || onForget) && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
          {onRemoveVirtual && !confirmRemove && (
            <button
              onClick={() => setConfirmRemove(true)}
              style={{ ...deckActionBtnStyle, color: "var(--color-error)" }}
              title="Remove this virtual deck. A layout of its own is kept and can be moved to another deck."
            >
              Remove virtual deck
            </button>
          )}
          {onRemoveVirtual && confirmRemove && (
            <InlineConfirm
              question="Remove this virtual deck?"
              onYes={() => {
                onRemoveVirtual();
                setConfirmRemove(false);
              }}
              onNo={() => setConfirmRemove(false)}
            />
          )}
          {onForget && !confirmForget && (
            <button
              onClick={() => setConfirmForget(true)}
              style={{ ...deckActionBtnStyle, color: "var(--color-error)" }}
              title="Drop this deck's name, settings, and saved layout"
            >
              Forget this deck
            </button>
          )}
          {onForget && confirmForget && (
            <InlineConfirm
              question={`Forget ${name || serial}? Its saved layout is deleted.`}
              onYes={() => {
                onForget();
                setConfirmForget(false);
              }}
              onNo={() => setConfirmForget(false)}
            />
          )}
        </div>
      )}

      {/* Add a virtual unit */}
      {virtualModels.length > 0 && (
        <div style={{ marginTop: "auto" }}>
          {!adding ? (
            <button
              onClick={() => setAdding(true)}
              style={{
                padding: "var(--space-xs) var(--space-sm)",
                borderRadius: "var(--border-radius)",
                border: "1px dashed var(--border-color)",
                background: "transparent",
                color: "var(--text-muted)",
                fontSize: "var(--font-size-sm)",
                cursor: "pointer",
                width: "100%",
              }}
              title="Add a software unit that runs exactly like plugged-in hardware"
            >
              + Virtual {deviceLabel}
            </button>
          ) : (
            <div style={{ display: "flex", gap: "var(--space-xs)" }}>
              <select
                value={addModel}
                onChange={(e) => setAddModel(e.target.value)}
                style={{
                  flex: 1,
                  padding: "4px 6px",
                  borderRadius: "var(--border-radius)",
                  border: "1px solid var(--border-color)",
                  background: "var(--bg-surface)",
                  color: "var(--text-primary)",
                  fontSize: 12,
                }}
              >
                {virtualModels.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
              <button
                onClick={() => {
                  if (addModel) onAddVirtual(addModel);
                  setAdding(false);
                }}
                style={{ ...pageMenuConfirmStyle, background: "var(--accent-bg)", color: "var(--text-on-accent)" }}
              >
                Add
              </button>
              <button onClick={() => setAdding(false)} style={pageMenuConfirmStyle}>
                Cancel
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function InlineConfirm({
  question,
  onYes,
  onNo,
}: {
  question: string;
  onYes: () => void;
  onNo: () => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)", fontSize: 12 }}>
      <span style={{ color: "var(--color-error, #ef4444)" }}>{question}</span>
      <div style={{ display: "flex", gap: "var(--space-xs)" }}>
        <button onClick={onYes} style={pageMenuConfirmStyle}>Yes</button>
        <button onClick={onNo} style={pageMenuConfirmStyle}>No</button>
      </div>
    </div>
  );
}

const deckActionBtnStyle: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-sm)",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  color: "var(--text-secondary)",
  fontSize: "var(--font-size-sm)",
  cursor: "pointer",
  textAlign: "left",
};

// ──── Appearance (layout-scoped default colors) ────

function AppearanceEditor({
  viewConfig,
  onViewChange,
  inherits,
}: {
  viewConfig: Record<string, unknown>;
  onViewChange: (next: Record<string, unknown>) => void;
  inherits: boolean;
}) {
  const setField = (field: string, value: unknown) => {
    const next = { ...viewConfig };
    if (value === undefined || value === "") {
      delete next[field];
    } else {
      next[field] = value;
    }
    onViewChange(next);
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)", maxWidth: 420 }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
        <label style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", width: 130 }}>
          Default key color
        </label>
        <InlineColorPicker
          value={typeof viewConfig.button_color === "string" ? (viewConfig.button_color as string) : ""}
          onChange={(c) => setField("button_color", c || undefined)}
        />
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
        <label style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", width: 130 }}>
          Text color
        </label>
        <InlineColorPicker
          value={typeof viewConfig.text_color === "string" ? (viewConfig.text_color as string) : ""}
          onChange={(c) => setField("text_color", c || undefined)}
        />
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
        Keys without their own colors use these.
        {inherits && " Blank values fall back to the shared layout's colors."}
      </div>
    </div>
  );
}
