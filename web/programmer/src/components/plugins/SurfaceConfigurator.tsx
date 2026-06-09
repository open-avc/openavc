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
import { useState, useCallback, useRef, useEffect } from "react";
import { X, Trash2, ChevronLeft, ChevronRight } from "lucide-react";
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
  // Same binding format as web UI buttons
  bindings?: ButtonBindings;
}

interface DialAdjust {
  key?: string;
  step?: number;
  min?: number;
  max?: number;
}

interface DialAssignment {
  index?: number;
  label?: string;
  adjust?: DialAdjust;
  cw?: Record<string, unknown>[];
  ccw?: Record<string, unknown>[];
  press?: Record<string, unknown>[];
}

interface TouchZone {
  label?: string;
  label_source?: string;
  value_source?: string;
  bg_color?: string;
  text_color?: string;
  x?: number;
  w?: number;
  touch?: Record<string, unknown>[];
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
  const [selectedDeckSerial, setSelectedDeckSerial] = useState<string | null>(null);

  // Live-detected hardware geometry. Surface plugins publish what's actually
  // plugged in (plugin.<id>.rows / columns / connected ...), which beats the
  // static SURFACE_LAYOUT default — so an XL renders as 8x4 even though the
  // plugin's declared default is the Neo's 2x4. Falls back to the static
  // layout when no hardware is connected. With more than one deck attached,
  // the deck picker selects which deck's geometry and config are shown
  // (per-serial state keys: plugin.<id>.<serial>.*).
  const liveState = useConnectionStore((s) => s.liveState);
  const statePrefix = `plugin.${pluginId}.`;
  const deckSerials = String(liveState[`${statePrefix}deck_serials`] ?? "")
    .split(",")
    .filter(Boolean);
  const activeSerial =
    selectedDeckSerial && deckSerials.includes(selectedDeckSerial)
      ? selectedDeckSerial
      : deckSerials[0] ?? null;
  const liveKey = (suffix: string) =>
    activeSerial ? `${statePrefix}${activeSerial}.${suffix}` : `${statePrefix}${suffix}`;

  const deckConnected = Boolean(liveState[liveKey("connected")]);
  const liveRows = Number(liveState[liveKey("rows")] ?? 0);
  const liveColumns = Number(liveState[liveKey("columns")] ?? 0);
  const liveDialCount = deckConnected
    ? Number(liveState[liveKey("dial_count")] ?? 0)
    : 0;
  const liveHasTouchscreen =
    deckConnected && Boolean(liveState[liveKey("has_touchscreen")]);
  const liveKeyCount = Number(liveState[liveKey("key_count")] ?? 0);
  const liveTouchKeyCount = deckConnected
    ? Number(liveState[liveKey("touch_key_count")] ?? 0)
    : 0;
  const liveHasInfoScreen =
    deckConnected && Boolean(liveState[liveKey("has_info_screen")]);
  // Page count is a global plugin setting (config.max_pages, flat config —
  // the runtime reads it un-overridden), beating the static SURFACE_LAYOUT.
  const configMaxPages = Number((config as { max_pages?: unknown }).max_pages);
  const effectiveMaxPages =
    Number.isFinite(configMaxPages) && configMaxPages >= 1
      ? Math.min(100, Math.floor(configMaxPages))
      : staticLayout.max_pages ?? 10;
  const layout: SurfaceLayout = {
    ...(staticLayout.type === "grid" && deckConnected && liveRows > 0 && liveColumns > 0
      ? { ...staticLayout, rows: liveRows, columns: liveColumns }
      : staticLayout),
    max_pages: effectiveMaxPages,
  };

  // Keep the selected page tab valid when the page count is lowered.
  useEffect(() => {
    if (currentPage > effectiveMaxPages - 1) {
      setCurrentPage(effectiveMaxPages - 1);
    }
  }, [currentPage, effectiveMaxPages]);

