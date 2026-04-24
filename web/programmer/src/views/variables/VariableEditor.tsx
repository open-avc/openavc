import { useState, useMemo, useCallback, useEffect } from "react";
import { Plus, Trash2, HardDrive, X, Link, Pencil, LayoutDashboard } from "lucide-react";
import { CopyButton } from "../../components/shared/CopyButton";
import { ConfirmDialog } from "../../components/shared/ConfirmDialog";
import { VariableKeyPicker } from "../../components/shared/VariableKeyPicker";
import { useProjectStore } from "../../store/projectStore";
import { useConnectionStore } from "../../store/connectionStore";
import { getScriptReferences } from "../../api/restClient";
import { setStateValue } from "../../api/stateClient";
import type { VariableConfig, ScriptReference } from "../../api/types";
import { showError } from "../../store/toastStore";
import {
  type VariableUsage,
  HelpBanner, UsageRow, buildUsageMap,
  headerBtnStyle, searchInputStyle, createFormStyle, miniLabel, fieldInput,
  btnPrimary, btnSecondary, codeStyle, typeBadgeStyle, iconBtn,
  detailLabel, detailInput, sectionTitle,
} from "./variablesShared";

// ==========================================================================
// Variables Actions (header button)
// ==========================================================================

export function VariablesActions() {
  return (
    <div style={{ display: "flex", gap: "var(--space-sm)" }}>
      <button
        onClick={() => window.dispatchEvent(new CustomEvent("openavc:delete-unused-vars"))}
        style={{ ...headerBtnStyle, background: "var(--bg-hover)", color: "var(--text-secondary)" }}
        title="Delete all variables with zero usages"
      >
        <Trash2 size={14} /> Delete Unused
      </button>
      <button
        onClick={() => window.dispatchEvent(new CustomEvent("openavc:toggle-var-create"))}
        style={headerBtnStyle}
      >
        <Plus size={14} /> New Variable
      </button>
    </div>
  );
}

// ==========================================================================
// Variables Sub-Tab
// ==========================================================================

