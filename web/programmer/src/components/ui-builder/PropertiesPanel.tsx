import { useState } from "react";
import { ChevronDown, ChevronRight, Trash2, Undo2, Link, Palette } from "lucide-react";
import { ConfirmDialog } from "../shared/ConfirmDialog";
import type { UIElement, UIPage, ProjectConfig, OverlayConfig, PageBackground, MasterElement } from "../../api/types";
import { BasicProperties } from "./PropertySections/BasicProperties";
import { LayoutProperties } from "./PropertySections/LayoutProperties";
import { StyleProperties } from "./PropertySections/StyleProperties";
import { BindingProperties } from "./PropertySections/BindingProperties";
import { VisibilityProperties } from "./PropertySections/VisibilityProperties";
import { AssetPicker } from "./AssetPicker";

interface ThemeSummary {
  id: string;
  name: string;
  preview_colors: string[];
  source: string;
}

interface PropertiesPanelProps {
  element: UIElement | null;
  selectedElementIds?: string[];
  masterElement?: MasterElement | null;
  page: UIPage | null;
  project: ProjectConfig;
  themeDefaults?: Record<string, Record<string, unknown>>;
  themes?: ThemeSummary[];
  onThemeChange?: (themeId: string) => void;
  onOpenThemeStudio?: () => void;
  onChange: (elementId: string, patch: Partial<UIElement>) => void;
  onRenameElement?: (oldId: string, newId: string) => void;
  onPageChange?: (patch: Partial<UIPage>) => void;
  onMasterElementChange?: (elementId: string, patch: Partial<MasterElement>) => void;
  onDemoteMaster?: (elementId: string) => void;
  onDeleteMaster?: (elementId: string) => void;
}

