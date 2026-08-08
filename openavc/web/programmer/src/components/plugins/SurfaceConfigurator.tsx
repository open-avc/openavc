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
 *
 * This file is the entry point and the routing decision — which surface a
 * layout gets — and nothing else. The editors themselves live in surface/,
 * one file per thing you can be looking at: the schematic surfaces and the
 * routing matrix, the deck workbench that a device-backed grid opens into,
 * and the inspector panels each of those hands a selected control to.
 */
import { useState, useCallback } from "react";
import { CollapsibleSection } from "../driver-builder/CollapsibleSection";
import { useConnectionStore } from "../../store/connectionStore";
import { AutoPageEditor } from "./surface/AutoPageEditor";
import { ControlAssignmentPanel } from "./surface/ControlAssignmentPanel";
import { DeckWorkbench } from "./surface/DeckWorkbench";
import { NoDeviceState } from "./surface/NoDeviceState";
import { RoutingMatrix } from "./surface/RoutingMatrix";
import { CustomSurface, GridSurface, PageTabs, StripSurface } from "./surface/StaticSurfaces";
import { networkEntriesOf } from "./surface/deckHelpers";
import type { ButtonAssignment, SurfaceLayout } from "./surface/types";

interface SurfaceConfiguratorProps {
  layout: SurfaceLayout;
  pluginId: string;
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
  onRequestConfigRefresh?: () => void;
}

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
    ...new Set([
      ...Object.keys(decksMap),
      ...Object.keys(deckNames),
      ...networkEntriesOf(config)
        .map((e) => e.serial)
        .filter((s): s is string => Boolean(s)),
    ]),
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
        // A configured network deck counts as a known unit even before its
        // first connect — the workbench shows its status card instead of
        // pretending nothing exists.
        if (knownSerials.length === 0 && networkEntriesOf(config).length === 0) {
          return (
            <NoDeviceState
              pluginId={pluginId}
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
