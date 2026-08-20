import { useState } from "react";
import { ChevronDown, ChevronRight, Trash2, Undo2, Link } from "lucide-react";
import { ConfirmDialog } from "../shared/ConfirmDialog";
import { NumericInput } from "../shared/NumericInput";
import type { UIElement, UIPage, ProjectConfig, OverlayConfig, PageBackground, MasterElement } from "../../api/types";
import { BasicProperties } from "./PropertySections/BasicProperties";
import { CustomControlConfig } from "./PropertySections/CustomControlConfig";
import { GrantEditor } from "./PropertySections/GrantEditor";
import { FieldRow } from "./PropertySections/FieldRow";
import { LayoutProperties } from "./PropertySections/LayoutProperties";
import { StyleProperties } from "./PropertySections/StyleProperties";
import { BindingProperties } from "./PropertySections/BindingProperties";
import { stylesheetClassNames } from "./customCssHelpers";
import { AssetPicker } from "./AssetPicker";
import { InlineColorPicker } from "../shared/InlineColorPicker";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import {
  getPlacement,
  withPlacement,
  pageSnap,
  referenceBox,
  referenceParentBox,
  containerChoices,
  displayStyleValue,
  storeStyleValue,
  layoutOrientation,
  masterPlacement,
  isHiddenInLayout,
  resolveHidden,
  withHidden,
  layoutById,
  sliderThemeDefaults,
} from "./uiBuilderHelpers";

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
  onChange: (elementId: string, patch: Partial<UIElement>) => void;
  onRenameElement?: (oldId: string, newId: string) => void;
  onPageChange?: (patch: Partial<UIPage>) => void;
  /** Moving an element into or out of a container. Not a plain patch — the
   *  box has to be re-expressed against the new parent or it teleports. */
  onReparent?: (elementId: string, parentId: string | null) => void;
  onMasterElementChange?: (elementId: string, patch: Partial<MasterElement>) => void;
  onDemoteMaster?: (elementId: string) => void;
  onDeleteMaster?: (elementId: string) => void;
  /** Opens the project stylesheet editor, offered where an element has no
   *  classes to pick from yet. */
  onOpenStylesheet?: () => void;
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
  onChange,
  onRenameElement,
  onPageChange,
  onReparent,
  onMasterElementChange,
  onDemoteMaster,
  onDeleteMaster,
  onOpenStylesheet,
}: PropertiesPanelProps) {
  // Typed coordinates belong to the layout being authored, not to whichever
  // one happens to be primary. Read before the early returns below so the hook
  // order never changes.
  const activeLayoutId = useUIBuilderStore((s) => s.activeLayoutId);
  // What the project stylesheet defines, so the Style section can offer its
  // classes instead of asking the author to remember them.
  const stylesheetClasses = stylesheetClassNames(project?.ui?.custom_css);

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
        stylesheetClasses={stylesheetClasses}
        onOpenStylesheet={onOpenStylesheet}
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

    // Common value for a style prop. `mixed` distinguishes "the elements
    // disagree" from "they all share the same (possibly unset) value" — the
    // latter must show the inherited theme value, not a false "mixed".
    const getCommonStyle = (prop: string): { common: unknown; mixed: boolean } => {
      const values = selectedElements.map((el) => el.style[prop]);
      const first = values[0];
      const mixed = !values.every((v) => v === first);
      return { common: mixed ? undefined : first, mixed };
    };

    // The theme default for a prop, but only when every selected element (which
    // may be different types) resolves to the same default — otherwise there's
    // no single effective value to show as a placeholder.
    const getCommonThemeDefault = (prop: string): unknown => {
      const defs = selectedElements.map((el) => themeDefaults?.[el.type]?.[prop]);
      const first = defs[0];
      return defs.every((d) => d === first) ? first : undefined;
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
            letterSpacing: "var(--tracking-wide)",
            fontWeight: "var(--font-weight-semibold)",
          }}
        >
          Multi-Select
        </div>
        <div style={{ fontSize: "var(--font-size-base)", color: "var(--text-primary)", fontWeight: "var(--font-weight-medium)" }}>
          {multiSelectCount} elements selected
        </div>
        <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", lineHeight: "var(--line-tight)", marginBottom: "var(--space-xs)" }}>
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
          const { common, mixed } = getCommonStyle(key);
          const themeDefault = getCommonThemeDefault(key);
          return (
            <div key={key} style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
              <label style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", minWidth: 70, flexShrink: 0 }}>{label}</label>
              {type === "number" ? (
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                  <input
                    type="number"
                    // Measurements are shown in px and stored in rem, the same
                    // boundary the single-element editor keeps.
                    value={common != null ? (displayStyleValue(key, Number(common)) as number) : ""}
                    // "mixed" only when the elements genuinely disagree; a shared
                    // unset value shows the inherited theme default instead.
                    placeholder={
                      mixed
                        ? "mixed"
                        : themeDefault != null
                        ? String(displayStyleValue(key, Number(themeDefault)))
                        : ""
                    }
                    onChange={(e) =>
                      applyStyleToAll({
                        [key]: e.target.value
                          ? storeStyleValue(key, Number(e.target.value))
                          : undefined,
                      })
                    }
                    style={{ width: 60, padding: "var(--space-2xs) var(--space-xs)", fontSize: "var(--font-size-xs)", borderRadius: "var(--border-radius)", border: "1px solid var(--border-color)", background: "var(--bg-primary)", color: "var(--text-primary)", textAlign: "center" }}
                  />
                  {unit && <span style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)" }}>{unit}</span>}
                </div>
              ) : (
                // InlineColorPicker (not a native input) so multi-select can show
                // the inherited theme color and clear back to it, instead of
                // fabricating #333333 and force-writing hex to every element on
                // open. Empty value ⇒ inherit; a pick/clear applies to all.
                <InlineColorPicker clearable
                  value={typeof common === "string" ? common : ""}
                  placeholder={mixed ? "" : String(themeDefault ?? "")}
                  onChange={(v) => applyStyleToAll({ [key]: v || undefined })}
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
              lineHeight: "var(--line-relaxed)",
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
          letterSpacing: "var(--tracking-wide)",
          fontWeight: "var(--font-weight-semibold)",
        }}>
          Properties
        </span>
      </div>

      <Section title="Basic" defaultOpen>
        <BasicProperties
          element={element}
          project={project}
          pages={project.ui.pages}
          macros={(project.macros || []).map((m) => ({ id: m.id, name: m.name }))}
          placement={getPlacement(page, element.id, activeLayoutId)}
          onChangePlacement={(placement) =>
            onPageChange?.({
              layouts: withPlacement(page, element.id, placement, activeLayoutId).layouts,
            })
          }
          onChange={handleChange}
          onRename={onRenameElement ? (newId) => onRenameElement(element.id, newId) : undefined}
        />
      </Section>

      <Section title="Layout" defaultOpen>
        <LayoutProperties
          element={element}
          placement={getPlacement(page, element.id, activeLayoutId)}
          containers={containerChoices(page, element.id)}
          parentPx={referenceParentBox(page, element.id, activeLayoutId)}
          orientation={layoutOrientation(page, activeLayoutId)}
          theme={sliderThemeDefaults(project)}
          onChangePlacement={(placement) => {
            // Geometry lives in the page's layout, so a typed coordinate is a
            // page change, not an element change.
            const next = withPlacement(page, element.id, placement, activeLayoutId);
            onPageChange?.({ layouts: next.layouts });
          }}
          onChange={handleChange}
          onChangeParent={(parentId) => onReparent?.(element.id, parentId)}
          hidden={(() => {
            const active = layoutById(page, activeLayoutId);
            const own = isHiddenInLayout(page, element.id, activeLayoutId);
            const resolved = resolveHidden(page, activeLayoutId).has(element.id);
            // Hidden, but not by the layout being authored: it came down the
            // inherits chain, and the checkbox here cannot take it back.
            const inheritedFrom =
              resolved && !own
                ? (page.layouts ?? []).find(
                    (l) => l.id !== active?.id && (l.hidden ?? []).includes(element.id),
                  )?.orientation ?? "inherited"
                : null;
            return {
              value: resolved,
              layoutLabel: active?.orientation ?? "this layout",
              inheritedFrom,
              onChange: (next: boolean) => {
                const page2 = withHidden(page, element.id, next, activeLayoutId);
                onPageChange?.({ layouts: page2.layouts });
              },
            };
          })()}
        />
      </Section>

      <Section title="Style" defaultOpen>
        {/* Theme override indicator (12.7) */}
        {themeDefaults?.[element.type] && (() => {
          const td = themeDefaults[element.type];
          const overrideKeys = Object.keys(td).filter(
            (k) => element.style[k] != null && element.style[k] !== td[k]
          );
          if (overrideKeys.length === 0) return null;
          return (
            <div style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              padding: "var(--space-xs) var(--space-sm)", marginBottom: "var(--space-sm)", borderRadius: "var(--border-radius)",
              background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.2)",
              fontSize: "var(--font-size-xs)",
            }}>
              <span style={{ color: "#f59e0b", fontWeight: "var(--font-weight-medium)" }}>
                Overrides theme ({overrideKeys.length})
              </span>
              <button
                onClick={() => {
                  const reset: Record<string, unknown> = {};
                  for (const k of overrideKeys) reset[k] = undefined;
                  handleChange({ style: { ...element.style, ...reset } });
                }}
                style={{
                  padding: "var(--space-2xs) var(--space-sm)", borderRadius: "var(--border-radius)", fontSize: "var(--font-size-2xs)",
                  background: "transparent", border: "1px solid rgba(245,158,11,0.3)",
                  color: "#f59e0b", cursor: "pointer",
                }}
              >
                <Undo2 size={10} style={{ verticalAlign: "middle", marginRight: "var(--space-2xs)" }} />
                Reset to theme
              </button>
            </div>
          );
        })()}
        <StyleProperties
          element={element}
          onChange={handleChange}
          themeDefaults={themeDefaults?.[element.type]}
          stylesheetClasses={stylesheetClasses}
          customCss={project?.ui?.custom_css ?? ""}
          onOpenStylesheet={onOpenStylesheet}
        />
      </Section>

      <Section title="Bindings" defaultOpen highlight icon={<Link size={12} style={{ color: "var(--accent)" }} />}>
        <BindingProperties
          element={element}
          project={project}
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
  stylesheetClasses,
  onOpenStylesheet,
}: {
  masterElement: MasterElement;
  page: UIPage;
  project: ProjectConfig;
  themeDefaults?: Record<string, Record<string, unknown>>;
  onChange: (elementId: string, patch: Partial<MasterElement>) => void;
  onRename?: (oldId: string, newId: string) => void;
  onDemote: (elementId: string) => void;
  onDelete: (elementId: string) => void;
  stylesheetClasses: string[];
  onOpenStylesheet?: () => void;
}) {
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  // A master has no page layout of its own — its boxes are keyed by orientation
  // — so the arrangement being authored on this page is what says which key an
  // edit lands on.
  const activeLayoutId = useUIBuilderStore((s) => s.activeLayoutId);
  const masterOrientation = layoutOrientation(page, activeLayoutId);
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
          letterSpacing: "var(--tracking-wide)",
          fontWeight: "var(--font-weight-semibold)",
          padding: "var(--space-xs)",
        }}
      >
        <span
          style={{
            display: "inline-block",
            padding: "var(--space-2xs) var(--space-sm)",
            borderRadius: "var(--border-radius)",
            background: "rgba(156,39,176,0.15)",
            fontSize: "var(--font-size-2xs)",
          }}
        >
          Master
        </span>
        Properties
      </div>

      <Section title="Basic" defaultOpen>
        <BasicProperties
          element={masterElement}
          project={project}
          pages={project.ui.pages}
          macros={(project.macros || []).map((m) => ({ id: m.id, name: m.name }))}
          onChange={handleElementChange}
          onRename={onRename ? (newId) => onRename(masterElement.id, newId) : undefined}
        />
      </Section>

      <Section title="Layout" defaultOpen>
        <LayoutProperties
          element={masterElement}
          // A master's box is a percentage of the VIEWPORT, keyed by
          // orientation, so it is valid on every page it appears on whatever
          // those pages are arranged like. Which key you are editing follows the
          // arrangement being authored: turn the canvas portrait and you are
          // placing the master for portrait glass, not silently rewriting the
          // landscape one.
          placement={
            masterPlacement(masterElement, masterOrientation) ?? { x: 0, y: 0, w: 25, h: 12.5 }
          }
          containers={[]}
          // A master is a percentage of the viewport, so its parent IS the
          // reference screen — turned, when the arrangement is portrait.
          parentPx={referenceBox(masterOrientation)}
          orientation={masterOrientation}
          onChangePlacement={(placement) =>
            handleElementChange({
              placements: { ...masterElement.placements, [masterOrientation]: placement },
            })
          }
          onChange={handleElementChange}
          // A master is not on any page's element tree, so it has no container
          // to be in and the picker above it is empty.
          onChangeParent={() => undefined}
        />
        {masterOrientation === "portrait" && !masterElement.placements?.portrait && (
          <div style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)", marginTop: "var(--space-xs)" }}>
            No portrait position of its own yet, so it is showing the landscape one. Move it
            and it gets one.
          </div>
        )}
      </Section>

      <Section title="Style" defaultOpen>
        <StyleProperties
          element={masterElement}
          onChange={handleElementChange}
          themeDefaults={themeDefaults?.[masterElement.type]}
          stylesheetClasses={stylesheetClasses}
          customCss={project?.ui?.custom_css ?? ""}
          onOpenStylesheet={onOpenStylesheet}
        />
      </Section>

      <Section title="Bindings" defaultOpen highlight icon={<Link size={12} style={{ color: "var(--accent)" }} />}>
        <BindingProperties
          element={masterElement}
          project={project}
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
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2xs)", paddingLeft: "var(--space-xs)" }}>
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
                    padding: "var(--space-2xs) var(--space-xs)",
                    borderRadius: "var(--border-radius)",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={selectedPageIds.includes(p.id)}
                    onChange={() => handleTogglePage(p.id)}
                  />
                  {p.name}
                  {p.page_type && p.page_type !== "page" && (
                    <span style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)" }}>
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
            padding: "var(--space-sm) var(--space-md)",
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
            padding: "var(--space-sm) var(--space-md)",
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
          fontSize: "var(--font-size-xs)",
          color: "var(--text-muted)",
          padding: "var(--space-xs)",
          lineHeight: "var(--line-tight)",
        }}
      >
        Master elements appear on multiple pages. Changes here apply everywhere.
      </div>

      {showDeleteConfirm && (
        <ConfirmDialog
          title="Delete Master Element"
          message={`Delete master element "${masterElement.id}"? You can undo with Ctrl+Z.`}
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
  //
  // The box defaults are PERCENTAGES of the viewport (0.8.0). They were still
  // the pre-0.8.0 pixel numbers here, so converting a page to an overlay gave
  // it a 400% x 300% box — createPage has had the right ones all along.
  const handleTypeChange = (newType: string) => {
    if (newType === "page") {
      onChange({ page_type: undefined as unknown as string, overlay: undefined });
    } else if (newType === "overlay") {
      onChange({
        page_type: "overlay",
        overlay: {
          width: overlay.width ?? 31.25,
          height: overlay.height ?? 37.5,
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
          width: overlay.width ?? 25,
          side: overlay.side ?? "right",
          backdrop: overlay.backdrop ?? "dim",
          dismiss_on_backdrop: overlay.dismiss_on_backdrop ?? true,
          animation: overlay.animation ?? "slide-left",
        },
      });
    }
  };

  const isCustom = page.render_mode === "custom";
  const keptElements = page.elements?.length ?? 0;

  const hasGradient = !!(bg.gradient?.from && bg.gradient?.to);

  const sectionHeader = (text: string, topGap = false): React.ReactNode => (
    <div
      style={{
        fontSize: "var(--font-size-sm)",
        color: "var(--text-secondary)",
        textTransform: "uppercase",
        letterSpacing: "var(--tracking-wide)",
        fontWeight: "var(--font-weight-semibold)",
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
            style={{ flex: 1, padding: "var(--space-xs) var(--space-sm)", fontSize: "var(--font-size-sm)" }}
          >
            <option value="page">Page</option>
            <option value="overlay">Overlay</option>
            <option value="sidebar">Sidebar</option>
          </select>
        </FieldRow>

        <FieldRow label="Contents">
          <select
            value={isCustom ? "custom" : "elements"}
            onChange={(e) => onChange({ render_mode: e.target.value as "elements" | "custom" })}
            style={{ flex: 1, padding: "var(--space-xs) var(--space-sm)", fontSize: "var(--font-size-sm)" }}
          >
            <option value="elements">Controls you place here</option>
            <option value="custom">A page you wrote yourself</option>
          </select>
        </FieldRow>
      </div>

      {isCustom && (
        <>
          {sectionHeader("Your Page", true)}
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
            <CustomControlConfig
              file={page.custom_file || ""}
              config={page.custom_config || {}}
              onChange={onChange}
              label="Page"
              settingsLabel="Settings passed to the page (JSON):"
            />
            {keptElements > 0 && (
              <div style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)", padding: "var(--space-2xs) 0" }}>
                {keptElements} control{keptElements === 1 ? "" : "s"} on this page
                {keptElements === 1 ? " is" : " are"} not drawn while it shows your own
                page. Switch Contents back to show {keptElements === 1 ? "it" : "them"} again.
              </div>
            )}
            <GrantEditor grant={page.grant} onChange={(grant) => onChange({ grant })} />
          </div>
        </>
      )}

      {isOverlayOrSidebar && (
        <>
          {sectionHeader(isSidebar ? "Sidebar" : "Overlay", true)}
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
            {/* Stored as percentages of the viewport (the runtime's _pct
                fallbacks are the placeholders) — the px labels this carried
                before 0.8.0 wrote pixel-scale numbers into a percent field. */}
            <FieldRow label="Width">
              <NumericInput
                value={overlay.width}
                onCommit={(v) => updateOverlay({ width: v })}
                allowEmpty
                min={5}
                max={100}
                placeholder={isSidebar ? "25" : "31.25"}
                style={{ flex: 1 }}
              />
              <span style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)" }}>% of screen</span>
            </FieldRow>

            {isOverlay && (
              <FieldRow label="Height">
                <NumericInput
                  value={overlay.height}
                  onCommit={(v) => updateOverlay({ height: v })}
                  allowEmpty
                  min={5}
                  max={100}
                  placeholder="37.5"
                  style={{ flex: 1 }}
                />
                <span style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)" }}>% of screen</span>
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

      {sectionHeader("Snapping", true)}
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
        <FieldRow label="Snap">
          <label style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", flex: 1 }}>
            <input
              type="checkbox"
              checked={pageSnap(page).enabled}
              onChange={(e) =>
                onChange({ snap: { ...pageSnap(page), enabled: e.target.checked } })
              }
            />
            <span style={{ fontSize: "var(--font-size-xs)" }}>Snap while dragging</span>
          </label>
        </FieldRow>

        <FieldRow label="Columns">
          <NumericInput
            value={Math.round(100 / pageSnap(page).x)}
            onCommit={(v) => {
              if (v !== undefined) onChange({ snap: { ...pageSnap(page), x: 100 / v } });
            }}
            integer
            min={1}
            max={48}
            style={{ flex: 1 }}
          />
        </FieldRow>

        <FieldRow label="Rows">
          <NumericInput
            value={Math.round(100 / pageSnap(page).y)}
            onCommit={(v) => {
              if (v !== undefined) onChange({ snap: { ...pageSnap(page), y: 100 / v } });
            }}
            integer
            min={1}
            max={48}
            style={{ flex: 1 }}
          />
        </FieldRow>
        <div style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)", fontStyle: "italic", padding: "0 0 0 76px" }}>
          A ruler, not a container. Changing it, or switching it off, moves nothing
          that is already placed. Hold Alt while dragging to ignore it for one move.
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
            style={{ flex: 1, fontSize: "var(--font-size-xs)" }}
          />
          {bg.color && (
            <button
              onClick={() => updateBg({ color: undefined })}
              style={{ fontSize: "var(--font-size-2xs)", padding: "var(--space-2xs) var(--space-xs)" }}
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
              <span style={{ fontSize: "var(--font-size-2xs)", width: 28, textAlign: "right", color: "var(--text-muted)" }}>
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
          <span style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)" }}>Overlay gradient</span>
        </FieldRow>

        {hasGradient && (
          <>
            <FieldRow label="From">
              <input
                type="text"
                value={bg.gradient?.from || ""}
                onChange={(e) => updateGradient({ from: e.target.value })}
                placeholder="rgba(0,0,0,0.8)"
                style={{ flex: 1, fontSize: "var(--font-size-xs)" }}
              />
            </FieldRow>
            <FieldRow label="To">
              <input
                type="text"
                value={bg.gradient?.to || ""}
                onChange={(e) => updateGradient({ to: e.target.value })}
                placeholder="rgba(0,0,0,0.4)"
                style={{ flex: 1, fontSize: "var(--font-size-xs)" }}
              />
            </FieldRow>
            <FieldRow label="Angle">
              <NumericInput
                value={bg.gradient?.angle ?? 180}
                onCommit={(v) => {
                  if (v !== undefined) updateGradient({ angle: v });
                }}
                min={0}
                max={360}
                style={{ flex: 1 }}
              />
              <span style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)" }}>deg</span>
            </FieldRow>
          </>
        )}
      </div>

      <div
        style={{
          fontSize: "var(--font-size-xs)",
          color: "var(--text-muted)",
          padding: "var(--space-xs)",
          lineHeight: "var(--line-tight)",
        }}
      >
        {isOverlayOrSidebar
          ? `Navigate to this ${isSidebar ? "sidebar" : "overlay"} using a page_nav element with target "${page.id}", or use $back to dismiss.`
          : "Page background is visible behind all elements. Use a gradient overlay on top of an image to keep text readable."}
      </div>
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
          padding: "var(--space-sm)",
          fontSize: "var(--font-size-sm)",
          fontWeight: "var(--font-weight-semibold)",
          background: highlight ? "rgba(138,180,147,0.06)" : "var(--bg-surface)",
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
}: {
  themes: ThemeSummary[];
  currentThemeId: string;
  onThemeChange: (themeId: string) => void;
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
            letterSpacing: "var(--tracking-wide)",
            fontWeight: "var(--font-weight-semibold)",
          }}
        >
          Theme
        </span>
      </div>

      {/* Theme picker grid — quick-switch only */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(100px, 1fr))",
          gap: "var(--space-sm)",
          marginBottom: "var(--space-sm)",
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
                padding: "var(--space-sm)",
                borderRadius: "var(--radius-lg)",
                border: isSelected ? "2px solid var(--accent)" : "1px solid var(--border-color)",
                background: isSelected ? "var(--accent-dim, rgba(138,180,147,0.12))" : "var(--bg-surface)",
                cursor: "pointer",
                textAlign: "center",
              }}
            >
              <div style={{ display: "flex", gap: "var(--space-2xs)", justifyContent: "center", marginBottom: "var(--space-xs)" }}>
                {(t.preview_colors || []).slice(0, 4).map((c, i) => (
                  <div
                    key={i}
                    style={{
                      width: 14,
                      height: 14,
                      borderRadius: "var(--border-radius)",
                      backgroundColor: c,
                      border: "1px solid rgba(128,128,128,0.3)",
                    }}
                  />
                ))}
              </div>
              <div
                style={{
                  fontSize: "var(--font-size-2xs)",
                  fontWeight: isSelected ? "var(--font-weight-semibold)" : "var(--font-weight-normal)",
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
          margin: "var(--space-xs) 0 var(--space-sm)",
        }}
      />
    </>
  );
}