export function PropertiesPanel({
  element,
  selectedElementIds,
  masterElement,
  page,
  project,
  themeDefaults,
  themes,
  onThemeChange,
  onOpenThemeStudio,
  onChange,
  onRenameElement,
  onPageChange,
  onMasterElementChange,
  onDemoteMaster,
  onDeleteMaster,
}: PropertiesPanelProps) {
  // Master element selected — show master element properties
  if (masterElement && page) {
    return (
      <MasterElementProperties
        masterElement={masterElement}
        page={page}
        project={project}
        themeDefaults={themeDefaults}
        onChange={onMasterElementChange || (() => {})}
        onRename={onRenameElement}
        onDemote={onDemoteMaster || (() => {})}
        onDelete={onDeleteMaster || (() => {})}
      />
    );
  }

  // Multi-select mode: show summary and common editable properties
  const multiSelectCount = selectedElementIds?.length ?? (element ? 1 : 0);
  if (multiSelectCount > 1 && page) {
    const selectedElements = (selectedElementIds ?? [])
      .map((eid) => page.elements.find((el) => el.id === eid))
      .filter((el): el is UIElement => !!el);

    const applyStyleToAll = (stylePatch: Record<string, unknown>) => {
      for (const el of selectedElements) {
        onChange(el.id, { style: { ...el.style, ...stylePatch } });
      }
    };

    // Get a common value for a style prop (returns undefined if mixed)
    const getCommonStyle = (prop: string): unknown => {
      const values = selectedElements.map((el) => el.style[prop]);
      const first = values[0];
      return values.every((v) => v === first) ? first : undefined;
    };

    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          padding: "var(--space-md)",
          gap: "var(--space-sm)",
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
          Multi-Select
        </div>
        <div style={{ fontSize: 13, color: "var(--text-primary)", fontWeight: 500 }}>
          {multiSelectCount} elements selected
        </div>
        <div style={{ fontSize: 11, color: "var(--text-muted)", lineHeight: 1.4, marginBottom: 4 }}>
          Changes below apply to all selected elements.
        </div>

        {/* Common style properties */}
        {([
          { key: "font_size", label: "Font Size", type: "number" as const, unit: "px" },
          { key: "padding", label: "Padding", type: "number" as const, unit: "px" },
          { key: "border_radius", label: "Radius", type: "number" as const, unit: "px" },
          { key: "bg_color", label: "Background", type: "color" as const, unit: undefined },
          { key: "text_color", label: "Text Color", type: "color" as const, unit: undefined },
        ]).map(({ key, label, type, unit }) => {
          const common = getCommonStyle(key);
          return (
            <div key={key} style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
              <label style={{ fontSize: 11, color: "var(--text-muted)", minWidth: 70, flexShrink: 0 }}>{label}</label>
              {type === "number" ? (
                <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <input
                    type="number"
                    value={common != null ? Number(common) : ""}
                    placeholder={common === undefined ? "mixed" : ""}
                    onChange={(e) => applyStyleToAll({ [key]: e.target.value ? Number(e.target.value) : undefined })}
                    style={{ width: 60, padding: "2px 4px", fontSize: 11, borderRadius: 3, border: "1px solid var(--border-color)", background: "var(--bg-primary)", color: "var(--text-primary)", textAlign: "center" }}
                  />
                  {unit && <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{unit}</span>}
                </div>
              ) : (
                <input
                  type="color"
                  value={typeof common === "string" ? common : "#333333"}
                  onChange={(e) => applyStyleToAll({ [key]: e.target.value })}
                  style={{ width: 28, height: 22, padding: 0, border: "1px solid var(--border-color)", borderRadius: 3, cursor: "pointer" }}
                />
              )}
            </div>
          );
        })}
      </div>
    );
  }

  if (!element || !page) {
    const currentThemeId = project?.ui?.settings?.theme_id || "dark-default";

    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          overflow: "auto",
          padding: "var(--space-sm)",
          gap: "var(--space-xs)",
        }}
      >
        {/* Theme Section */}
        {themes && themes.length > 0 && onThemeChange && (
          <ThemeSection
            themes={themes}
            currentThemeId={currentThemeId}
            onThemeChange={onThemeChange}
            onOpenThemeStudio={onOpenThemeStudio}
          />
        )}

        {/* Page Properties */}
        {page && onPageChange ? (
          <PageProperties page={page} onChange={onPageChange} />
        ) : (
          <div
            style={{
              color: "var(--text-muted)",
              fontSize: "var(--font-size-sm)",
              padding: "var(--space-lg)",
              textAlign: "center",
            }}
          >
            Select an element to edit its properties
          </div>
        )}
      </div>
    );
  }

  const handleChange = (patch: Partial<UIElement>) => {
    onChange(element.id, patch);
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        overflow: "auto",
        padding: "var(--space-sm)",
        gap: "var(--space-xs)",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "var(--space-xs) var(--space-xs)",
        }}
      >
        <span style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
          textTransform: "uppercase",
          letterSpacing: "0.5px",
          fontWeight: 600,
        }}>
          Properties
        </span>
        {onOpenThemeStudio && (
          <button
            onClick={onOpenThemeStudio}
            style={{
              display: "flex", alignItems: "center", gap: 3,
              padding: "1px 6px", borderRadius: 3,
              background: "transparent", border: "1px solid var(--border-color)",
              color: "var(--text-muted)", fontSize: 10, cursor: "pointer",
            }}
            title="Open Theme Studio"
          >
            <Palette size={10} /> Theme
          </button>
        )}
      </div>

      <Section title="Basic" defaultOpen>
        <BasicProperties
          element={element}
          pages={project.ui.pages}
          macros={(project.macros || []).map((m) => ({ id: m.id, name: m.name }))}
          onChange={handleChange}
          onRename={onRenameElement ? (newId) => onRenameElement(element.id, newId) : undefined}
        />
      </Section>

      <Section title="Layout" defaultOpen>
        <LayoutProperties
          element={element}
          gridConfig={page.grid}
          onChange={handleChange}
        />
      </Section>

      <Section title="Style" defaultOpen>
        {/* Theme override indicator (12.7) */}
        {themeDefaults?.[element.type] && (() => {
          const td = themeDefaults[element.type];
          const overrideKeys = ["bg_color", "text_color", "border_color", "font_size"].filter(
            (k) => element.style[k] != null && td[k] != null && element.style[k] !== td[k]
          );
          if (overrideKeys.length === 0) return null;
          return (
            <div style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              padding: "4px 8px", marginBottom: 6, borderRadius: 4,
              background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.2)",
              fontSize: 11,
            }}>
              <span style={{ color: "#f59e0b", fontWeight: 500 }}>
                Overrides theme ({overrideKeys.length})
              </span>
              <button
                onClick={() => {
                  const reset: Record<string, unknown> = {};
                  for (const k of overrideKeys) reset[k] = undefined;
                  handleChange({ style: { ...element.style, ...reset } });
                }}
                style={{
                  padding: "1px 6px", borderRadius: 3, fontSize: 10,
                  background: "transparent", border: "1px solid rgba(245,158,11,0.3)",
                  color: "#f59e0b", cursor: "pointer",
                }}
              >
                <Undo2 size={10} style={{ verticalAlign: "middle", marginRight: 2 }} />
                Reset to theme
              </button>
            </div>
          );
        })()}
        <StyleProperties element={element} onChange={handleChange} themeDefaults={themeDefaults?.[element.type]} />
      </Section>

      <Section title="Bindings" defaultOpen highlight icon={<Link size={12} style={{ color: "var(--accent)" }} />}>
        <BindingProperties
          element={element}
          project={project}
          onChange={handleChange}
        />
      </Section>

      <Section title="Visibility">
        <VisibilityProperties
          element={element}
          onChange={handleChange}
        />
      </Section>
    </div>
  );
}