  // Per-deck config view. A decks[serial] override fully replaces the deck's
  // sections (buttons, dials, ...); a deck without one mirrors the flat
  // config — the runtime resolves it the same way.
  const decksMap =
    (config.decks as Record<string, Record<string, unknown>> | undefined) ?? {};
  const deckOverride = activeSerial ? decksMap[activeSerial] : undefined;
  const isCustomized = deckOverride !== undefined;
  const viewConfig = isCustomized ? deckOverride : config;
  const onViewChange = useCallback(
    (next: Record<string, unknown>) => {
      if (isCustomized && activeSerial) {
        onConfigChange({ ...config, decks: { ...decksMap, [activeSerial]: next } });
      } else {
        onConfigChange(next);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isCustomized, activeSerial, config, onConfigChange]
  );

  const buttons = (viewConfig.buttons as ButtonAssignment[] | undefined) ?? [];
  const dials = (viewConfig.dials as DialAssignment[] | undefined) ?? [];

  // A physical surface button supports a subset of element actions: macro,
  // device command, set-state, and (on paged surfaces) deck-page navigation.
  // value_map needs a continuous input value and script.call is panel-only.
  const supportsPages = !!layout.supports_pages;
  const maxPages = layout.max_pages ?? 10;
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
      onViewChange({ ...viewConfig, buttons: [...existing, updated] });
    },
    [buttons, viewConfig, onViewChange]
  );

  const clearAssignment = useCallback(
    (index: number, page: number) => {
      const filtered = buttons.filter(
        (b) => !(b.index === index && (b.page ?? 0) === page)
      );
      onViewChange({ ...viewConfig, buttons: filtered });
    },
    [buttons, viewConfig, onViewChange]
  );

  const getDial = useCallback(
    (index: number): DialAssignment | undefined =>
      dials.find((d) => d.index === index),
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

  const selectedDialIndex =
    selectedControl !== null && selectedControl.startsWith("dial:")
      ? parseInt(selectedControl.slice(5))
      : null;

  switch (layout.type) {
    case "grid":
      return (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-lg)" }}>
          {/* Deck picker — only with more than one deck attached */}
          {deckSerials.length > 1 && (
            <DeckPicker
              serials={deckSerials}
              activeSerial={activeSerial}
              onSelect={(serial) => {
                setSelectedDeckSerial(serial);
                setSelectedControl(null);
              }}
              statePrefix={statePrefix}
              pluginId={pluginId}
              decksMap={decksMap}
              config={config}
              onConfigChange={onConfigChange}
            />
          )}
          {/* Per-deck overrides for the global scalar settings (customized decks only) */}
          {isCustomized && (
            <DeckScalarSettings viewConfig={viewConfig} onViewChange={onViewChange} />
          )}
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
              {/* Dials (detected on the connected hardware) */}
              {liveDialCount > 0 && (
                <DialRow
                  count={liveDialCount}
                  selectedControl={selectedControl}
                  onSelectControl={setSelectedControl}
                  getDial={getDial}
                />
              )}
              {/* Color-only touch keys (indexed after the LCD keys) */}
              {liveTouchKeyCount > 0 && liveKeyCount > 0 && (
                <TouchKeyRow
                  keyCount={liveKeyCount}
                  touchKeyCount={liveTouchKeyCount}
                  currentPage={currentPage}
                  selectedControl={selectedControl}
                  onSelectControl={setSelectedControl}
                  getAssignment={getAssignment}
                />
              )}
            </div>
            {selectedDialIndex !== null && (
              <DialAssignmentPanel
                dialIndex={selectedDialIndex}
                dial={getDial(selectedDialIndex)}
                allowedActions={allowedActions}
                navigateOptions={navigateOptions}
                onUpdate={(updates) => updateDial(selectedDialIndex, updates)}
                onClear={() => clearDial(selectedDialIndex)}
                onClose={() => setSelectedControl(null)}
              />
            )}
            {selectedControl !== null && selectedDialIndex === null && (
              <ControlAssignmentPanel
                controlId={selectedControl}
                allowedActions={allowedActions}
                navigateOptions={navigateOptions}
                colorOnly={
                  liveTouchKeyCount > 0 &&
                  liveKeyCount > 0 &&
                  parseInt(selectedControl) >= liveKeyCount
                }
                keyCount={liveKeyCount}
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
          {liveHasTouchscreen && (
            <TouchscreenZonesEditor
              config={viewConfig}
              onConfigChange={onViewChange}
              allowedActions={allowedActions}
              navigateOptions={navigateOptions}
            />
          )}
          {liveHasInfoScreen && (
            <InfoStripEditor config={viewConfig} onConfigChange={onViewChange} />
          )}
          {layout.supports_pages && (
            <AutoPageEditor
              layout={layout}
              config={viewConfig}
              onConfigChange={onViewChange}
            />
          )}
          <BrightnessEditor config={viewConfig} onConfigChange={onViewChange} />
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
            layout={layout}
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
      return <RoutingMatrix layout={layout} pluginId={pluginId} config={config} onRequestConfigRefresh={onRequestConfigRefresh} />;

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
}) {
  const project = useProjectStore((s) => s.project);

  const currentBindings: ButtonBindings = assignment?.bindings ?? {};
  const controlIndex = parseInt(controlId);
  const title = colorOnly
    ? `Touch Key ${controlIndex - keyCount + 1}`
    : `Button ${controlIndex + 1}`;

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
          {title}
        </h4>
        <button onClick={onClose} style={{ color: "var(--text-muted)", cursor: "pointer" }}>
          <X size={14} />
        </button>
      </div>

      {/* Icon (LCD keys only) */}
      {!colorOnly && (
        <div>
          <label style={panelLabelStyle}>Icon</label>
          <IconPicker
            value={assignment?.icon ?? ""}
            onChange={(icon) => onUpdate({ icon: icon || undefined })}
          />
        </div>
      )}

      {/* Default Colors */}
      <div>
        <label style={panelLabelStyle}>{colorOnly ? "Key Color" : "Default Colors"}</label>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
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
      </div>

      {/* Shared binding editor — same component the web UI Builder uses */}
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
          showToggleLabels={!colorOnly}
          allowedActions={allowedActions}
          navigateOptions={navigateOptions}
        />
      ) : (
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Loading project...</div>
      )}

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

// ──── Deck Picker (multiple decks attached) ────

function DeckPicker({
  serials,
  activeSerial,
  onSelect,
  statePrefix,
  pluginId,
  decksMap,
  config,
  onConfigChange,
}: {
  serials: string[];
  activeSerial: string | null;
  onSelect: (serial: string) => void;
  statePrefix: string;
  pluginId: string;
  decksMap: Record<string, Record<string, unknown>>;
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
}) {
  const liveState = useConnectionStore((s) => s.liveState);
  const [confirmRevert, setConfirmRevert] = useState(false);
  const isCustomized = activeSerial ? decksMap[activeSerial] !== undefined : false;

  // The per-deck config sections an override replaces (mirrors the runtime).
  const DECK_SECTIONS = [
    "buttons", "auto_page", "dials", "touchscreen",
    "info_strip", "auto_brightness", "idle_dim",
  ];

  const customizeDeck = () => {
    if (!activeSerial) return;
    // Start the override as a copy of the main config's sections so the deck
    // keeps its current behavior until it's edited.
    const copy: Record<string, unknown> = {};
    for (const section of DECK_SECTIONS) {
      if (config[section] !== undefined) {
        copy[section] = JSON.parse(JSON.stringify(config[section]));
      }
    }
    onConfigChange({ ...config, decks: { ...decksMap, [activeSerial]: copy } });
  };

  const revertDeck = () => {
    if (!activeSerial) return;
    const next = { ...decksMap };
    delete next[activeSerial];
    onConfigChange({ ...config, decks: next });
    setConfirmRevert(false);
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-sm)",
        flexWrap: "wrap",
      }}
    >
      {serials.map((serial) => {
        const model = String(liveState[`${statePrefix}${serial}.model`] ?? "Deck");
        const custom = decksMap[serial] !== undefined;
        const isActive = serial === activeSerial;
        return (
          <button
            key={serial}
            onClick={() => {
              setConfirmRevert(false);
              onSelect(serial);
            }}
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-start",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: isActive ? "var(--accent-dim)" : "var(--bg-surface)",
              border: isActive
                ? "2px solid var(--accent)"
                : "1px solid var(--border-color)",
              cursor: "pointer",
            }}
          >
            <span style={{ fontSize: "var(--font-size-sm)", fontWeight: isActive ? 600 : 400 }}>
              {model}
            </span>
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
              {serial} — {custom ? "custom layout" : "mirrors main config"}
            </span>
          </button>
        );
      })}

      {activeSerial && (
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", marginLeft: "auto" }}>
          <button
            onClick={() => api.emitContextAction(pluginId, "identify_deck", { serial: activeSerial })}
            title="Flash this deck's keys so you can tell which one it is"
            style={{
              padding: "var(--space-xs) var(--space-sm)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              fontSize: "var(--font-size-sm)",
              cursor: "pointer",
            }}
          >
            Identify
          </button>
          {!isCustomized && (
            <button
              onClick={customizeDeck}
              title="Give this deck its own button/dial assignments instead of mirroring the main config"
              style={{
                padding: "var(--space-xs) var(--space-sm)",
                borderRadius: "var(--border-radius)",
                background: "var(--bg-hover)",
                fontSize: "var(--font-size-sm)",
                cursor: "pointer",
              }}
            >
              Customize separately
            </button>
          )}
          {isCustomized && !confirmRevert && (
            <button
              onClick={() => setConfirmRevert(true)}
              title="Drop this deck's custom assignments and mirror the main config again"
              style={{
                padding: "var(--space-xs) var(--space-sm)",
                borderRadius: "var(--border-radius)",
                background: "var(--bg-hover)",
                fontSize: "var(--font-size-sm)",
                cursor: "pointer",
              }}
            >
              Mirror main config
            </button>
          )}
          {confirmRevert && (
            <span style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", fontSize: 12 }}>
              <span style={{ color: "var(--color-error, #ef4444)" }}>
                Delete this deck's custom assignments?
              </span>
              <button
                onClick={revertDeck}
                style={{
                  padding: "2px 8px", borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)", fontSize: 12, cursor: "pointer",
                }}
              >
                Yes
              </button>
              <button
                onClick={() => setConfirmRevert(false)}
                style={{
                  padding: "2px 8px", borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)", fontSize: 12, cursor: "pointer",
                }}
              >
                No
              </button>
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// ──── Per-Deck Scalar Settings (customized decks only) ────
//
// The runtime resolves brightness/button_color/text_color per deck via the
// decks[serial] override first, then the flat plugin settings. This row lets
// a customized deck author those overrides; blank = inherit the main setting.

function DeckScalarSettings({
  viewConfig,
  onViewChange,
}: {
  viewConfig: Record<string, unknown>;
  onViewChange: (next: Record<string, unknown>) => void;
}) {
  const brightness = viewConfig.brightness;
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
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-lg)",
        flexWrap: "wrap",
        padding: "var(--space-sm) var(--space-md)",
        border: "1px solid var(--border-color)",
        borderRadius: "var(--border-radius)",
        background: "var(--bg-surface)",
      }}
    >
      <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)" }}>
        This deck's settings
      </span>
      <label style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", fontSize: 12, color: "var(--text-muted)" }}>
        Brightness
        <input
          type="number"
          min={0}
          max={100}
          value={typeof brightness === "number" ? brightness : ""}
          placeholder="main"
          onChange={(e) =>
            setField(
              "brightness",
              e.target.value === "" ? undefined : Math.max(0, Math.min(100, Number(e.target.value)))
            )
          }
          style={{
            width: 64, padding: "4px 6px",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            background: "var(--bg-surface)", color: "var(--text-primary)",
            fontSize: "var(--font-size-sm)",
          }}
        />
        %
      </label>
      <label style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", fontSize: 12, color: "var(--text-muted)" }}>
        Button color
        <InlineColorPicker
          value={typeof viewConfig.button_color === "string" ? viewConfig.button_color : ""}
          onChange={(c) => setField("button_color", c || undefined)}
        />
      </label>
      <label style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", fontSize: 12, color: "var(--text-muted)" }}>
        Text color
        <InlineColorPicker
          value={typeof viewConfig.text_color === "string" ? viewConfig.text_color : ""}
          onChange={(c) => setField("text_color", c || undefined)}
        />
      </label>
      <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
        Blank values use the main plugin settings.
      </span>
    </div>
  );
}