export function VariablesSubTab() {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const updateWithUndo = useProjectStore((s) => s.updateWithUndo);
  const liveState = useConnectionStore((s) => s.liveState);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [search, setSearch] = useState("");
  const [renameTarget, setRenameTarget] = useState<{ oldId: string; newId: string; usages: VariableUsage[] } | null>(null);
  const [newId, setNewId] = useState("");
  const [newType, setNewType] = useState("string");
  const [newLabel, setNewLabel] = useState("");
  const [newDefault, setNewDefault] = useState("");
  const [newDesc, setNewDesc] = useState("");
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

  const scriptCount = project?.scripts?.length ?? 0;
  const [scriptRefs, setScriptRefs] = useState<ScriptReference[]>([]);
  useEffect(() => {
    let cancelled = false;
    getScriptReferences()
      .then((refs) => { if (!cancelled) setScriptRefs(refs); })
      .catch(console.error);
    return () => { cancelled = true; };
  }, [scriptCount]);

  const usageMap = useMemo(() => {
    if (!project) return new Map<string, VariableUsage[]>();
    return buildUsageMap(project, scriptRefs);
  }, [project, scriptRefs]);

  // Listen for "delete unused" header button (10.7)
  useEffect(() => {
    const handler = () => {
      const currentUsageMap = buildUsageMap(useProjectStore.getState().project!, scriptRefs);
      const currentVars = useProjectStore.getState().project?.variables ?? [];
      const unused = currentVars.filter((v) => (currentUsageMap.get(v.id) ?? []).length === 0);
      if (unused.length === 0) {
        setPendingConfirm({
          title: "No Unused Variables",
          message: "All variables are referenced by at least one macro, UI element, or script.",
          confirmLabel: "OK",
          onConfirm: () => setPendingConfirm(null),
        });
        return;
      }
      setPendingConfirm({
        title: "Delete Unused Variables",
        message: (
          <>
            <div>{unused.length} variable(s) have no references and will be deleted:</div>
            <ul style={{ margin: "8px 0 0 16px", padding: 0, fontSize: 12 }}>
              {unused.map((v) => <li key={v.id}><code style={codeStyle}>var.{v.id}</code> ({v.label})</li>)}
            </ul>
          </>
        ),
        confirmLabel: `Delete ${unused.length}`,
        onConfirm: () => {
          const ids = new Set(unused.map((v) => v.id));
          updateWithUndo(
            { variables: currentVars.filter((v) => !ids.has(v.id)) },
            `Delete ${unused.length} unused variable(s)`
          );
          if (selectedId && ids.has(selectedId)) setSelectedId(null);
          useProjectStore.getState().debouncedSave();
          setPendingConfirm(null);
        },
      });
    };
    window.addEventListener("openavc:delete-unused-vars", handler);
    return () => window.removeEventListener("openavc:delete-unused-vars", handler);
  }, [scriptRefs, selectedId, updateWithUndo]);

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
      description: newDesc.trim(),
    };
    update({ variables: [...variables, newVar] });
    setNewId("");
    setNewType("string");
    setNewLabel("");
    setNewDefault("");
    setNewDesc("");
    setShowCreate(false);
    setSelectedId(id);
    useProjectStore.getState().debouncedSave();
  }, [variables, newType, newLabel, newDefault, newDesc, update]);

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
          useProjectStore.getState().debouncedSave();
        },
      });
    },
    [variables, usageMap, selectedId, updateWithUndo]
  );

  const handleUpdate = useCallback(
    (id: string, patch: Partial<VariableConfig>) => {
      update({
        variables: variables.map((v) => (v.id === id ? { ...v, ...patch } : v)),
      });
      useProjectStore.getState().debouncedSave(1500);
    },
    [variables, update]
  );

  const handleStartRename = useCallback((oldId: string) => {
    const usages = usageMap.get(oldId) ?? [];
    setRenameTarget({ oldId, newId: oldId, usages });
  }, [usageMap]);

  const handleConfirmRename = useCallback(() => {
    if (!renameTarget || !project) return;
    const { oldId, newId } = renameTarget;
    const safeNewId = newId.trim().replace(/[^a-zA-Z0-9_]/g, "_");
    if (!safeNewId || safeNewId === oldId) { setRenameTarget(null); return; }
    if (variables.some((v) => v.id === safeNewId)) {
      showError(`Variable "${safeNewId}" already exists.`);
      return;
    }

    const oldKey = `var.${oldId}`;
    const newKey = `var.${safeNewId}`;

    // Deep clone project data for modification
    const newVars = variables.map((v) => v.id === oldId ? { ...v, id: safeNewId } : v);

    const renameInSteps = (steps: typeof project.macros[0]["steps"]): [typeof steps, boolean] => {
      let changed = false;
      const mapped = steps.map((s) => {
        let step = s;
        if (step.action === "state.set" && step.key === oldKey) { step = { ...step, key: newKey }; changed = true; }
        if (step.action === "state.set" && step.value === `$${oldKey}`) { step = { ...step, value: `$${newKey}` }; changed = true; }
        if ((step.action === "conditional" || step.action === "wait_until") && step.condition?.key === oldKey) {
          step = { ...step, condition: { ...step.condition, key: newKey } }; changed = true;
        }
        if (step.skip_if?.key === oldKey) { step = { ...step, skip_if: { ...step.skip_if, key: newKey } }; changed = true; }
        if ((step.action === "device.command" || step.action === "group.command") && step.params) {
          const newParams: Record<string, unknown> = {};
          let paramChanged = false;
          for (const [pk, pv] of Object.entries(step.params)) {
            if (pv === `$${oldKey}`) { newParams[pk] = `$${newKey}`; paramChanged = true; }
            else newParams[pk] = pv;
          }
          if (paramChanged) { step = { ...step, params: newParams }; changed = true; }
        }
        if (step.then_steps) {
          const [ts, tc] = renameInSteps(step.then_steps);
          if (tc) { step = { ...step, then_steps: ts }; changed = true; }
        }
        if (step.else_steps) {
          const [es, ec] = renameInSteps(step.else_steps);
          if (ec) { step = { ...step, else_steps: es }; changed = true; }
        }
        return step;
      });
      return [mapped, changed];
    };

    const newMacros = project.macros.map((m) => {
      const [steps, changed] = renameInSteps(m.steps);
      const triggers = (m.triggers ?? []).map((t) => {
        let tc = false;
        const patched = { ...t };
        if (t.state_key === oldKey) { patched.state_key = newKey; tc = true; }
        const conditions = (t.conditions ?? []).map((c) => {
          if (c.key === oldKey) { tc = true; return { ...c, key: newKey }; }
          return c;
        });
        if (tc) patched.conditions = conditions;
        return tc ? patched : t;
      });
      return changed || triggers !== m.triggers ? { ...m, steps, triggers } : m;
    });
    const newPages = project.ui.pages.map((page) => ({
      ...page,
      elements: page.elements.map((el) => {
        const b = el.bindings;
        if (!b) return el;
        let modified = false;
        const nb = { ...b };
        for (const bk of ["variable", "text", "feedback", "value", "color"] as const) {
          const binding = nb[bk] as any;
          if (binding?.key === oldKey) { nb[bk] = { ...binding, key: newKey }; modified = true; }
        }
        for (const ev of ["press", "release", "change"] as const) {
          const binding = nb[ev] as any;
          if (binding?.action === "state.set" && binding?.key === oldKey) {
            nb[ev] = { ...binding, key: newKey }; modified = true;
          }
          if (binding?.action === "value_map" && binding?.map) {
            const actionMap = binding.map as Record<string, any>;
            let mapChanged = false;
            const newMap: Record<string, any> = {};
            for (const [optVal, subAction] of Object.entries(actionMap)) {
              if (subAction?.action === "state.set" && subAction?.key === oldKey) {
                newMap[optVal] = { ...subAction, key: newKey }; mapChanged = true;
              } else {
                newMap[optVal] = subAction;
              }
            }
            if (mapChanged) { nb[ev] = { ...binding, map: newMap }; modified = true; }
          }
        }
        return modified ? { ...el, bindings: nb } : el;
      }),
    }));

    updateWithUndo(
      { variables: newVars, macros: newMacros, ui: { ...project.ui, pages: newPages } },
      `Rename variable "${oldId}" to "${safeNewId}"`
    );
    setSelectedId(safeNewId);
    setRenameTarget(null);
    useProjectStore.getState().debouncedSave();
  }, [renameTarget, project, variables, updateWithUndo]);

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
                  onChange={(e) => setNewId(e.target.value.replace(/[^a-zA-Z0-9_]/g, ""))}
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
            <div>
              <label style={miniLabel}>Description</label>
              <input
                style={fieldInput}
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                placeholder="What this variable is for (optional)"
                onKeyDown={(e) => e.key === "Enter" && handleCreate()}
              />
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
                  <br /><br />
                  <a href="https://docs.openavc.com/variables-and-state" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
                    Learn about variables and state
                  </a>
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
                    {v.description && (
                      <div style={{ fontSize: 10, color: "var(--text-muted)", opacity: 0.7, marginTop: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {v.description}
                      </div>
                    )}
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
                <button
                  onClick={() => handleStartRename(selectedVar.id)}
                  style={{ ...iconBtn, marginLeft: "var(--space-sm)" }}
                  title="Rename variable (updates all references)"
                >
                  <Pencil size={14} />
                </button>
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
                  onChange={(e) => {
                    const newType = e.target.value;
                    const patch: Partial<VariableConfig> = { type: newType };
                    if (newType === "boolean") patch.default = Boolean(selectedVar.default);
                    else if (newType === "number") patch.default = Number(selectedVar.default) || 0;
                    else patch.default = selectedVar.default != null ? String(selectedVar.default) : "";
                    const cleanValidation = { ...selectedVar.validation };
                    if (newType !== "number") { cleanValidation.min = undefined; cleanValidation.max = undefined; }
                    if (newType !== "string") { cleanValidation.allowed = undefined; }
                    patch.validation = cleanValidation;
                    handleUpdate(selectedVar.id, patch);
                  }}
                >
                  <option value="string">String</option>
                  <option value="boolean">Boolean</option>
                  <option value="number">Number</option>
                </select>
              </div>
              <div>
                <label style={detailLabel}>Default Value</label>
                {selectedVar.type === "boolean" ? (
                  <select style={detailInput} value={String(selectedVar.default ?? false)} onChange={(e) => handleUpdate(selectedVar.id, { default: e.target.value === "true" })}>
                    <option value="false">false</option>
                    <option value="true">true</option>
                  </select>
                ) : (
                  <input
                    style={detailInput}
                    type={selectedVar.type === "number" ? "number" : "text"}
                    value={selectedVar.default != null ? String(selectedVar.default) : ""}
                    onChange={(e) => {
                      const v = e.target.value;
                      if (selectedVar.type === "number") handleUpdate(selectedVar.id, { default: v === "" ? 0 : Number(v) });
                      else handleUpdate(selectedVar.id, { default: v });
                    }}
                    placeholder={selectedVar.type === "number" ? "0" : ""}
                  />
                )}
              </div>
              <div>
                <label style={detailLabel}>Current Value</label>
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                  <span style={{ fontSize: "var(--font-size-sm)", color: selectedLiveValue !== undefined ? "var(--text-primary)" : "var(--text-muted)", fontWeight: 500 }}>
                    {selectedLiveValue !== undefined ? JSON.stringify(selectedLiveValue) : "not set"}
                  </span>
                  {selectedLiveValue !== undefined && (
                    <button
                      onClick={() => {
                        const val = prompt("Set value for var." + selectedVar.id + ":", String(selectedLiveValue ?? ""));
                        if (val === null) return;
                        let parsed: unknown = val;
                        if (selectedVar.type === "boolean") parsed = val === "true";
                        else if (selectedVar.type === "number") parsed = Number(val) || 0;
                        setStateValue(`var.${selectedVar.id}`, parsed).catch(() => showError("Failed to set value"));
                      }}
                      style={{ ...iconBtn, fontSize: 11, padding: "1px 6px", border: "1px solid var(--border-color)", borderRadius: "var(--border-radius)" }}
                      title="Set current value"
                    >
                      Set
                    </button>
                  )}
                </div>
              </div>
            </div>

            {/* Description (10.1) */}
            <div style={{ maxWidth: 500, marginBottom: "var(--space-xl)" }}>
              <label style={detailLabel}>Description</label>
              <input
                style={detailInput}
                value={selectedVar.description ?? ""}
                onChange={(e) => handleUpdate(selectedVar.id, { description: e.target.value })}
                placeholder="What this variable is for..."
              />
            </div>

            {/* Validation rules (10.3) */}
            <div style={{ maxWidth: 500, marginBottom: "var(--space-xl)" }}>
              <label style={detailLabel}>Validation Rules</label>
              {selectedVar.type === "number" ? (
                <div style={{ display: "flex", gap: "var(--space-md)", alignItems: "center" }}>
                  <div style={{ flex: 1 }}>
                    <label style={{ ...miniLabel, marginBottom: 2 }}>Min</label>
                    <input
                      type="number"
                      style={detailInput}
                      value={selectedVar.validation?.min ?? ""}
                      onChange={(e) => {
                        const val = e.target.value === "" ? null : Number(e.target.value);
                        handleUpdate(selectedVar.id, {
                          validation: { ...selectedVar.validation, min: val },
                        });
                      }}
                      placeholder="No minimum"
                    />
                  </div>
                  <div style={{ flex: 1 }}>
                    <label style={{ ...miniLabel, marginBottom: 2 }}>Max</label>
                    <input
                      type="number"
                      style={detailInput}
                      value={selectedVar.validation?.max ?? ""}
                      onChange={(e) => {
                        const val = e.target.value === "" ? null : Number(e.target.value);
                        handleUpdate(selectedVar.id, {
                          validation: { ...selectedVar.validation, max: val },
                        });
                      }}
                      placeholder="No maximum"
                    />
                  </div>
                </div>
              ) : selectedVar.type === "string" ? (
                <div>
                  <label style={{ ...miniLabel, marginBottom: 2 }}>Allowed Values (one per line, leave empty for any)</label>
                  <textarea
                    style={{ ...detailInput, minHeight: 60, resize: "vertical", fontFamily: "var(--font-mono)", fontSize: 12 }}
                    value={(selectedVar.validation?.allowed ?? []).join("\n")}
                    onChange={(e) => {
                      const raw = e.target.value;
                      const allowed = raw ? raw.split("\n").filter((s) => s.length > 0) : null;
                      handleUpdate(selectedVar.id, {
                        validation: { ...selectedVar.validation, allowed: allowed && allowed.length > 0 ? allowed : null },
                      });
                    }}
                    placeholder={"e.g.\npresentation\nmeeting\nstandby"}
                  />
                </div>
              ) : (
                <div style={{ fontSize: 12, color: "var(--text-muted)", fontStyle: "italic" }}>
                  Boolean variables don't need validation rules.
                </div>
              )}
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                When a value is set outside these rules, a warning appears in the Activity log.
              </div>
              {selectedLiveValue !== undefined && (() => {
                const v = selectedVar.validation;
                if (!v) return null;
                if (selectedVar.type === "number" && typeof selectedLiveValue === "number") {
                  if (v.min != null && selectedLiveValue < v.min) return <div style={{ fontSize: 12, color: "#ef4444", fontWeight: 500, marginTop: 4 }}>Current value {selectedLiveValue} is below minimum ({v.min})</div>;
                  if (v.max != null && selectedLiveValue > v.max) return <div style={{ fontSize: 12, color: "#ef4444", fontWeight: 500, marginTop: 4 }}>Current value {selectedLiveValue} is above maximum ({v.max})</div>;
                }
                if (selectedVar.type === "string" && v.allowed && v.allowed.length > 0 && typeof selectedLiveValue === "string") {
                  if (!v.allowed.includes(selectedLiveValue)) return <div style={{ fontSize: 12, color: "#ef4444", fontWeight: 500, marginTop: 4 }}>Current value "{selectedLiveValue}" is not in allowed values</div>;
                }
                return null;
              })()}
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

      {/* Rename dialog (10.5) */}
      {renameTarget && (
        <div
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}
          onClick={() => setRenameTarget(null)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{ background: "var(--bg-surface)", border: "1px solid var(--border-color)", borderRadius: "var(--border-radius)", padding: "var(--space-lg)", width: "min(440px, 90vw)", boxShadow: "0 8px 32px rgba(0,0,0,0.4)" }}
          >
            <div style={{ fontWeight: 600, fontSize: "var(--font-size-md)", color: "var(--text-primary)", marginBottom: "var(--space-md)" }}>
              Rename Variable
            </div>
            <div style={{ marginBottom: "var(--space-md)" }}>
              <label style={miniLabel}>Current: <code style={codeStyle}>var.{renameTarget.oldId}</code></label>
              <input
                style={{ ...detailInput, marginTop: 4 }}
                value={renameTarget.newId}
                onChange={(e) => setRenameTarget({ ...renameTarget, newId: e.target.value.replace(/[^a-zA-Z0-9_]/g, "") })}
                placeholder="new_variable_id"
                autoFocus
                onKeyDown={(e) => e.key === "Enter" && handleConfirmRename()}
              />
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                New key: <code style={codeStyle}>var.{renameTarget.newId.trim() || "..."}</code>
              </div>
            </div>
            {renameTarget.usages.length > 0 && (
              <div style={{ marginBottom: "var(--space-md)", padding: "var(--space-sm)", background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.2)", borderRadius: "var(--border-radius)", fontSize: 12 }}>
                <div style={{ fontWeight: 600, color: "#f59e0b", marginBottom: 4 }}>
                  {renameTarget.usages.length} reference(s) will be updated:
                </div>
                {renameTarget.usages.slice(0, 8).map((u, i) => (
                  <div key={i} style={{ color: "var(--text-secondary)", fontSize: 11, padding: "1px 0" }}>
                    {u.label} — {u.detail}
                  </div>
                ))}
                {renameTarget.usages.length > 8 && (
                  <div style={{ color: "var(--text-muted)", fontSize: 11 }}>...and {renameTarget.usages.length - 8} more</div>
                )}
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4, fontStyle: "italic" }}>
                  Script references (.py files) must be updated manually.
                </div>
              </div>
            )}
            <div style={{ display: "flex", gap: "var(--space-sm)", justifyContent: "flex-end" }}>
              <button onClick={() => setRenameTarget(null)} style={btnSecondary}>Cancel</button>
              <button
                onClick={handleConfirmRename}
                disabled={!renameTarget.newId.trim() || renameTarget.newId.trim() === renameTarget.oldId}
                style={{ ...btnPrimary, opacity: !renameTarget.newId.trim() || renameTarget.newId.trim() === renameTarget.oldId ? 0.5 : 1 }}
              >
                Rename
              </button>
            </div>
          </div>
        </div>
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
  const isBound = variable.source_key != null;
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
    if (newKey !== oldKey && newKey in sourceMap) return;
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

          {variable.source_key && sourceValue === undefined && (
            <div style={{ fontSize: 12, color: "#f59e0b", fontStyle: "italic" }}>
              Source key "{variable.source_key}" has no value. The device may be offline or the key may not exist.
            </div>
          )}

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