function MasterElementProperties({
  masterElement,
  page,
  project,
  themeDefaults,
  onChange,
  onRename,
  onDemote,
  onDelete,
}: {
  masterElement: MasterElement;
  page: UIPage;
  project: ProjectConfig;
  themeDefaults?: Record<string, Record<string, unknown>>;
  onChange: (elementId: string, patch: Partial<MasterElement>) => void;
  onRename?: (oldId: string, newId: string) => void;
  onDemote: (elementId: string) => void;
  onDelete: (elementId: string) => void;
}) {
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const pagesValue = masterElement.pages;
  const isAllPages = pagesValue === "*";
  const selectedPageIds = Array.isArray(pagesValue) ? pagesValue : [];

  const handlePagesMode = (mode: "all" | "specific") => {
    if (mode === "all") {
      onChange(masterElement.id, { pages: "*" });
    } else {
      onChange(masterElement.id, { pages: [page.id] });
    }
  };

  const handleTogglePage = (pageId: string) => {
    const current = Array.isArray(pagesValue) ? pagesValue : [];
    const next = current.includes(pageId)
      ? current.filter(id => id !== pageId)
      : [...current, pageId];
    // Ensure at least one page is selected
    if (next.length === 0) return;
    onChange(masterElement.id, { pages: next });
  };

  const handleElementChange = (patch: Partial<MasterElement>) => {
    onChange(masterElement.id, patch);
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        overflow: "auto",
        padding: "var(--space-sm)",
        gap: "var(--space-xs)",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          fontSize: "var(--font-size-sm)",
          color: "#9C27B0",
          textTransform: "uppercase",
          letterSpacing: "0.5px",
          fontWeight: 600,
          padding: "var(--space-xs)",
        }}
      >
        <span
          style={{
            display: "inline-block",
            padding: "1px 6px",
            borderRadius: 3,
            background: "rgba(156,39,176,0.15)",
            fontSize: 10,
          }}
        >
          Master
        </span>
        Properties
      </div>

      <Section title="Basic" defaultOpen>
        <BasicProperties
          element={masterElement}
          pages={project.ui.pages}
          macros={(project.macros || []).map((m) => ({ id: m.id, name: m.name }))}
          onChange={handleElementChange}
          onRename={onRename ? (newId) => onRename(masterElement.id, newId) : undefined}
        />
      </Section>

      <Section title="Layout" defaultOpen>
        <LayoutProperties
          element={masterElement}
          gridConfig={page.grid}
          onChange={handleElementChange}
        />
      </Section>

      <Section title="Style" defaultOpen>
        <StyleProperties element={masterElement} onChange={handleElementChange} themeDefaults={themeDefaults?.[masterElement.type]} />
      </Section>

      <Section title="Bindings" defaultOpen highlight icon={<Link size={12} style={{ color: "var(--accent)" }} />}>
        <BindingProperties
          element={masterElement}
          project={project}
          onChange={handleElementChange}
        />
      </Section>

      <Section title="Visibility">
        <VisibilityProperties
          element={masterElement}
          onChange={handleElementChange}
        />
      </Section>

      <Section title="Pages" defaultOpen>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
          <FieldRow label="Show on">
            <select
              value={isAllPages ? "all" : "specific"}
              onChange={(e) => handlePagesMode(e.target.value as "all" | "specific")}
              style={{ flex: 1 }}
            >
              <option value="all">All pages</option>
              <option value="specific">Specific pages</option>
            </select>
          </FieldRow>

          {!isAllPages && (
            <div style={{ display: "flex", flexDirection: "column", gap: 2, paddingLeft: 4 }}>
              {project.ui.pages.map((p) => (
                <label
                  key={p.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--space-sm)",
                    fontSize: "var(--font-size-sm)",
                    color: "var(--text-secondary)",
                    cursor: "pointer",
                    padding: "2px 4px",
                    borderRadius: 3,
                  }}
                >
                  <input
                    type="checkbox"
                    checked={selectedPageIds.includes(p.id)}
                    onChange={() => handleTogglePage(p.id)}
                  />
                  {p.name}
                  {p.page_type && p.page_type !== "page" && (
                    <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                      ({p.page_type})
                    </span>
                  )}
                </label>
              ))}
            </div>
          )}
        </div>
      </Section>

      {/* Actions */}
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)", padding: "var(--space-sm) 0" }}>
        <button
          onClick={() => onDemote(masterElement.id)}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "var(--space-sm)",
            padding: "6px 12px",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-hover)",
            border: "1px solid var(--border-color)",
            cursor: "pointer",
            fontSize: "var(--font-size-sm)",
            color: "var(--text-primary)",
          }}
          title="Move this master element back to the current page as a regular element"
        >
          <Undo2 size={13} />
          Move to Current Page
        </button>
        <button
          onClick={() => setShowDeleteConfirm(true)}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "var(--space-sm)",
            padding: "6px 12px",
            borderRadius: "var(--border-radius)",
            background: "rgba(244,67,54,0.1)",
            border: "1px solid rgba(244,67,54,0.3)",
            cursor: "pointer",
            fontSize: "var(--font-size-sm)",
            color: "var(--color-error)",
          }}
        >
          <Trash2 size={13} />
          Delete Master Element
        </button>
      </div>

      <div
        style={{
          fontSize: 11,
          color: "var(--text-muted)",
          padding: "var(--space-xs)",
          lineHeight: 1.4,
        }}
      >
        Master elements appear on multiple pages. Changes here apply everywhere.
      </div>

      {showDeleteConfirm && (
        <ConfirmDialog
          title="Delete Master Element"
          message={`Delete master element "${masterElement.id}"? This cannot be undone.`}
          confirmLabel="Delete"
          destructive
          onConfirm={() => {
            onDelete(masterElement.id);
            setShowDeleteConfirm(false);
          }}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      )}
    </div>
  );
}

