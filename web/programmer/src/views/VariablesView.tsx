import { useState, useMemo, useCallback, useEffect, useRef } from "react";
import { Plus, Trash2, ChevronRight, Zap, Layout, FileCode, LayoutDashboard, HardDrive, X, Cpu, Link, ExternalLink } from "lucide-react";
import { CopyButton } from "../components/shared/CopyButton";
import { ConfirmDialog } from "../components/shared/ConfirmDialog";
import { VariableKeyPicker } from "../components/shared/VariableKeyPicker";
import { useNavigationStore, type FocusTarget } from "../store/navigationStore";
import { ViewContainer } from "../components/layout/ViewContainer";
import { useProjectStore } from "../store/projectStore";
import { useConnectionStore } from "../store/connectionStore";
import { getStateHistory, listDrivers, getScriptReferences } from "../api/restClient";
import type { ProjectConfig, VariableConfig, MacroConfig, UIPage, ScriptConfig, StateHistoryEntry, DriverInfo, ScriptReference } from "../api/types";
import type { ViewId } from "../components/layout/Sidebar";
import { showError } from "../store/toastStore";

type SubTab = "variables" | "device_states" | "activity";

interface VariableUsage {
  type: "macro" | "ui" | "script";
  icon: typeof Zap;
  label: string;
  detail: string;
  /** Navigation target when clicked */
  nav?: { view: ViewId; focus: FocusTarget };
}

// --- Dismissible help banner ---

function HelpBanner({ storageKey, children }: { storageKey: string; children: React.ReactNode }) {
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(storageKey) === "1");
  if (dismissed) return null;
  return (
    <div style={helpBannerStyle}>
      <div style={{ flex: 1, lineHeight: 1.5 }}>{children}</div>
      <button
        onClick={() => { setDismissed(true); localStorage.setItem(storageKey, "1"); }}
        style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", padding: 2, flexShrink: 0 }}
        title="Dismiss"
      >
        <X size={14} />
      </button>
    </div>
  );
}

// --- Clickable usage row ---