// ──── Dial Row (rotary encoders under the key grid) ────

function DialRow({
  count,
  selectedControl,
  onSelectControl,
  getDial,
}: {
  count: number;
  selectedControl: string | null;
  onSelectControl: (id: string) => void;
  getDial: (index: number) => DialAssignment | undefined;
}) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-around",
        gap: "var(--space-sm)",
        marginTop: "var(--space-sm)",
        padding: "var(--space-md)",
        background: "var(--bg-base)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
      }}
    >
      {Array.from({ length: count }, (_, i) => {
        const dial = getDial(i);
        const isSelected = selectedControl === `dial:${i}`;
        const hasAssignment =
          !!dial?.label || !!dial?.adjust?.key ||
          !!dial?.cw?.length || !!dial?.ccw?.length || !!dial?.press?.length;
        return (
          <div
            key={i}
            style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}
          >
            <button
              onClick={() => onSelectControl(`dial:${i}`)}
              title={dial?.label ? `Dial ${i + 1}: ${dial.label}` : `Dial ${i + 1} (unassigned)`}
              style={{
                width: 44,
                height: 44,
                borderRadius: "50%",
                background: isSelected
                  ? "var(--accent-dim)"
                  : hasAssignment
                    ? "var(--bg-elevated)"
                    : "var(--bg-surface)",
                border: isSelected
                  ? "2px solid var(--accent)"
                  : "1px solid var(--border-color)",
                cursor: "pointer",
                position: "relative",
              }}
            >
              {/* Knob indicator line */}
              <div
                style={{
                  position: "absolute",
                  top: 4,
                  left: "50%",
                  width: 2,
                  height: 10,
                  marginLeft: -1,
                  background: hasAssignment ? "var(--accent)" : "var(--text-muted)",
                  borderRadius: 1,
                }}
              />
            </button>
            <div style={{ fontSize: 9, color: "var(--text-muted)", maxWidth: 60, textAlign: "center", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {dial?.label || `Dial ${i + 1}`}
            </div>
          </div>
        );
      })}
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
}: {
  dialIndex: number;
  dial: DialAssignment | undefined;
  onUpdate: (updates: Partial<DialAssignment>) => void;
  onClear: () => void;
  onClose: () => void;
  allowedActions?: string[];
  navigateOptions?: { value: string; label: string }[];
}) {
  const project = useProjectStore((s) => s.project);
  const adjust = dial?.adjust ?? {};

  const updateAdjust = (patch: Partial<DialAdjust>) => {
    const next = { ...adjust, ...patch };
    // Strip empty fields so a cleared adjust disappears from the config
    if (!next.key) {
      onUpdate({ adjust: undefined });
    } else {
      onUpdate({ adjust: next });
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

function TouchscreenZonesEditor({
  config,
  onConfigChange,
  allowedActions,
  navigateOptions,
}: {
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
  allowedActions?: string[];
  navigateOptions?: { value: string; label: string }[];
}) {
  const project = useProjectStore((s) => s.project);
  const touchscreen = (config.touchscreen as { zones?: TouchZone[] } | undefined) ?? {};
  const zones = touchscreen.zones ?? [];
  const [expandedZone, setExpandedZone] = useState<number | null>(null);

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

  if (!project) return null;

  return (
    <div style={{ maxWidth: 560 }}>
      <h4 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600, marginBottom: 4 }}>
        Touchscreen
      </h4>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
        By default the touchscreen shows one zone per dial with its label and
        live value. Add custom zones to take over the strip — zones split it
        evenly, and tapping a zone runs its actions.
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

// ──── Touch Key Row (color-only keys indexed after the LCD keys) ────

function TouchKeyRow({
  keyCount,
  touchKeyCount,
  currentPage,
  selectedControl,
  onSelectControl,
  getAssignment,
}: {
  keyCount: number;
  touchKeyCount: number;
  currentPage: number;
  selectedControl: string | null;
  onSelectControl: (id: string) => void;
  getAssignment: (index: number, page?: number) => ButtonAssignment | undefined;
}) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        gap: "var(--space-sm)",
        marginTop: "var(--space-sm)",
        padding: "var(--space-sm) var(--space-md)",
        background: "var(--bg-base)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
      }}
    >
      {Array.from({ length: touchKeyCount }, (_, i) => {
        const index = keyCount + i;
        const assignment = getAssignment(index, currentPage);
        const isSelected = selectedControl === String(index);
        const hasAssignment = !!assignment?.bg_color || !!assignment?.bindings?.press;
        return (
          <button
            key={index}
            onClick={() => onSelectControl(String(index))}
            title={`Touch Key ${i + 1}${hasAssignment ? "" : " (unassigned)"}`}
            style={{
              flex: 1,
              height: 22,
              borderRadius: 11,
              background: isSelected
                ? "var(--accent-dim)"
                : assignment?.bg_color || "var(--bg-surface)",
              border: isSelected
                ? "2px solid var(--accent)"
                : "1px solid var(--border-color)",
              cursor: "pointer",
              fontSize: 9,
              color: "var(--text-muted)",
            }}
          >
            {hasAssignment ? "" : `T${i + 1}`}
          </button>
        );
      })}
    </div>
  );
}

