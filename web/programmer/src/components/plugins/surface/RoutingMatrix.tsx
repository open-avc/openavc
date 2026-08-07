/**
 * Routing matrix (Dante, NDI) — the one surface whose geometry isn't declared.
 *
 * Sources and destinations come from the state keys the plugin publishes, so
 * rows and columns appear as the system discovers them and a cell is a
 * crosspoint to route rather than a control to configure.
 */
import { useState } from "react";
import { Trash2, ChevronRight } from "lucide-react";
import { useConnectionStore } from "../../../store/connectionStore";
import { isCellRouted, matchStateKeys } from "../routingMatrixHelpers";
import * as api from "../../../api/restClient";
import { useAnchoredPanel } from "../../shared/AnchoredPanel";
import type { SurfaceLayout } from "./types";

export function RoutingMatrix({
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
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [pendingCells, setPendingCells] = useState<Set<string>>(new Set());
  // A short list of preset names, so it floors at its own readable width and
  // lets a long name push it wider rather than being clipped to the trigger.
  const presetPanel = useAnchoredPanel<HTMLButtonElement>({ minWidth: 180, widthMode: "min" });
  const presetDropdownOpen = presetPanel.open;

  // Get row/column labels from state ('*' matches anywhere in the pattern)
  const stateKeys = Object.keys(liveState);
  const rowNames = matchStateKeys(stateKeys, layout.rows_state_pattern ?? "").map(
    (m) => m.name,
  );
  const colNames = matchStateKeys(stateKeys, layout.columns_state_pattern ?? "").map(
    (m) => m.name,
  );

  const getCellState = (row: string, col: string): boolean => {
    const pattern = layout.cell_state_pattern ?? "";
    const key = pattern.replace("{row}", row).replace("{col}", col);
    return isCellRouted(liveState[key]);
  };

  const handleCellClick = async (row: string, col: string) => {
    // One action per cell at a time: the toggle direction is derived from
    // liveState, which only updates when the plugin pushes new crosspoint
    // state — a rapid second click would re-read the pre-click state and
    // send the same route/unroute again.
    const cellKey = `${row}|${col}`;
    if (pendingCells.has(cellKey)) return;
    setPendingCells((prev) => new Set(prev).add(cellKey));
    try {
      const actionId = getCellState(row, col) ? "unroute" : "route";
      await api.emitContextAction(pluginId, actionId, { row, col });
    } finally {
      setPendingCells((prev) => {
        const next = new Set(prev);
        next.delete(cellKey);
        return next;
      });
    }
  };

  // Preset support
  const showPresets = layout.presets === true;
  const presets = (config?._presets as Record<string, unknown[]>) ?? {};
  const presetNames = Object.keys(presets);
  const activePreset = String(liveState[`plugin.${pluginId}.active_preset`] ?? "");
  const isDirty = Boolean(liveState[`plugin.${pluginId}.preset_dirty`]);

  const handleRecallPreset = async (name: string) => {
    presetPanel.close();
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
          <div ref={presetPanel.containerRef}>
            <button
              ref={presetPanel.triggerRef}
              onClick={presetPanel.toggle}
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
                ...presetPanel.panelStyle,
                background: "var(--bg-surface)",
                border: "1px solid var(--border-color)",
                borderRadius: "var(--border-radius)",
                boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
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
                    const pending = pendingCells.has(`${row}|${col}`);
                    return (
                      <td key={col} style={{ padding: 1 }}>
                        <button
                          onClick={() => handleCellClick(row, col)}
                          disabled={pending}
                          style={{
                            width: 24,
                            height: 24,
                            borderRadius: 3,
                            background: active ? "var(--accent-bg)" : "var(--bg-surface)",
                            border: "1px solid var(--border-color)",
                            cursor: pending ? "wait" : "pointer",
                            opacity: pending ? 0.5 : 1,
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