function UsageRow({ usage }: { usage: VariableUsage }) {
  const navigateTo = useNavigationStore((s) => s.navigateTo);
  const hasNav = !!usage.nav;
  const typeLabel = usage.type === "macro" ? "Macro" : usage.type === "ui" ? "UI" : "Script";

  return (
    <div
      onClick={hasNav ? () => navigateTo(usage.nav!.view, usage.nav!.focus) : undefined}
      style={{
        ...usageRowStyle,
        cursor: hasNav ? "pointer" : "default",
      }}
      onMouseEnter={hasNav ? (e) => (e.currentTarget.style.background = "var(--bg-hover)") : undefined}
      onMouseLeave={hasNav ? (e) => (e.currentTarget.style.background = "var(--bg-surface)") : undefined}
      title={hasNav ? `Jump to ${typeLabel}` : undefined}
    >
      <usage.icon size={14} style={{ color: usageColor(usage.type), flexShrink: 0 }} />
      <span style={{ color: usageColor(usage.type), fontWeight: 500, flexShrink: 0 }}>
        {typeLabel}
      </span>
      <span style={{ color: "var(--text-primary)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{usage.label}</span>
      <ChevronRight size={12} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
      <span style={{ color: "var(--text-secondary)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{usage.detail}</span>
      {hasNav && (
        <ExternalLink size={12} style={{ color: "var(--text-muted)", flexShrink: 0, opacity: 0.6 }} />
      )}
    </div>
  );
}

// ==========================================================================
// Main View
// ==========================================================================

export function VariablesView() {
  const [subTab, setSubTab] = useState<SubTab>("variables");

  return (
    <ViewContainer
      title="State"
      actions={subTab === "variables" ? <VariablesActions /> : undefined}
    >
      {/* Sub-tab bar */}
      <div style={subTabBarStyle}>
        {([
          { key: "variables" as const, label: "Variables" },
          { key: "device_states" as const, label: "Device States" },
          { key: "activity" as const, label: "Activity" },
        ]).map((tab) => (
          <button
            key={tab.key}
            onClick={() => setSubTab(tab.key)}
            style={{
              ...subTabBtnStyle,
              borderBottom: subTab === tab.key ? "2px solid var(--accent)" : "2px solid transparent",
              color: subTab === tab.key ? "var(--accent)" : "var(--text-secondary)",
              fontWeight: subTab === tab.key ? 600 : 400,
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {subTab === "variables" && <VariablesSubTab />}
      {subTab === "device_states" && <DeviceStatesSubTab />}
      {subTab === "activity" && <ActivitySubTab />}
    </ViewContainer>
  );
}

// ==========================================================================
// Variables Actions (header button)
// ==========================================================================

function VariablesActions() {
  const [showCreate, setShowCreate] = useState(false);
  // We use a global event to toggle the create form inside VariablesSubTab
  // Instead, use a simpler approach: expose via a custom event
  return (
    <button
      onClick={() => {
        window.dispatchEvent(new CustomEvent("openavc:toggle-var-create"));
      }}
      style={headerBtnStyle}
    >
      <Plus size={14} /> New Variable
    </button>
  );
}

// ==========================================================================
// Variables Sub-Tab
// ==========================================================================

function VariablesSubTab() {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const updateWithUndo = useProjectStore((s) => s.updateWithUndo);
  const liveState = useConnectionStore((s) => s.liveState);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [search, setSearch] = useState("");
  const [newId, setNewId] = useState("");
  const [newType, setNewType] = useState("string");
  const [newLabel, setNewLabel] = useState("");
  const [newDefault, setNewDefault] = useState("");
  const [pendingConfirm, setPendingConfirm] = useState<{ title: string; message: React.ReactNode; confirmLabel: string; onConfirm: () => void } | null>(null);

  // Listen for header button toggle
  useEffect(() => {
    const handler = () => setShowCreate((v) => !v);
    window.addEventListener("openavc:toggle-var-create", handler);
    return () => window.removeEventListener("openavc:toggle-var-create", handler);
  }, []);

  const variables = project?.variables ?? [];
  const filteredVariables = variables.filter(v =>
    !search || v.id.toLowerCase().includes(search.toLowerCase()) ||
    (v.label && v.label.toLowerCase().includes(search.toLowerCase()))
  );

  // Script references (fetched once)
  const [scriptRefs, setScriptRefs] = useState<ScriptReference[]>([]);
  useEffect(() => {
    let cancelled = false;
    getScriptReferences()
      .then((refs) => { if (!cancelled) setScriptRefs(refs); })
      .catch(console.error);
    return () => { cancelled = true; };
  }, []);

  const usageMap = useMemo(() => {
    if (!project) return new Map<string, VariableUsage[]>();
    return buildUsageMap(project, scriptRefs);
  }, [project, scriptRefs]);

  const doCreateVariable = useCallback((id: string) => {
    if (variables.some((v) => v.id === id)) {
      showError(`Variable "${id}" already exists.`);
      return;
    }
    let defVal: unknown = newDefault;
    if (newType === "boolean") defVal = newDefault === "true";
    else if (newType === "number") defVal = Number(newDefault) || 0;

    const newVar: VariableConfig = {
      id,
      type: newType,
      default: defVal,
      label: newLabel.trim() || id,
    };
    update({ variables: [...variables, newVar] });
    setNewId("");
    setNewType("string");
    setNewLabel("");
    setNewDefault("");
    setShowCreate(false);
    setSelectedId(id);
    setTimeout(() => useProjectStore.getState().save(), 100);
  }, [variables, newType, newLabel, newDefault, update]);

  const handleCreate = useCallback(() => {
    if (!project) return;
    const rawId = newId.trim();
    const id = rawId.replace(/[^a-zA-Z0-9_]/g, "_");
    if (!id) return;
    if (id !== rawId) {
      setPendingConfirm({
        title: "Sanitized ID",
        message: `ID will be sanitized to "${id}" (special characters replaced with underscores). State key: var.${id}`,
        confirmLabel: "Continue",
        onConfirm: () => { setPendingConfirm(null); doCreateVariable(id); },
      });
      return;
    }
    doCreateVariable(id);
  }, [project, newId, doCreateVariable]);

  const handleDelete = useCallback(
    (id: string) => {
      const usages = usageMap.get(id) ?? [];
      const message = usages.length > 0
        ? (
          <>
            <div>Variable "{id}" is used in {usages.length} place(s):</div>
            <ul style={{ margin: "8px 0 0 16px", padding: 0, fontSize: 12 }}>
              {usages.slice(0, 5).map((u, i) => <li key={i}>{u.label}: {u.detail}</li>)}
              {usages.length > 5 && <li>...and {usages.length - 5} more</li>}
            </ul>
          </>
        )
        : `Delete variable "${id}"?`;
      setPendingConfirm({
        title: "Delete Variable",
        message,
        confirmLabel: "Delete",
        onConfirm: () => {
          setPendingConfirm(null);
          updateWithUndo({ variables: variables.filter((v) => v.id !== id) }, `Delete variable "${id}"`);
          if (selectedId === id) setSelectedId(null);
          setTimeout(() => useProjectStore.getState().save(), 100);
        },
      });
    },
    [variables, usageMap, selectedId, update]
  );

  const varSaveTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const handleUpdate = useCallback(
    (id: string, patch: Partial<VariableConfig>) => {
      update({
        variables: variables.map((v) => (v.id === id ? { ...v, ...patch } : v)),
      });
      clearTimeout(varSaveTimer.current);
      varSaveTimer.current = setTimeout(() => useProjectStore.getState().save(), 1500);
    },
    [variables, update]
  );

  // Flush pending save on unmount to prevent data loss
  useEffect(() => {
    return () => {
      if (varSaveTimer.current) {
        clearTimeout(varSaveTimer.current);
        useProjectStore.getState().save();
      }
    };
  }, []);

  const selectedVar = variables.find((v) => v.id === selectedId);
  const selectedUsages = selectedId ? usageMap.get(selectedId) ?? [] : [];
  const selectedLiveValue = selectedId ? liveState[`var.${selectedId}`] : undefined;

  return (
    <div style={{ display: "flex", height: "100%" }}>
      {/* Left: variable list */}
      <div style={{ width: 280, flexShrink: 0, borderRight: "1px solid var(--border-color)", display: "flex", flexDirection: "column" }}>
        <HelpBanner storageKey="openavc-help-variables">
          Variables are values you create for your program logic — things like room mode,
          system status, or custom flags. They&apos;re separate from device properties, which
          are reported by hardware automatically. You can bind variables to device properties,
          or use them independently in macros, scripts, and UI elements.
        </HelpBanner>

        {/* Search */}
        <div style={{ padding: "var(--space-sm) var(--space-md)" }}>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search variables..."
            style={searchInputStyle}
          />
        </div>

        {/* Create form */}
        {showCreate && (
          <div style={createFormStyle}>
            <div style={{ display: "flex", gap: "var(--space-sm)" }}>
              <div style={{ flex: 1 }}>
                <label style={miniLabel}>ID</label>
                <input
                  style={fieldInput}
                  value={newId}
                  onChange={(e) => setNewId(e.target.value)}
                  placeholder="e.g. room_active"
                  autoFocus
                  onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                />
              </div>
              <div style={{ width: 90 }}>
                <label style={miniLabel}>Type</label>
                <select style={fieldInput} value={newType} onChange={(e) => setNewType(e.target.value)}>
                  <option value="string">String</option>
                  <option value="boolean">Boolean</option>
                  <option value="number">Number</option>
                </select>
              </div>
            </div>
            <div style={{ display: "flex", gap: "var(--space-sm)" }}>
              <div style={{ flex: 1 }}>
                <label style={miniLabel}>Label</label>
                <input
                  style={fieldInput}
                  value={newLabel}
                  onChange={(e) => setNewLabel(e.target.value)}
                  placeholder="Display name"
                  onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                />
              </div>
              <div style={{ width: 90 }}>
                <label style={miniLabel}>Default</label>
                {newType === "boolean" ? (
                  <select style={fieldInput} value={newDefault} onChange={(e) => setNewDefault(e.target.value)}>
                    <option value="false">false</option>
                    <option value="true">true</option>
                  </select>
                ) : (
                  <input
                    style={fieldInput}
                    value={newDefault}
                    onChange={(e) => setNewDefault(e.target.value)}
                    placeholder={newType === "number" ? "0" : ""}
                    onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                  />
                )}
              </div>
            </div>
            <div style={{ display: "flex", gap: "var(--space-xs)" }}>
              <button onClick={handleCreate} style={btnPrimary}>Create</button>
              <button onClick={() => setShowCreate(false)} style={btnSecondary}>Cancel</button>
            </div>
          </div>
        )}

        {/* List */}
        <div style={{ flex: 1, overflow: "auto" }}>
          {filteredVariables.length === 0 ? (
            <div style={{ padding: "var(--space-xl)", textAlign: "center", color: "var(--text-muted)", fontSize: "var(--font-size-sm)", lineHeight: 1.6 }}>
              {variables.length === 0 ? (
                <>
                  No variables defined yet.
                  <br /><br />
                  Variables are shared values that your macros, scripts, and
                  UI elements can all read and write. For example,{" "}
                  <code style={codeStyle}>room_active</code> could track whether
                  the room is powered on.
                  <br /><br />
                  Click <strong>New Variable</strong> above, or create one
                  from any macro or UI binding editor.
                </>
              ) : (
                <>No variables match &ldquo;{search}&rdquo;.</>
              )}
            </div>
          ) : (
            filteredVariables.map((v) => {
              const usages = usageMap.get(v.id) ?? [];
              const live = liveState[`var.${v.id}`];
              return (
                <div
                  key={v.id}
                  onClick={() => setSelectedId(v.id)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "var(--space-sm) var(--space-md)",
                    cursor: "pointer",
                    background: selectedId === v.id ? "var(--bg-hover)" : "transparent",
                    borderBottom: "1px solid var(--border-color)",
                  }}
                  onMouseEnter={(e) => {
                    if (selectedId !== v.id) (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)";
                  }}
                  onMouseLeave={(e) => {
                    if (selectedId !== v.id) (e.currentTarget as HTMLElement).style.background = "transparent";
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                      <code style={{ ...codeStyle, color: "var(--accent)", fontWeight: selectedId === v.id ? 600 : 400 }}>
                        var.{v.id}
                      </code>
                      <CopyButton value={`var.${v.id}`} title="Copy variable key" />
                      <span style={typeBadgeStyle}>{v.type}</span>
                      {v.persist && <span title="Persisted across restarts"><HardDrive size={12} style={{ color: "var(--text-muted)" }} /></span>}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 1 }}>
                      {v.label}{live !== undefined ? ` = ${JSON.stringify(live)}` : ""}
                    </div>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", flexShrink: 0 }}>
                    {usages.length > 0 && (
                      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                        {usages.length} use{usages.length !== 1 ? "s" : ""}
                      </span>
                    )}
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDelete(v.id); }}
                      style={iconBtn}
                      title="Delete variable"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Right: detail panel */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {selectedVar ? (
          <div style={{ padding: "var(--space-lg)" }}>
            {/* Header */}
            <div style={{ marginBottom: "var(--space-xl)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                <code style={{ fontSize: "var(--font-size-lg)", color: "var(--accent)", fontWeight: 600 }}>
                  var.{selectedVar.id}
                </code>
                <CopyButton value={`var.${selectedVar.id}`} size={14} title="Copy variable key" />
                <span style={{ ...typeBadgeStyle, marginLeft: "var(--space-xs)", fontSize: 12 }}>{selectedVar.type}</span>
              </div>
            </div>

            {/* Editable fields */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-md)", maxWidth: 500, marginBottom: "var(--space-xl)" }}>
              <div>
                <label style={detailLabel}>Label</label>
                <input
                  style={detailInput}
                  value={selectedVar.label}
                  onChange={(e) => handleUpdate(selectedVar.id, { label: e.target.value })}
                />
              </div>
              <div>
                <label style={detailLabel}>Type</label>
                <select
                  style={detailInput}
                  value={selectedVar.type}
                  onChange={(e) => handleUpdate(selectedVar.id, { type: e.target.value })}
                >
                  <option value="string">String</option>
                  <option value="boolean">Boolean</option>
                  <option value="number">Number</option>
                </select>
              </div>
              <div>
                <label style={detailLabel}>Default Value</label>
                <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>
                  {JSON.stringify(selectedVar.default)}
                </span>
              </div>
              <div>
                <label style={detailLabel}>Current Value</label>
                <span style={{ fontSize: "var(--font-size-sm)", color: selectedLiveValue !== undefined ? "var(--text-primary)" : "var(--text-muted)", fontWeight: 500 }}>
                  {selectedLiveValue !== undefined ? JSON.stringify(selectedLiveValue) : "not set (system not running)"}
                </span>
              </div>
            </div>

            {/* Dashboard tracking + Persistence */}
            <div style={{ display: "flex", gap: "var(--space-md)", marginBottom: "var(--space-xl)", flexWrap: "wrap" }}>
              <div>
                <button
                  onClick={() => handleUpdate(selectedVar.id, { dashboard: !selectedVar.dashboard })}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--space-sm)",
                    padding: "var(--space-sm) var(--space-md)",
                    borderRadius: "var(--border-radius)",
                    background: selectedVar.dashboard ? "rgba(33,150,243,0.15)" : "var(--bg-surface)",
                    border: "1px solid " + (selectedVar.dashboard ? "rgba(33,150,243,0.3)" : "var(--border-color)"),
                    color: selectedVar.dashboard ? "var(--accent)" : "var(--text-secondary)",
                    fontSize: "var(--font-size-sm)",
                    cursor: "pointer",
                  }}
                >
                  <LayoutDashboard size={14} />
                  {selectedVar.dashboard ? "Shown on Dashboard" : "Show on Dashboard"}
                </button>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: "var(--space-xs)" }}>
                  Tracked variables appear on the Dashboard with their live value.
                </div>
              </div>
              <div>
                <button
                  onClick={() => handleUpdate(selectedVar.id, { persist: !selectedVar.persist })}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--space-sm)",
                    padding: "var(--space-sm) var(--space-md)",
                    borderRadius: "var(--border-radius)",
                    background: selectedVar.persist ? "rgba(33,150,243,0.15)" : "var(--bg-surface)",
                    border: "1px solid " + (selectedVar.persist ? "rgba(33,150,243,0.3)" : "var(--border-color)"),
                    color: selectedVar.persist ? "var(--accent)" : "var(--text-secondary)",
                    fontSize: "var(--font-size-sm)",
                    cursor: "pointer",
                  }}
                >
                  <HardDrive size={14} />
                  {selectedVar.persist ? "Persisted Across Restarts" : "Persist Across Restarts"}
                </button>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: "var(--space-xs)" }}>
                  When enabled, this variable&apos;s value is saved to disk and restored after a server restart.
                </div>
              </div>
            </div>

            {/* Source binding */}
            <SourceBindingEditor
              variable={selectedVar}
              liveState={liveState}
              onUpdate={(patch) => handleUpdate(selectedVar.id, patch)}
            />

            {/* Where used */}
            <div>
              <h3 style={sectionTitle}>
                Where Used ({selectedUsages.length})
              </h3>
              {selectedUsages.length === 0 ? (
                <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", fontStyle: "italic" }}>
                  This variable is not referenced by any macros, UI bindings, or scripts yet.
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                  {selectedUsages.map((u, i) => (
                    <UsageRow key={i} usage={u} />
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-muted)", gap: "var(--space-sm)", padding: "var(--space-xl)", textAlign: "center" }}>
            <div style={{ fontSize: "var(--font-size-md)" }}>
              {variables.length === 0 ? "Create your first variable" : "Select a variable to see details"}
            </div>
            <div style={{ fontSize: "var(--font-size-sm)", maxWidth: 420, lineHeight: 1.5 }}>
              Variables are shared values visible across your entire system.
              When a macro sets a variable, UI elements update instantly and
              scripts can react. Think of them as signals on a bus.
            </div>
          </div>
        )}
      </div>
      {pendingConfirm && (
        <ConfirmDialog
          title={pendingConfirm.title}
          message={pendingConfirm.message}
          confirmLabel={pendingConfirm.confirmLabel}
          onConfirm={pendingConfirm.onConfirm}
          onCancel={() => setPendingConfirm(null)}
        />
      )}
    </div>
  );
}

// ==========================================================================
// Source Binding Editor (for variable detail panel)
// ==========================================================================

function SourceBindingEditor({
  variable,
  liveState,
  onUpdate,
}: {
  variable: VariableConfig;
  liveState: Record<string, unknown>;
  onUpdate: (patch: Partial<VariableConfig>) => void;
}) {
  const isBound = !!variable.source_key;
  const sourceMap = variable.source_map ?? {};
  const sourceValue = variable.source_key ? liveState[variable.source_key] : undefined;

  // Compute the mapped value for preview
  const mappedValue = sourceValue !== undefined && isBound
    ? (sourceMap[String(sourceValue)] ?? sourceValue)
    : undefined;

  const handleModeChange = (bound: boolean) => {
    if (bound) {
      onUpdate({ source_key: "", source_map: {} });
    } else {
      onUpdate({ source_key: undefined, source_map: undefined });
    }
  };

  const handleSourceKeyChange = (key: string) => {
    onUpdate({ source_key: key || undefined });
  };

  const handleMapChange = (newMap: Record<string, unknown>) => {
    onUpdate({ source_map: Object.keys(newMap).length > 0 ? newMap : undefined });
  };

  const addMapEntry = () => {
    // Generate unique placeholder key to avoid overwriting existing entries
    let key = "value";
    let counter = 1;
    while (key in sourceMap) {
      key = `value_${counter++}`;
    }
    handleMapChange({ ...sourceMap, [key]: "" });
  };

  const removeMapEntry = (key: string) => {
    const newMap = { ...sourceMap };
    delete newMap[key];
    handleMapChange(newMap);
  };

  const updateMapEntry = (oldKey: string, newKey: string, newValue: unknown) => {
    const entries = Object.entries(sourceMap);
    const newMap: Record<string, unknown> = {};
    for (const [k, v] of entries) {
      if (k === oldKey) {
        newMap[newKey] = newValue;
      } else {
        newMap[k] = v;
      }
    }
    handleMapChange(newMap);
  };

  return (
    <div style={{ marginBottom: "var(--space-xl)" }}>
      <h3 style={sectionTitle}>
        <Link size={14} style={{ verticalAlign: "middle", marginRight: 4 }} />
        Source
      </h3>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)", fontStyle: "italic" }}>
        Choose where this variable gets its value.
      </div>

      {/* Mode toggle */}
      <div style={{ display: "flex", gap: "var(--space-md)", marginBottom: "var(--space-md)" }}>
        <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: "var(--font-size-sm)", cursor: "pointer" }}>
          <input type="radio" name={`source-${variable.id}`} checked={!isBound} onChange={() => handleModeChange(false)} />
          Manual
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: "var(--font-size-sm)", cursor: "pointer" }}>
          <input type="radio" name={`source-${variable.id}`} checked={isBound} onChange={() => handleModeChange(true)} />
          Bound to state key
        </label>
      </div>

      {!isBound && (
        <div style={{ fontSize: 12, color: "var(--text-muted)", fontStyle: "italic" }}>
          You control this value — set it from macros, scripts, or UI actions.
        </div>
      )}

      {isBound && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
          <div style={{ fontSize: 12, color: "var(--text-muted)", fontStyle: "italic", marginBottom: 2 }}>
            This variable automatically mirrors a device property. Use the value map
            to translate hardware values into friendly text.
          </div>

          <div>
            <label style={detailLabel}>Source State Key</label>
            <VariableKeyPicker
              value={variable.source_key || ""}
              onChange={handleSourceKeyChange}
              placeholder="Select state key to bind..."
            />
          </div>

          {/* Value Map */}
          <div>
            <label style={detailLabel}>Value Map (optional)</label>
            <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic", marginBottom: "var(--space-xs)" }}>
              Translate raw device values into something more useful. If a value
              isn&apos;t in the map, the raw value is used.
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {Object.entries(sourceMap).map(([mapKey, mapValue], idx) => (
                <div key={idx} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <input
                    value={mapKey}
                    onChange={(e) => updateMapEntry(mapKey, e.target.value, mapValue)}
                    placeholder="Source value"
                    style={{ ...fieldInput, width: 120 }}
                  />
                  <span style={{ fontSize: 11, color: "var(--text-muted)" }}>&rarr;</span>
                  <input
                    value={String(mapValue ?? "")}
                    onChange={(e) => updateMapEntry(mapKey, mapKey, e.target.value)}
                    placeholder="Variable value"
                    style={{ ...fieldInput, flex: 1 }}
                  />
                  <button
                    onClick={() => removeMapEntry(mapKey)}
                    style={{ ...iconBtn, color: "var(--color-error)" }}
                    title="Remove mapping"
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
              <button
                onClick={addMapEntry}
                style={{
                  display: "flex", alignItems: "center", gap: 4,
                  padding: "3px 8px", borderRadius: "var(--border-radius)",
                  fontSize: 11, color: "var(--accent)", background: "transparent",
                  border: "1px dashed var(--border-color)", alignSelf: "flex-start",
                  cursor: "pointer",
                }}
              >
                <Plus size={12} /> Add mapping
              </button>
            </div>
          </div>

          {/* Live preview */}
          {variable.source_key && sourceValue !== undefined && (
            <div style={{
              padding: "var(--space-sm) var(--space-md)",
              background: "var(--bg-surface)",
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              fontSize: "var(--font-size-sm)",
              fontFamily: "var(--font-mono)",
            }}>
              <span style={{ color: "var(--text-muted)" }}>Source: </span>
              <span style={{ color: "var(--text-primary)" }}>{variable.source_key}</span>
              <span style={{ color: "var(--text-muted)" }}> = </span>
              <span style={{ color: "var(--text-primary)" }}>{String(sourceValue)}</span>
              <span style={{ color: "var(--text-muted)" }}> &rarr; Variable: </span>
              <span style={{ color: "var(--accent)", fontWeight: 600 }}>{String(mappedValue)}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ==========================================================================
// Device States Sub-Tab
// ==========================================================================

function DeviceStatesSubTab() {
  const project = useProjectStore((s) => s.project);
  const liveState = useConnectionStore((s) => s.liveState);

  const devices = project?.devices ?? [];
  const [selectedDeviceId, setSelectedDeviceId] = useState<string | null>(null);
  const [selectedProp, setSelectedProp] = useState<string | null>(null);

  // Driver registry (from /api/drivers — includes project-level community drivers)
  const [driverRegistry, setDriverRegistry] = useState<DriverInfo[]>([]);

  // Load driver registry once
  useEffect(() => {
    let cancelled = false;
    listDrivers()
      .then((drivers) => { if (!cancelled) setDriverRegistry(drivers); })
      .catch(console.error);
    return () => { cancelled = true; };
  }, []);

  // Script references (fetched once)
  const [scriptRefs, setScriptRefs] = useState<ScriptReference[]>([]);
  useEffect(() => {
    let cancelled = false;
    getScriptReferences()
      .then((refs) => { if (!cancelled) setScriptRefs(refs); })
      .catch(console.error);
    return () => { cancelled = true; };
  }, []);

  // Cross-reference map for all state keys
  const usageMap = useMemo(() => {
    if (!project) return new Map<string, VariableUsage[]>();
    return buildStateUsageMap(project, scriptRefs);
  }, [project, scriptRefs]);

  // Group devices by device_groups
  const projGroups = project?.device_groups ?? [];
  const deviceGroups = useMemo(() => {
    const deviceToGroup = new Map<string, string>();
    for (const g of projGroups) {
      for (const did of g.device_ids) {
        if (!deviceToGroup.has(did)) deviceToGroup.set(did, g.name);
      }
    }
    const groups = new Map<string, typeof devices>();
    for (const d of devices) {
      const g = deviceToGroup.get(d.id) || "Ungrouped";
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g)!.push(d);
    }
    return groups;
  }, [devices, projGroups]);

  // Build state entries from driver-declared state_variables + live state
  const stateEntries = useMemo(() => {
    if (!selectedDeviceId) return [];
    const selectedDevice = devices.find((d) => d.id === selectedDeviceId);
    if (!selectedDevice) return [];

    const prefix = `device.${selectedDeviceId}.`;
    const seen = new Set<string>();
    const entries: { prop: string; key: string; value: unknown; meta: { type?: string; label?: string; values?: string[] } | null }[] = [];

    // Get state_variables from driver registry
    const driverDef = driverRegistry.find((d) => d.id === selectedDevice.driver);
    const declaredVars = (driverDef?.state_variables ?? {}) as Record<string, { type?: string; label?: string; values?: string[] }>;

    // 1. Start with driver-declared state variables (always available once loaded)
    for (const [prop, meta] of Object.entries(declaredVars)) {
      const key = `${prefix}${prop}`;
      seen.add(key);
      entries.push({ prop, key, value: liveState[key], meta });
    }

    // 2. Add any live state keys not declared by the driver (e.g., connected, enabled, name)
    for (const [k, v] of Object.entries(liveState)) {
      if (k.startsWith(prefix) && !seen.has(k)) {
        const prop = k.slice(prefix.length);
        entries.push({ prop, key: k, value: v, meta: null });
      }
    }

    entries.sort((a, b) => a.prop.localeCompare(b.prop));
    return entries;
  }, [selectedDeviceId, devices, driverRegistry, liveState]);

  const selectedDevice = devices.find((d) => d.id === selectedDeviceId);
  const selectedPropUsages = selectedProp ? usageMap.get(selectedProp) ?? [] : [];

  return (
    <div style={{ display: "flex", height: "100%" }}>
      {/* Left: device list */}
      <div style={{ width: 280, flexShrink: 0, borderRight: "1px solid var(--border-color)", display: "flex", flexDirection: "column" }}>
        <HelpBanner storageKey="openavc-help-device-states">
          Device states are live values reported by your hardware — power status,
          input selection, volume levels, etc. These update automatically. You can
          bind UI elements directly to these values, use them in macro triggers,
          or reference them in scripts. Use the copy button to grab a state key.
        </HelpBanner>

        <div style={{ flex: 1, overflow: "auto" }}>
          {devices.length === 0 ? (
            <div style={{ padding: "var(--space-xl)", textAlign: "center", color: "var(--text-muted)", fontSize: "var(--font-size-sm)", lineHeight: 1.6 }}>
              No devices configured yet.
              <br /><br />
              Add devices in the <strong>Devices</strong> tab to see their live state properties here.
            </div>
          ) : (
            Array.from(deviceGroups.entries()).map(([group, groupDevices]) => (
              <div key={group}>
                {deviceGroups.size > 1 && (
                  <div style={{ padding: "var(--space-sm) var(--space-md) 2px", fontSize: 11, color: "var(--text-muted)", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                    {group}
                  </div>
                )}
                {groupDevices.map((d) => {
                  const isConnected = liveState[`device.${d.id}.connected`] === true;
                  return (
                    <div
                      key={d.id}
                      onClick={() => { setSelectedDeviceId(d.id); setSelectedProp(null); }}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "var(--space-sm)",
                        padding: "var(--space-sm) var(--space-md)",
                        cursor: "pointer",
                        background: selectedDeviceId === d.id ? "var(--bg-hover)" : "transparent",
                        borderBottom: "1px solid var(--border-color)",
                      }}
                      onMouseEnter={(e) => { if (selectedDeviceId !== d.id) e.currentTarget.style.background = "var(--bg-hover)"; }}
                      onMouseLeave={(e) => { if (selectedDeviceId !== d.id) e.currentTarget.style.background = "transparent"; }}
                    >
                      <div style={{
                        width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
                        background: isConnected ? "var(--color-success)" : "var(--text-muted)",
                      }} />
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div style={{ fontSize: "var(--font-size-sm)", fontWeight: 500, color: "var(--text-primary)" }}>{d.name}</div>
                        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{d.driver}</div>
                      </div>
                      <Cpu size={14} style={{ color: "var(--text-muted)", flexShrink: 0, opacity: 0.5 }} />
                    </div>
                  );
                })}
              </div>
            ))
          )}
        </div>
      </div>

      {/* Right: state properties */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {selectedDevice ? (
          <div style={{ padding: "var(--space-lg)" }}>
            <div style={{ marginBottom: "var(--space-lg)" }}>
              <div style={{ fontSize: "var(--font-size-lg)", fontWeight: 600, color: "var(--text-primary)" }}>
                {selectedDevice.name}
              </div>
              <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
                {selectedDevice.driver} &middot; device.{selectedDevice.id}.*
              </div>
            </div>

            {stateEntries.length === 0 ? (
              <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)", fontStyle: "italic" }}>
                This driver does not declare any state properties.
              </div>
            ) : (
              <>
              {/* Column header */}
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", padding: "0 var(--space-md) 4px", fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
                <div style={{ flex: 1 }}>Property</div>
                <div style={{ width: 16, flexShrink: 0, textAlign: "center" }} title="Used in macros, UI, or scripts" />
                <div style={{ flexShrink: 0, minWidth: 80, textAlign: "right" }}>Live Value</div>
                <div style={{ width: 22, flexShrink: 0 }} />
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                {stateEntries.map((entry) => {
                  const meta = entry.meta;
                  const isSelected = selectedProp === entry.key;
                  const usageCount = usageMap.get(entry.key)?.length ?? 0;
                  return (
                    <div key={entry.key}>
                      <div
                        onClick={() => setSelectedProp(isSelected ? null : entry.key)}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: "var(--space-sm)",
                          padding: "var(--space-sm) var(--space-md)",
                          borderRadius: "var(--border-radius)",
                          background: isSelected ? "var(--bg-hover)" : "var(--bg-surface)",
                          border: "1px solid " + (isSelected ? "var(--accent)" : "var(--border-color)"),
                          cursor: "pointer",
                          transition: "background 0.1s",
                        }}
                        onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = "var(--bg-hover)"; }}
                        onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = "var(--bg-surface)"; }}
                      >
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                            <code style={{ fontFamily: "var(--font-mono)", fontSize: "var(--font-size-sm)", fontWeight: 500, color: "var(--text-primary)" }}>
                              {entry.prop}
                            </code>
                            {meta?.type && <span style={typeBadgeStyle}>{meta.type}</span>}
                            {meta?.label && (
                              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{meta.label}</span>
                            )}
                          </div>
                          <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                            {entry.key}
                          </div>
                        </div>
                        {/* Usage indicator */}
                        <div style={{ width: 16, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center" }} title={usageCount > 0 ? `Used in ${usageCount} place(s)` : "Not used yet"}>
                          {usageCount > 0 ? (
                            <Zap size={12} style={{ color: "#f59e0b" }} />
                          ) : (
                            <div style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--border-color)" }} />
                          )}
                        </div>
                        {/* Live value */}
                        <span style={{ fontSize: "var(--font-size-sm)", color: entry.value !== undefined ? "var(--text-secondary)" : "var(--text-muted)", fontFamily: "var(--font-mono)", flexShrink: 0, fontStyle: entry.value !== undefined ? "normal" : "italic", minWidth: 80, textAlign: "right" }}>
                          {entry.value !== undefined ? String(entry.value) : "—"}
                        </span>
                        <CopyButton value={entry.key} size={14} title="Copy state key" />
                      </div>

                      {/* Expanded detail: metadata + where used */}
                      {isSelected && (
                        <div style={{ padding: "var(--space-sm) var(--space-md) var(--space-md) var(--space-lg)", borderLeft: "2px solid var(--accent)", marginLeft: "var(--space-md)" }}>
                          {meta?.label && (
                            <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", marginBottom: "var(--space-xs)" }}>
                              {meta.label}
                            </div>
                          )}
                          {meta?.values && (
                            <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
                              Possible values: {meta.values.join(", ")}
                            </div>
                          )}

                          <h4 style={{ ...sectionTitle, fontSize: 11, marginBottom: "var(--space-sm)" }}>
                            Where Used ({selectedPropUsages.length})
                          </h4>
                          {selectedPropUsages.length === 0 ? (
                            <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", fontStyle: "italic" }}>
                              This property isn&apos;t used anywhere yet. You can reference it in
                              macro triggers, UI bindings, or scripts.
                            </div>
                          ) : (
                            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                              {selectedPropUsages.map((u, i) => (
                                <UsageRow key={i} usage={u} />
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
              </>
            )}
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-muted)", gap: "var(--space-sm)", padding: "var(--space-xl)", textAlign: "center" }}>
            <Cpu size={32} style={{ opacity: 0.3 }} />
            <div style={{ fontSize: "var(--font-size-md)" }}>Select a device</div>
            <div style={{ fontSize: "var(--font-size-sm)", maxWidth: 360, lineHeight: 1.5 }}>
              Choose a device from the list to browse its live state properties.
              You can copy state keys for use in macros, UI bindings, and scripts.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ==========================================================================
// Activity Sub-Tab
// ==========================================================================

function ActivitySubTab() {
  const [entries, setEntries] = useState<StateHistoryEntry[]>([]);
  const [filter, setFilter] = useState<"all" | "device" | "var" | "system">("all");
  const [loading, setLoading] = useState(true);

  // Poll for state history
  useEffect(() => {
    let cancelled = false;
    const fetchHistory = () => {
      getStateHistory(100)
        .then((data) => { if (!cancelled) { setEntries(data); setLoading(false); } })
        .catch(() => { if (!cancelled) setLoading(false); });
    };
    fetchHistory();
    const interval = setInterval(fetchHistory, 3000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  const filteredEntries = useMemo(() => {
    if (filter === "all") return entries;
    return entries.filter((e) => {
      if (filter === "device") return e.key.startsWith("device.");
      if (filter === "var") return e.key.startsWith("var.");
      if (filter === "system") return e.key.startsWith("system.");
      return true;
    });
  }, [entries, filter]);

  const formatTime = (ts: number) => {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  };

  const sourceColor = (source: string) => {
    switch (source) {
      case "device": return "#3b82f6";
      case "macro": return "#f59e0b";
      case "script": return "#10b981";
      case "api": return "#8b5cf6";
      case "ui": return "#ec4899";
      default: return "var(--text-muted)";
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <HelpBanner storageKey="openavc-help-activity">
        Every time a device property or variable changes, it appears here.
        The system is fully reactive — you never need to poll or check in a loop.
        Macros, UI bindings, and scripts all respond to these changes automatically.
      </HelpBanner>

      {/* Filter bar */}
      <div style={{ display: "flex", gap: "var(--space-sm)", padding: "var(--space-sm) var(--space-md)", borderBottom: "1px solid var(--border-color)", alignItems: "center" }}>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Filter:</span>
        {(["all", "device", "var", "system"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            style={{
              padding: "2px 10px",
              borderRadius: 12,
              fontSize: 11,
              border: "1px solid " + (filter === f ? "var(--accent)" : "var(--border-color)"),
              background: filter === f ? "rgba(33,150,243,0.15)" : "transparent",
              color: filter === f ? "var(--accent)" : "var(--text-secondary)",
              cursor: "pointer",
            }}
          >
            {f === "all" ? "All" : f === "var" ? "Variables" : f === "device" ? "Device" : "System"}
          </button>
        ))}
      </div>

      {/* Entries */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {loading ? (
          <div style={{ padding: "var(--space-xl)", textAlign: "center", color: "var(--text-muted)" }}>Loading...</div>
        ) : filteredEntries.length === 0 ? (
          <div style={{ padding: "var(--space-xl)", textAlign: "center", color: "var(--text-muted)", fontSize: "var(--font-size-sm)", fontStyle: "italic" }}>
            No state changes recorded yet. Start the system to see activity.
          </div>
        ) : (
          [...filteredEntries].reverse().map((entry, i) => (
            <div
              key={`${entry.key}-${entry.timestamp}-${i}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-sm)",
                padding: "4px var(--space-md)",
                fontSize: "var(--font-size-sm)",
                borderBottom: "1px solid var(--border-color)",
              }}
            >
              <span style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)", flexShrink: 0, width: 70 }}>
                {formatTime(entry.timestamp)}
              </span>
              <code style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {entry.key}
              </code>
              <span style={{ fontSize: 11, color: "var(--text-muted)", flexShrink: 0 }}>
                {entry.old_value !== null && entry.old_value !== undefined ? String(entry.old_value) : "null"}
              </span>
              <span style={{ fontSize: 11, color: "var(--text-muted)", flexShrink: 0 }}>
                &rarr;
              </span>
              <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-primary)", fontWeight: 500, flexShrink: 0 }}>
                {entry.new_value !== null && entry.new_value !== undefined ? String(entry.new_value) : "null"}
              </span>
              <span style={{
                fontSize: 10, padding: "0 6px", borderRadius: 8, flexShrink: 0,
                background: `${sourceColor(entry.source)}20`,
                color: sourceColor(entry.source),
                fontWeight: 500,
              }}>
                {entry.source}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// ==========================================================================
// Cross-reference logic
// ==========================================================================

/** Simple glob matcher for patterns like "device.*.power" */
function globMatch(pattern: string, key: string): boolean {
  if (pattern === key) return true;
  if (!pattern.includes("*")) return false;
  const regex = new RegExp("^" + pattern.replace(/\./g, "\\.").replace(/\*/g, "[^.]+") + "$");
  return regex.test(key);
}

/** Build usage map for var.* keys only (used in Variables sub-tab) */
function buildUsageMap(project: ProjectConfig, scriptRefs: ScriptReference[] = []): Map<string, VariableUsage[]> {
  const map = new Map<string, VariableUsage[]>();

  const addUsage = (varId: string, usage: VariableUsage) => {
    const list = map.get(varId) ?? [];
    list.push(usage);
    map.set(varId, list);
  };

  for (const macro of project.macros) {
    const macroNav = { view: "macros" as ViewId, focus: { type: "macro", id: macro.id } };
    for (const step of macro.steps) {
      if (step.action === "state.set" && step.key?.startsWith("var.")) {
        addUsage(step.key.slice(4), {
          type: "macro", icon: Zap, label: macro.name,
          detail: `Set Variable step → ${JSON.stringify(step.value)}`,
          nav: macroNav,
        });
      }
    }
    for (const trigger of macro.triggers ?? []) {
      if (trigger.state_key?.startsWith("var.")) {
        addUsage(trigger.state_key.slice(4), {
          type: "macro", icon: Zap, label: macro.name,
          detail: `Trigger "${trigger.id}" — state change on this variable`,
          nav: macroNav,
        });
      }
      for (const cond of trigger.conditions ?? []) {
        if (cond.key?.startsWith("var.")) {
          addUsage(cond.key.slice(4), {
            type: "macro", icon: Zap, label: macro.name,
            detail: `Trigger "${trigger.id}" — guard condition`,
            nav: macroNav,
          });
        }
      }
    }
  }

  for (const page of project.ui.pages) {
    for (const el of page.elements) {
      const elNav = { view: "ui-builder" as ViewId, focus: { type: "element", id: el.id, detail: `page:${page.id}` } };
      scanBindingForVars(el.bindings, (varId, detail) => {
        addUsage(varId, {
          type: "ui", icon: Layout,
          label: `${page.name} → ${el.label || el.type} (${el.id})`,
          detail,
          nav: elNav,
        });
      });
    }
  }

  // Script references
  for (const ref of scriptRefs) {
    if (!ref.key.startsWith("var.")) continue;
    const varId = ref.key.slice(4);
    const usageLabel = ref.usage_type === "subscribe" ? "@on_state_change" : ref.usage_type === "write" ? "state.set" : "state.get";
    addUsage(varId, {
      type: "script", icon: FileCode, label: ref.script_name,
      detail: `line ${ref.line} — ${usageLabel}`,
      nav: { view: "scripts", focus: { type: "script", id: ref.script_id, detail: `line:${ref.line}` } },
    });
  }

  return map;
}

/** Build usage map for ALL state keys (var.*, device.*, system.*) — used in Device States sub-tab */
function buildStateUsageMap(project: ProjectConfig, scriptRefs: ScriptReference[] = []): Map<string, VariableUsage[]> {
  const map = new Map<string, VariableUsage[]>();

  const addUsage = (key: string, usage: VariableUsage) => {
    const list = map.get(key) ?? [];
    list.push(usage);
    map.set(key, list);
  };

  for (const macro of project.macros) {
    const macroNav = { view: "macros" as ViewId, focus: { type: "macro", id: macro.id } };
    for (const step of macro.steps) {
      if (step.action === "state.set" && step.key) {
        addUsage(step.key, {
          type: "macro", icon: Zap, label: macro.name,
          detail: `Set Variable step → ${JSON.stringify(step.value)}`,
          nav: macroNav,
        });
      }
    }
    for (const trigger of macro.triggers ?? []) {
      if (trigger.state_key) {
        addUsage(trigger.state_key, {
          type: "macro", icon: Zap, label: macro.name,
          detail: `Trigger "${trigger.id}" — state change`,
          nav: macroNav,
        });
      }
      for (const cond of trigger.conditions ?? []) {
        if (cond.key) {
          addUsage(cond.key, {
            type: "macro", icon: Zap, label: macro.name,
            detail: `Trigger "${trigger.id}" — guard condition`,
            nav: macroNav,
          });
        }
      }
    }
  }

  for (const page of project.ui.pages) {
    for (const el of page.elements) {
      const elNav = { view: "ui-builder" as ViewId, focus: { type: "element", id: el.id, detail: `page:${page.id}` } };
      scanBindingForAllKeys(el.bindings, (key, detail) => {
        addUsage(key, {
          type: "ui", icon: Layout,
          label: `${page.name} → ${el.label || el.type} (${el.id})`,
          detail,
          nav: elNav,
        });
      });
    }
  }

  // Script references — match against all keys, supporting wildcards
  for (const ref of scriptRefs) {
    const usageLabel = ref.usage_type === "subscribe" ? "@on_state_change" : ref.usage_type === "write" ? "state.set" : "state.get";
    const scriptNav = { view: "scripts" as ViewId, focus: { type: "script", id: ref.script_id, detail: `line:${ref.line}` } };
    const entry: VariableUsage = {
      type: "script", icon: FileCode, label: ref.script_name,
      detail: `line ${ref.line} — ${usageLabel}`,
      nav: scriptNav,
    };
    if (ref.key.includes("*")) {
      // Wildcard pattern — add to all matching existing keys
      for (const existingKey of map.keys()) {
        if (globMatch(ref.key, existingKey)) {
          map.get(existingKey)!.push(entry);
        }
      }
    } else {
      addUsage(ref.key, entry);
    }
  }

  return map;
}

function scanBindingForVars(
  bindings: Record<string, unknown>,
  onFound: (varId: string, detail: string) => void,
) {
  if (!bindings) return;

  const checkKey = (obj: any, context: string) => {
    if (!obj || typeof obj !== "object") return;
    const key = obj.key as string | undefined;
    if (key?.startsWith("var.")) {
      onFound(key.slice(4), context);
    }
  };

  if (bindings.variable) checkKey(bindings.variable, "Two-way variable binding");
  if (bindings.text) checkKey(bindings.text, "Text display binding");
  if (bindings.feedback) checkKey(bindings.feedback, "Feedback/color binding");

  for (const eventType of ["press", "release", "change"]) {
    const binding = bindings[eventType] as Record<string, unknown> | undefined;
    if (!binding) continue;
    if (binding.action === "state.set" && typeof binding.key === "string" && binding.key.startsWith("var.")) {
      onFound(binding.key.slice(4), `${eventType} → Set Variable`);
    }
    if (binding.action === "value_map" && binding.map) {
      const actionMap = binding.map as Record<string, any>;
      for (const [optVal, subAction] of Object.entries(actionMap)) {
        if (subAction?.action === "state.set" && typeof subAction.key === "string" && subAction.key.startsWith("var.")) {
          onFound(subAction.key.slice(4), `${eventType} → ${optVal} → Set Variable`);
        }
      }
    }
  }

  if (bindings.value) checkKey(bindings.value, "Slider value source");
}

/** Scan bindings for ALL state key references (not just var.*) */
function scanBindingForAllKeys(
  bindings: Record<string, unknown>,
  onFound: (key: string, detail: string) => void,
) {
  if (!bindings) return;

  const checkKey = (obj: any, context: string) => {
    if (!obj || typeof obj !== "object") return;
    const key = obj.key as string | undefined;
    if (key) onFound(key, context);
  };

  if (bindings.variable) checkKey(bindings.variable, "Two-way binding");
  if (bindings.text) checkKey(bindings.text, "Text display binding");
  if (bindings.feedback) checkKey(bindings.feedback, "Feedback binding");
  if (bindings.color) checkKey(bindings.color, "Color binding");

  for (const eventType of ["press", "release", "change"]) {
    const binding = bindings[eventType] as Record<string, unknown> | undefined;
    if (!binding) continue;
    if (binding.action === "state.set" && typeof binding.key === "string") {
      onFound(binding.key, `${eventType} → Set state`);
    }
    if (binding.action === "value_map" && binding.map) {
      const actionMap = binding.map as Record<string, any>;
      for (const [optVal, subAction] of Object.entries(actionMap)) {
        if (subAction?.action === "state.set" && typeof subAction.key === "string") {
          onFound(subAction.key, `${eventType} → ${optVal} → Set state`);
        }
      }
    }
  }

  if (bindings.value) checkKey(bindings.value, "Slider value source");
}

function usageColor(type: string): string {
  switch (type) {
    case "macro": return "#f59e0b";
    case "ui": return "#3b82f6";
    case "script": return "#10b981";
    default: return "var(--text-muted)";
  }
}

// ==========================================================================
// Styles
// ==========================================================================

const subTabBarStyle: React.CSSProperties = {
  display: "flex",
  gap: 0,
  borderBottom: "1px solid var(--border-color)",
  flexShrink: 0,
};

const subTabBtnStyle: React.CSSProperties = {
  padding: "var(--space-sm) var(--space-lg)",
  background: "none",
  border: "none",
  fontSize: "var(--font-size-sm)",
  cursor: "pointer",
  transition: "color 0.15s",
};

const helpBannerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: "var(--space-sm)",
  padding: "var(--space-sm) var(--space-md)",
  background: "rgba(33,150,243,0.08)",
  borderBottom: "1px solid rgba(33,150,243,0.15)",
  fontSize: 12,
  color: "var(--text-secondary)",
  lineHeight: 1.5,
  fontStyle: "italic",
};

const headerBtnStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-xs)",
  padding: "var(--space-xs) var(--space-md)",
  borderRadius: "var(--border-radius)",
  background: "var(--accent)",
  color: "#fff",
  fontSize: "var(--font-size-sm)",
  border: "none",
  cursor: "pointer",
};

const searchInputStyle: React.CSSProperties = {
  width: "100%",
  padding: "var(--space-xs) var(--space-sm)",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-surface)",
  color: "var(--text-primary)",
};

const createFormStyle: React.CSSProperties = {
  padding: "var(--space-md)",
  borderBottom: "1px solid var(--border-color)",
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
  background: "var(--bg-surface)",
};

const miniLabel: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  color: "var(--text-muted)",
  marginBottom: 2,
};

const fieldInput: React.CSSProperties = {
  width: "100%",
  padding: "4px 6px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
};

const btnPrimary: React.CSSProperties = {
  padding: "4px 14px",
  borderRadius: "var(--border-radius)",
  background: "var(--accent)",
  color: "#fff",
  fontSize: "var(--font-size-sm)",
  border: "none",
  cursor: "pointer",
};

const btnSecondary: React.CSSProperties = {
  padding: "4px 14px",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  color: "var(--text-secondary)",
  fontSize: "var(--font-size-sm)",
  border: "none",
  cursor: "pointer",
};

const codeStyle: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-sm)",
};

const typeBadgeStyle: React.CSSProperties = {
  fontSize: 10,
  fontWeight: 600,
  color: "var(--text-muted)",
  background: "var(--bg-hover)",
  padding: "0 5px",
  borderRadius: 3,
  textTransform: "uppercase",
  letterSpacing: "0.5px",
};

const iconBtn: React.CSSProperties = {
  display: "flex",
  padding: 4,
  borderRadius: "var(--border-radius)",
  background: "transparent",
  color: "var(--text-muted)",
  border: "none",
  cursor: "pointer",
};

const detailLabel: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
  marginBottom: 4,
};

const detailInput: React.CSSProperties = {
  width: "100%",
  padding: "4px 8px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
};

const sectionTitle: React.CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
  fontWeight: 600,
  marginBottom: "var(--space-md)",
};

const usageRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  padding: "var(--space-sm) var(--space-md)",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-surface)",
  border: "1px solid var(--border-color)",
  fontSize: "var(--font-size-sm)",
};