function PageProperties({
  page,
  onChange,
}: {
  page: UIPage;
  onChange: (patch: Partial<UIPage>) => void;
}) {
  const bg = page.background || {};
  const overlay = page.overlay || {};
  const pageType = page.page_type || "page";
  const isOverlay = pageType === "overlay";
  const isSidebar = pageType === "sidebar";
  const isOverlayOrSidebar = isOverlay || isSidebar;

  const updateBg = (patch: Partial<PageBackground>) => {
    onChange({ background: { ...bg, ...patch } });
  };

  const updateGradient = (patch: Record<string, unknown>) => {
    const grad = bg.gradient || { type: "linear", angle: 180, from: "rgba(0,0,0,0.8)", to: "rgba(0,0,0,0.4)" };
    updateBg({ gradient: { ...grad, ...patch } as PageBackground["gradient"] });
  };

  const updateOverlay = (patch: Partial<OverlayConfig>) => {
    onChange({ overlay: { ...overlay, ...patch } });
  };

  // Preserve grid across page-type switches — Aaron explicitly wants this.
  // The previous behavior reset grid to 4×4 / 4×8 silently, which clamped
  // existing elements off the grid with no path back.
  const handleTypeChange = (newType: string) => {
    if (newType === "page") {
      onChange({ page_type: undefined as unknown as string, overlay: undefined });
    } else if (newType === "overlay") {
      onChange({
        page_type: "overlay",
        overlay: {
          width: overlay.width ?? 400,
          height: overlay.height ?? 300,
          position: overlay.position ?? "center",
          backdrop: overlay.backdrop ?? "dim",
          dismiss_on_backdrop: overlay.dismiss_on_backdrop ?? true,
          animation: overlay.animation ?? "fade",
        },
      });
    } else if (newType === "sidebar") {
      onChange({
        page_type: "sidebar",
        overlay: {
          width: overlay.width ?? 320,
          side: overlay.side ?? "right",
          backdrop: overlay.backdrop ?? "dim",
          dismiss_on_backdrop: overlay.dismiss_on_backdrop ?? true,
          animation: overlay.animation ?? "slide-left",
        },
      });
    }
  };

  const hasGradient = !!(bg.gradient?.from && bg.gradient?.to);

  const sectionHeader = (text: string, topGap = false): React.ReactNode => (
    <div
      style={{
        fontSize: "var(--font-size-sm)",
        color: "var(--text-secondary)",
        textTransform: "uppercase",
        letterSpacing: "0.5px",
        fontWeight: 600,
        padding: "var(--space-xs)",
        marginTop: topGap ? "var(--space-sm)" : undefined,
      }}
    >
      {text}
    </div>
  );

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        overflow: "auto",
        padding: "var(--space-sm)",
        gap: "var(--space-sm)",
      }}
    >
      {sectionHeader(isSidebar ? "Sidebar Properties" : isOverlay ? "Overlay Properties" : "Page Properties")}

      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
        <FieldRow label="Page Type">
          <select
            value={pageType}
            onChange={(e) => handleTypeChange(e.target.value)}
            style={{ flex: 1, padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
          >
            <option value="page">Page</option>
            <option value="overlay">Overlay</option>
            <option value="sidebar">Sidebar</option>
          </select>
        </FieldRow>
      </div>

      {isOverlayOrSidebar && (
        <>
          {sectionHeader(isSidebar ? "Sidebar" : "Overlay", true)}
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
            <FieldRow label="Width">
              <input
                type="number"
                value={overlay.width ?? (isSidebar ? 320 : 400)}
                onChange={(e) => updateOverlay({ width: Number(e.target.value) })}
                min={100}
                style={{ flex: 1 }}
              />
              <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
            </FieldRow>

            {isOverlay && (
              <FieldRow label="Height">
                <input
                  type="number"
                  value={overlay.height ?? 300}
                  onChange={(e) => updateOverlay({ height: Number(e.target.value) })}
                  min={100}
                  style={{ flex: 1 }}
                />
                <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
              </FieldRow>
            )}

            {isOverlay && (
              <FieldRow label="Position">
                <select
                  value={overlay.position || "center"}
                  onChange={(e) => updateOverlay({ position: e.target.value })}
                  style={{ flex: 1 }}
                >
                  <option value="center">Center</option>
                  <option value="top">Top</option>
                  <option value="bottom">Bottom</option>
                </select>
              </FieldRow>
            )}

            {isSidebar && (
              <FieldRow label="Side">
                <select
                  value={overlay.side || "right"}
                  onChange={(e) => updateOverlay({ side: e.target.value })}
                  style={{ flex: 1 }}
                >
                  <option value="right">Right</option>
                  <option value="left">Left</option>
                </select>
              </FieldRow>
            )}

            <FieldRow label="Backdrop">
              <select
                value={overlay.backdrop || "dim"}
                onChange={(e) => updateOverlay({ backdrop: e.target.value })}
                style={{ flex: 1 }}
              >
                <option value="dim">Dim</option>
                <option value="blur">Blur</option>
                <option value="none">None</option>
              </select>
            </FieldRow>

            <FieldRow label="Animation">
              <select
                value={overlay.animation || (isSidebar ? "slide-left" : "fade")}
                onChange={(e) => updateOverlay({ animation: e.target.value })}
                style={{ flex: 1 }}
              >
                <option value="fade">Fade</option>
                <option value="scale">Scale</option>
                <option value="slide-up">Slide Up</option>
                <option value="slide-down">Slide Down</option>
                {isSidebar && <option value="slide-left">Slide Left</option>}
                {isSidebar && <option value="slide-right">Slide Right</option>}
                <option value="none">None</option>
              </select>
            </FieldRow>

            <FieldRow label="Tap to Close">
              <input
                type="checkbox"
                checked={overlay.dismiss_on_backdrop !== false}
                onChange={(e) => updateOverlay({ dismiss_on_backdrop: e.target.checked })}
              />
            </FieldRow>
          </div>
        </>
      )}

      {sectionHeader("Grid", true)}
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
        <FieldRow label="Grid Cols">
          <input
            type="number"
            value={page.grid.columns}
            onChange={(e) =>
              onChange({ grid: { ...page.grid, columns: Math.max(1, Number(e.target.value)) } })
            }
            min={1}
            max={24}
            style={{ flex: 1 }}
          />
        </FieldRow>

        <FieldRow label="Grid Rows">
          <input
            type="number"
            value={page.grid.rows}
            onChange={(e) =>
              onChange({ grid: { ...page.grid, rows: Math.max(1, Number(e.target.value)) } })
            }
            min={1}
            max={24}
            style={{ flex: 1 }}
          />
        </FieldRow>

        <FieldRow label="Grid Gap">
          <input
            type="number"
            value={page.grid_gap ?? ""}
            onChange={(e) =>
              onChange({ grid_gap: e.target.value ? Number(e.target.value) : undefined })
            }
            placeholder="theme"
            min={0}
            max={24}
            style={{ flex: 1 }}
          />
          <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
        </FieldRow>
        <div style={{ fontSize: 10, color: "var(--text-muted)", fontStyle: "italic", padding: "0 0 0 76px" }}>
          Space between grid cells. Leave blank for theme default.
        </div>
      </div>

      {sectionHeader("Background", true)}
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
        <FieldRow label="Color">
          <input
            type="color"
            value={bg.color || "#1a1a2e"}
            onChange={(e) => updateBg({ color: e.target.value })}
            style={{ width: 32, height: 24, padding: 0, border: "1px solid var(--border-color)" }}
          />
          <input
            type="text"
            value={bg.color || ""}
            onChange={(e) => updateBg({ color: e.target.value })}
            placeholder="Theme default"
            style={{ flex: 1, fontSize: 11 }}
          />
          {bg.color && (
            <button
              onClick={() => updateBg({ color: undefined })}
              style={{ fontSize: 10, padding: "2px 4px" }}
              title="Clear"
            >
              ✕
            </button>
          )}
        </FieldRow>

        <FieldRow label="Image">
          <div style={{ flex: 1 }}>
            <AssetPicker
              value={bg.image || ""}
              onChange={(v) => updateBg({ image: v || undefined })}
            />
          </div>
        </FieldRow>

        {bg.image && (
          <>
            <FieldRow label="Opacity">
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={bg.image_opacity ?? 1}
                onChange={(e) => updateBg({ image_opacity: Number(e.target.value) })}
                style={{ flex: 1 }}
              />
              <span style={{ fontSize: 10, width: 28, textAlign: "right", color: "var(--text-muted)" }}>
                {Math.round((bg.image_opacity ?? 1) * 100)}%
              </span>
            </FieldRow>

            <FieldRow label="Size">
              <select
                value={bg.image_size || "cover"}
                onChange={(e) => updateBg({ image_size: e.target.value })}
                style={{ flex: 1 }}
              >
                <option value="cover">Cover</option>
                <option value="contain">Contain</option>
                <option value="stretch">Stretch</option>
              </select>
            </FieldRow>

            <FieldRow label="Position">
              <select
                value={bg.image_position || "center"}
                onChange={(e) => updateBg({ image_position: e.target.value })}
                style={{ flex: 1 }}
              >
                <option value="center">Center</option>
                <option value="top">Top</option>
                <option value="bottom">Bottom</option>
                <option value="left">Left</option>
                <option value="right">Right</option>
              </select>
            </FieldRow>
          </>
        )}

        <FieldRow label="Gradient">
          <input
            type="checkbox"
            checked={hasGradient}
            onChange={(e) => {
              if (e.target.checked) {
                updateBg({
                  gradient: { type: "linear", angle: 180, from: "rgba(0,0,0,0.8)", to: "rgba(0,0,0,0.4)" },
                });
              } else {
                updateBg({ gradient: undefined });
              }
            }}
          />
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Overlay gradient</span>
        </FieldRow>

        {hasGradient && (
          <>
            <FieldRow label="From">
              <input
                type="text"
                value={bg.gradient?.from || ""}
                onChange={(e) => updateGradient({ from: e.target.value })}
                placeholder="rgba(0,0,0,0.8)"
                style={{ flex: 1, fontSize: 11 }}
              />
            </FieldRow>
            <FieldRow label="To">
              <input
                type="text"
                value={bg.gradient?.to || ""}
                onChange={(e) => updateGradient({ to: e.target.value })}
                placeholder="rgba(0,0,0,0.4)"
                style={{ flex: 1, fontSize: 11 }}
              />
            </FieldRow>
            <FieldRow label="Angle">
              <input
                type="number"
                value={bg.gradient?.angle ?? 180}
                onChange={(e) => updateGradient({ angle: Number(e.target.value) })}
                min={0}
                max={360}
                style={{ flex: 1 }}
              />
              <span style={{ fontSize: 10, color: "var(--text-muted)" }}>deg</span>
            </FieldRow>
          </>
        )}
      </div>

      <div
        style={{
          fontSize: 11,
          color: "var(--text-muted)",
          padding: "var(--space-xs)",
          lineHeight: 1.4,
        }}
      >
        {isOverlayOrSidebar
          ? `Navigate to this ${isSidebar ? "sidebar" : "overlay"} using a page_nav element with target "${page.id}", or use $back to dismiss.`
          : "Page background is visible behind all elements. Use a gradient overlay on top of an image to keep text readable."}
      </div>
    </div>
  );
}

function FieldRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-sm)",
      }}
    >
      <label
        style={{
          width: 72,
          flexShrink: 0,
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
        }}
      >
        {label}
      </label>
      {children}
    </div>
  );
}

function Section({
  title,
  defaultOpen,
  icon,
  highlight,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  icon?: React.ReactNode;
  highlight?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen ?? true);

  return (
    <div
      style={{
        border: `1px solid ${highlight ? "var(--accent)" : "var(--border-color)"}`,
        borderRadius: "var(--border-radius)",
        overflow: "hidden",
      }}
    >
      <button
        onClick={() => setOpen(!open)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          width: "100%",
          padding: "6px 8px",
          fontSize: "var(--font-size-sm)",
          fontWeight: 600,
          background: highlight ? "rgba(33,150,243,0.06)" : "var(--bg-surface)",
          color: "var(--text-primary)",
          textAlign: "left",
        }}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {icon}
        {title}
      </button>
      {open && (
        <div
          style={{
            padding: "var(--space-sm)",
            background: "var(--bg-elevated)",
          }}
        >
          {children}
        </div>
      )}
    </div>
  );
}

// --- Theme Section (shown when no element selected) ---
// Thin picker. Click a card to switch theme. Click "Open Theme Studio…" for the full editor.

function ThemeSection({
  themes,
  currentThemeId,
  onThemeChange,
  onOpenThemeStudio,
}: {
  themes: ThemeSummary[];
  currentThemeId: string;
  onThemeChange: (themeId: string) => void;
  onOpenThemeStudio?: () => void;
}) {
  return (
    <>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "var(--space-xs)",
        }}
      >
        <span
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            textTransform: "uppercase",
            letterSpacing: "0.5px",
            fontWeight: 600,
          }}
        >
          Theme
        </span>
        {onOpenThemeStudio && (
          <button
            onClick={onOpenThemeStudio}
            title="Edit, duplicate, or save themes with live preview"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              padding: "3px 8px",
              borderRadius: 4,
              background: "var(--bg-hover)",
              border: "1px solid var(--border-color)",
              cursor: "pointer",
              fontSize: 11,
              color: "var(--text-secondary)",
            }}
          >
            <Palette size={11} /> Open Studio
          </button>
        )}
      </div>

      {/* Theme picker grid — quick-switch only */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(100px, 1fr))",
          gap: 6,
          marginBottom: 8,
        }}
      >
        {themes.map((t) => {
          const isSelected = currentThemeId === t.id;
          return (
            <div
              key={t.id}
              onClick={() => onThemeChange(t.id)}
              title={`Switch to "${t.name}"`}
              style={{
                padding: 6,
                borderRadius: 6,
                border: isSelected ? "2px solid var(--accent)" : "1px solid var(--border-color)",
                background: isSelected ? "var(--accent-dim, rgba(33,150,243,0.12))" : "var(--bg-surface)",
                cursor: "pointer",
                textAlign: "center",
              }}
            >
              <div style={{ display: "flex", gap: 2, justifyContent: "center", marginBottom: 3 }}>
                {(t.preview_colors || []).slice(0, 4).map((c, i) => (
                  <div
                    key={i}
                    style={{
                      width: 14,
                      height: 14,
                      borderRadius: 3,
                      backgroundColor: c,
                      border: "1px solid rgba(128,128,128,0.3)",
                    }}
                  />
                ))}
              </div>
              <div
                style={{
                  fontSize: 10,
                  fontWeight: isSelected ? 600 : 400,
                  color: "var(--text-primary)",
                }}
              >
                {t.name}
              </div>
            </div>
          );
        })}
      </div>

      <div
        style={{
          height: 1,
          background: "var(--border-color)",
          margin: "4px 0 8px",
        }}
      />
    </>
  );
}