// ──── Info Strip Editor (secondary info screen) ────

function InfoStripEditor({
  config,
  onConfigChange,
}: {
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
}) {
  const infoStrip =
    (config.info_strip as { source?: string; key?: string; text?: string; label?: string } | undefined) ?? undefined;
  const source = infoStrip ? (infoStrip.source ?? "state") : "";

  const update = (patch: Record<string, unknown>) => {
    onConfigChange({ ...config, info_strip: { ...(infoStrip ?? {}), ...patch } });
  };

  const inputStyle: React.CSSProperties = {
    width: "100%", padding: "4px 6px",
    borderRadius: "var(--border-radius)",
    border: "1px solid var(--border-color)",
    background: "var(--bg-surface)", color: "var(--text-primary)",
    fontSize: "var(--font-size-sm)",
  };

  return (
    <div style={{ maxWidth: 560 }}>
      <h4 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600, marginBottom: 4 }}>
        Info Screen
      </h4>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
        The small screen between the touch keys can show a live state value or
        static text.
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)", maxWidth: 320 }}>
        <div>
          <label style={panelHintStyle}>Show</label>
          <select
            value={source}
            onChange={(e) => {
              const next = e.target.value;
              if (!next) {
                const { info_strip: _drop, ...rest } = config;
                onConfigChange(rest);
              } else {
                update({ source: next });
              }
            }}
            style={inputStyle}
          >
            <option value="">Nothing</option>
            <option value="state">A live state value</option>
            <option value="text">Static text</option>
          </select>
        </div>

        {source === "state" && (
          <div>
            <label style={panelHintStyle}>State key</label>
            <VariableKeyPicker
              value={infoStrip?.key ?? ""}
              onChange={(key) => update({ key })}
              placeholder="Pick a state key to display..."
            />
          </div>
        )}
        {source === "text" && (
          <div>
            <label style={panelHintStyle}>Text</label>
            <input
              type="text"
              value={infoStrip?.text ?? ""}
              onChange={(e) => update({ text: e.target.value })}
              placeholder="Text shown on the screen"
              style={inputStyle}
            />
          </div>
        )}
        {source && (
          <div>
            <label style={panelHintStyle}>Heading (optional, shown above)</label>
            <input
              type="text"
              value={infoStrip?.label ?? ""}
              onChange={(e) => update({ label: e.target.value || undefined })}
              placeholder="e.g. Room Temp"
              style={inputStyle}
            />
          </div>
        )}
      </div>
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
      <h4 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600, marginBottom: 4 }}>
        Automatic Paging
      </h4>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
        Switch pages automatically when system state changes. Rules are checked in order; the first match wins.
      </div>

      {rules.length === 0 && (
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
          No automatic paging rules yet.
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
                  <option key={p} value={p}>Page {p + 1}</option>
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
}: {
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
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
      <h4 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600, marginBottom: 4 }}>
        Brightness
      </h4>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
        Change the deck brightness automatically. Rules are checked in order;
        the first match wins, and with no match the base brightness from the
        plugin settings applies.
      </div>

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
