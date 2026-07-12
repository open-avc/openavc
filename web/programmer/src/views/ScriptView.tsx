import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { Save, Play, FileCode, ChevronDown, RefreshCw } from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { ScriptFileTree } from "../components/scripts/ScriptFileTree";
import { ScriptEditor, type RuntimeError } from "../components/scripts/ScriptEditor";
import { ScriptConsole } from "../components/scripts/ScriptConsole";
import { ConfirmDialog } from "../components/shared/ConfirmDialog";
import { CreateDriverDialog } from "../components/scripts/CreateDriverDialog";
import { SCRIPT_TEMPLATES } from "../components/scripts/scriptTemplates";
import { DRIVER_TEMPLATES } from "../components/scripts/driverTemplates";
import { useProjectStore } from "../store/projectStore";
import { useNavigationStore } from "../store/navigationStore";
import { useLogStore } from "../store/logStore";
import * as api from "../api/restClient";
import { showError, showSuccess } from "../store/toastStore";
import type { PythonDriverInfo } from "../api/types";

export function ScriptView() {
  const project = useProjectStore((s) => s.project);
  const load = useProjectStore((s) => s.load);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedType, setSelectedType] = useState<"script" | "driver" | null>(null);
  const [source, setSource] = useState("");
  const [originalSource, setOriginalSource] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [showTemplates, setShowTemplates] = useState(false);
  const [showCreateDriver, setShowCreateDriver] = useState(false);
  const [pendingConfirm, setPendingConfirm] = useState<{ title: string; message: string; confirmLabel: string; onConfirm: () => void } | null>(null);
  const [scriptLoadErrors, setScriptLoadErrors] = useState<Record<string, string>>({});
  const [pythonDrivers, setPythonDrivers] = useState<PythonDriverInfo[]>([]);
  const [driverReloadErrors, setDriverReloadErrors] = useState<RuntimeError[]>([]);

  const editorInstanceRef = useRef<any>(null);
  const pendingLineRef = useRef<number | null>(null);
  const driverFileInputRef = useRef<HTMLInputElement>(null);

  // Fetch script load errors and Python drivers on mount
  useEffect(() => {
    api.getScriptErrors().then(setScriptLoadErrors).catch(() => {});
    loadPythonDrivers();
  }, []);

  const loadPythonDrivers = useCallback(async (): Promise<PythonDriverInfo[]> => {
    try {
      const result = await api.getPythonDrivers();
      setPythonDrivers(result.drivers);
      return result.drivers;
    } catch {
      // Silently handle — driver list is optional
      return [];
    }
  }, []);

  // Reactive pending-focus consume lives after the selection handlers (below),
  // so its deps can reference them without hitting the temporal dead zone.
  const pendingFocus = useNavigationStore((s) => s.pendingFocus);

  const scripts = project?.scripts ?? [];
  const isDirty = source !== originalSource;

  // Warn before closing tab with unsaved changes
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (isDirty) { e.preventDefault(); }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  // Extract runtime errors from log entries for the selected item
  const runtimeErrors = useMemo((): RuntimeError[] => {
    if (!selectedId) return [];
    if (selectedType === "driver") return driverReloadErrors;
    const entries = useLogStore.getState().logEntries;
    const errors: RuntimeError[] = [];
    const scriptFile = scripts.find((s) => s.id === selectedId)?.file ?? selectedId;
    for (const entry of entries) {
      if (entry.level !== "ERROR" || entry.category !== "script") continue;
      if (!entry.message.includes(selectedId) && !entry.message.includes(scriptFile)) continue;
      const lineMatch = entry.message.match(/line (\d+)/);
      if (lineMatch) {
        errors.push({ line: parseInt(lineMatch[1], 10), message: entry.message.split("\n")[0] });
      }
    }
    return errors;
  }, [selectedId, selectedType, scripts, driverReloadErrors]);

  // --- Selection handlers ---

  const doSelect = useCallback(async (id: string, type: "script" | "driver") => {
    setSelectedId(id);
    setSelectedType(type);
    setDriverReloadErrors([]);
    setLoading(true);
    try {
      const result = type === "script"
        ? await api.getScriptSource(id)
        : await api.getPythonDriverSource(id);
      setSource(result.source);
      setOriginalSource(result.source);
    } catch (e) {
      console.error(`Failed to load ${type}:`, e);
      setSource(`# Error loading ${type}: ${e}`);
      setOriginalSource("");
    } finally {
      setLoading(false);
    }
  }, []);

  const handleSelectScript = useCallback((id: string) => {
    if (isDirty && selectedId) {
      setPendingConfirm({
        title: "Unsaved Changes",
        message: "You have unsaved changes. Switch and discard them?",
        confirmLabel: "Discard & Switch",
        onConfirm: () => { setPendingConfirm(null); doSelect(id, "script"); },
      });
      return;
    }
    doSelect(id, "script");
  }, [isDirty, selectedId, doSelect]);

  const handleSelectDriver = useCallback((id: string) => {
    if (isDirty && selectedId) {
      setPendingConfirm({
        title: "Unsaved Changes",
        message: "You have unsaved changes. Switch and discard them?",
        confirmLabel: "Discard & Switch",
        onConfirm: () => { setPendingConfirm(null); doSelect(id, "driver"); },
      });
      return;
    }
    doSelect(id, "driver");
  }, [isDirty, selectedId, doSelect]);

  // Act on a pending focus target (e.g. a console "line N" click). Subscribing to
  // pendingFocus rather than reading once on mount is what makes the links work
  // when already on the Script view: App.tsx keys views by activeView, so a
  // same-view navigateTo doesn't remount this component.
  useEffect(() => {
    if (pendingFocus?.type !== "script" && pendingFocus?.type !== "python_driver") return;
    const focus = useNavigationStore.getState().consumeFocus();
    if (!focus) return;
    const line = focus.detail?.startsWith("line:") ? parseInt(focus.detail.slice(5), 10) : null;
    const targetType: "script" | "driver" = focus.type === "python_driver" ? "driver" : "script";
    const alreadyOpen = !!focus.id && focus.id === selectedId && targetType === selectedType;

    if (focus.id && !alreadyOpen) {
      // A different file: open it; onEditorReady runs the jump once it mounts.
      if (line !== null) pendingLineRef.current = line;
      if (targetType === "script") handleSelectScript(focus.id);
      else handleSelectDriver(focus.id);
    } else if (line !== null) {
      // Target already open (or no id given): jump the live editor directly.
      const editor = editorInstanceRef.current;
      if (editor) {
        editor.revealLineInCenter(line);
        editor.setPosition({ lineNumber: line, column: 1 });
        editor.focus();
      } else {
        pendingLineRef.current = line;
      }
    }
  }, [pendingFocus, selectedId, selectedType, handleSelectScript, handleSelectDriver]);

  // --- Save handlers ---

  const handleSave = useCallback(async () => {
    if (!selectedId || !selectedType) return;
    setSaving(true);
    try {
      if (selectedType === "script") {
        await api.saveScriptSource(selectedId, source);
      } else {
        await api.savePythonDriverSource(selectedId, source);
      }
      setOriginalSource(source);
    } catch (e) {
      console.error(`Failed to save ${selectedType}:`, e);
      showError(`Save failed: ${e}`);
    } finally {
      setSaving(false);
    }
  }, [selectedId, selectedType, source]);

  // --- Reload handlers ---

  const handleReloadScript = useCallback(async () => {
    if (!selectedId || selectedType !== "script") return;

    // Save first if dirty
    if (isDirty) {
      setSaving(true);
      try {
        await api.saveScriptSource(selectedId, source);
        setOriginalSource(source);
      } catch (e) {
        showError(`Save failed: ${e}`);
        setSaving(false);
        return;
      }
      setSaving(false);
    }

    // Reload just this script — peers' handlers and timers keep running, and
    // the previously loaded version stays active if the new one fails.
    setReloading(true);
    try {
      const result = await api.reloadScript(selectedId);
      setScriptLoadErrors(result.errors ?? {});
      if (result.status === "error") {
        const preserved = result.old_script_preserved
          ? " The previously loaded version is still active."
          : "";
        showError(`Script reload failed: ${result.error}${preserved}`);
        useLogStore.getState().addLogEntry({
          timestamp: Date.now() / 1000,
          level: "ERROR",
          source: "openavc.programmer",
          device: "",
          category: "script",
          message: `Script '${selectedId}' reload failed: ${result.error}${preserved}`,
        });
      } else {
        showSuccess(`Script reloaded — ${result.handlers ?? 0} handler(s)`);
        useLogStore.getState().addLogEntry({
          timestamp: Date.now() / 1000,
          level: "INFO",
          source: "openavc.programmer",
          device: "",
          category: "script",
          message: `Script '${selectedId}' reloaded — ${result.handlers ?? 0} handler(s) registered`,
        });
      }
    } catch (e) {
      showError(`Script reload failed: ${e}`);
      useLogStore.getState().addLogEntry({
        timestamp: Date.now() / 1000,
        level: "ERROR",
        source: "openavc.programmer",
        device: "",
        category: "script",
        message: `Script reload failed: ${e}`,
      });
    } finally {
      setReloading(false);
    }
  }, [selectedId, selectedType, source, isDirty]);

  const handleReloadDriver = useCallback(async () => {
    if (!selectedId || selectedType !== "driver") return;

    // Save first if dirty
    if (isDirty) {
      setSaving(true);
      try {
        await api.savePythonDriverSource(selectedId, source);
        setOriginalSource(source);
      } catch (e) {
        showError(`Save failed: ${e}`);
        setSaving(false);
        return;
      }
      setSaving(false);
    }

    // Reload driver
    setReloading(true);
    try {
      const result = await api.reloadPythonDriver(selectedId);

      if (result.status === "error") {
        // Reassure the operator the room isn't down when the previous driver
        // is still serving devices (validation/import/reload failed safely).
        const preserved = result.old_driver_preserved
          ? " The previously loaded driver is still active."
          : "";
        showError(`Driver reload failed: ${result.error}${preserved}`);
        // Show error marker on the offending line
        if (result.line) {
          setDriverReloadErrors([{ line: result.line, message: result.error ?? "Reload error" }]);
        }
        useLogStore.getState().addLogEntry({
          timestamp: Date.now() / 1000,
          level: "ERROR",
          source: "openavc.programmer",
          device: "",
          category: "driver",
          message: `Driver reload failed: ${result.error}${preserved}`,
        });
      } else {
        setDriverReloadErrors([]);
        const devCount = result.devices_reconnected?.length ?? 0;
        showSuccess(devCount > 0
          ? `Driver reloaded — ${devCount} device(s) reconnected`
          : "Driver reloaded");
        useLogStore.getState().addLogEntry({
          timestamp: Date.now() / 1000,
          level: "INFO",
          source: "openavc.programmer",
          device: "",
          category: "driver",
          message: devCount > 0
            ? `Driver '${result.driver_id}' reloaded — ${devCount} device(s) reconnected: ${result.devices_reconnected!.join(", ")}`
            : `Driver '${result.driver_id}' reloaded — no devices affected`,
        });
      }
      // Refresh driver list
      await loadPythonDrivers();
    } catch (e) {
      showError(`Driver reload failed: ${e}`);
      useLogStore.getState().addLogEntry({
        timestamp: Date.now() / 1000,
        level: "ERROR",
        source: "openavc.programmer",
        device: "",
        category: "driver",
        message: `Driver reload failed: ${e}`,
      });
    } finally {
      setReloading(false);
    }
  }, [selectedId, selectedType, source, isDirty, loadPythonDrivers]);

  // Keyboard shortcut: Ctrl+Shift+R to save & reload
  const handleReloadRef = useRef(selectedType === "driver" ? handleReloadDriver : handleReloadScript);
  handleReloadRef.current = selectedType === "driver" ? handleReloadDriver : handleReloadScript;
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && e.key === "R") {
        e.preventDefault();
        handleReloadRef.current();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  // --- Create handlers ---

  const handleCreateScript = useCallback(
    async (id: string, file: string, description: string) => {
      try {
        await api.createScript({
          id,
          file,
          description,
          source: `"""${description || id}"""\nfrom openavc import on_event, state, log\n\n`,
        });
        await load();
        doSelect(id, "script");
      } catch (e) {
        showError(`Create failed: ${e}`);
      }
    },
    [load, doSelect]
  );

  const handleCreateDriver = useCallback(
    async (id: string, source: string) => {
      try {
        await api.createPythonDriver({ id, source });
        await loadPythonDrivers();
        setShowCreateDriver(false);
        doSelect(id, "driver");
      } catch (e) {
        showError(`Create failed: ${e}`);
      }
    },
    [loadPythonDrivers, doSelect]
  );

  const handleImportDriverFile = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      // Reset the input so the same file can be re-selected later.
      if (driverFileInputRef.current) driverFileInputRef.current.value = "";
      if (!file) return;
      const isZip = file.name.toLowerCase().endsWith(".zip");
      try {
        const result = isZip
          ? await api.importDriverBundle(file)
          : await api.uploadDriver(file);
        const drivers = await loadPythonDrivers();
        const activated = result.activated_devices ?? [];
        const extra = activated.length > 0 ? ` — connected ${activated.length} waiting device(s)` : "";
        showSuccess(`Imported driver "${result.driver_id}"${extra}`);
        // A YAML driver (e.g. an .avcdriver inside a bundle) lives in the
        // Driver Builder, not this tree — only open it here when it actually
        // shows up as a Python driver.
        if (drivers.some((d) => d.id === result.driver_id)) {
          doSelect(result.driver_id, "driver");
        }
      } catch (err) {
        showError(`Import failed: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
    [loadPythonDrivers, doSelect]
  );

  const handleExportDriver = useCallback(async (id: string) => {
    try {
      await api.downloadDriverBundle(id);
    } catch (e) {
      showError(`Export failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, []);

  // --- Delete handlers ---

  const handleDeleteScript = useCallback(
    (id: string) => {
      const scriptName = scripts.find((s) => s.id === id)?.file || id;
      setPendingConfirm({
        title: "Delete Script",
        message: `Delete script "${scriptName}"? If this script has event handlers (@on_event, @on_state_change), those handlers will stop working.`,
        confirmLabel: "Delete",
        onConfirm: async () => {
          setPendingConfirm(null);
          try {
            await api.deleteScript(id);
            await load();
            if (selectedId === id && selectedType === "script") {
              setSelectedId(null);
              setSelectedType(null);
              setSource("");
              setOriginalSource("");
            }
          } catch (e) {
            showError(`Delete failed: ${e}`);
          }
        },
      });
    },
    [selectedId, selectedType, scripts, load]
  );

  const handleDeleteDriver = useCallback(
    (id: string) => {
      const driver = pythonDrivers.find((d) => d.id === id);
      if (driver && driver.devices_using.length > 0) {
        showError(`Cannot delete: driver is used by ${driver.devices_using.join(", ")}`);
        return;
      }
      setPendingConfirm({
        title: "Delete Driver",
        message: `Delete Python driver "${driver?.name || id}"? This will remove the driver file from driver_repo/.`,
        confirmLabel: "Delete",
        onConfirm: async () => {
          setPendingConfirm(null);
          try {
            await api.deletePythonDriver(id);
            await loadPythonDrivers();
            if (selectedId === id && selectedType === "driver") {
              setSelectedId(null);
              setSelectedType(null);
              setSource("");
              setOriginalSource("");
            }
          } catch (e) {
            showError(`Delete failed: ${e}`);
          }
        },
      });
    },
    [selectedId, selectedType, pythonDrivers, loadPythonDrivers]
  );

  // --- Template insertion ---

  const handleInsertTemplate = useCallback(
    (code: string) => {
      if (source.trim() && source !== originalSource) {
        setPendingConfirm({
          title: "Replace Content",
          message: "Replace current editor content with this template? Unsaved changes will be lost.",
          confirmLabel: "Replace",
          onConfirm: () => { setPendingConfirm(null); setSource(code); setShowTemplates(false); },
        });
        return;
      }
      setSource(code);
      setShowTemplates(false);
    },
    [source, originalSource]
  );

  // Which templates to show based on mode
  const activeTemplates = selectedType === "driver" ? DRIVER_TEMPLATES : SCRIPT_TEMPLATES;
  const selectedDriverInfo = selectedType === "driver"
    ? pythonDrivers.find((d) => d.id === selectedId)
    : null;
  const templateItems = selectedType === "driver"
    ? activeTemplates.map((t) => ({
        name: (t as any).name,
        description: (t as any).description,
        code: (t as any).generateCode({
          id: selectedId ?? "my_driver",
          name: selectedDriverInfo?.name ?? selectedId ?? "My Driver",
          manufacturer: selectedDriverInfo?.manufacturer ?? "",
          category: selectedDriverInfo?.category ?? "utility",
          transport: "tcp",
        }),
      }))
    : (activeTemplates as any[]);

  return (
    <ViewContainer
      title="Code"
      actions={
        selectedId ? (
          <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center" }}>
            {/* Templates dropdown */}
            <div style={{ position: "relative" }}>
              <button
                onClick={() => setShowTemplates(!showTemplates)}
                style={actionBtnStyle}
              >
                <FileCode size={14} />
                Templates
                <ChevronDown size={12} />
              </button>
              {showTemplates && (
                <div
                  style={{
                    position: "absolute",
                    top: "100%",
                    right: 0,
                    marginTop: 4,
                    background: "var(--bg-surface)",
                    border: "1px solid var(--border-color)",
                    borderRadius: "var(--border-radius)",
                    boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
                    zIndex: 20,
                    minWidth: 220,
                  }}
                >
                  {templateItems.map((t: any) => (
                    <div
                      key={t.name}
                      onClick={() => handleInsertTemplate(t.code)}
                      style={{
                        padding: "var(--space-sm) var(--space-md)",
                        cursor: "pointer",
                        fontSize: "var(--font-size-sm)",
                      }}
                      onMouseEnter={(e) =>
                        ((e.currentTarget as HTMLElement).style.background =
                          "var(--bg-hover)")
                      }
                      onMouseLeave={(e) =>
                        ((e.currentTarget as HTMLElement).style.background =
                          "transparent")
                      }
                    >
                      <div style={{ fontWeight: 500, color: "var(--text-primary)" }}>
                        {t.name}
                      </div>
                      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                        {t.description}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <button
              onClick={handleSave}
              disabled={!isDirty || saving}
              style={{
                ...actionBtnStyle,
                opacity: isDirty ? 1 : 0.5,
              }}
            >
              <Save size={14} />
              {saving ? "Saving..." : "Save"}
            </button>

            {selectedType === "driver" ? (
              <button
                onClick={handleReloadDriver}
                disabled={reloading}
                title="Save and hot-reload the driver (Ctrl+Shift+R)"
                style={{
                  ...actionBtnStyle,
                  background: "var(--accent-bg)",
                  color: "#fff",
                }}
              >
                <RefreshCw size={14} />
                {reloading ? "Reloading..." : "Save & Reload Driver"}
              </button>
            ) : (
              <button
                onClick={handleReloadScript}
                disabled={reloading}
                title="Save and hot-reload this script. Other scripts keep running (Ctrl+Shift+R)"
                style={{
                  ...actionBtnStyle,
                  background: "var(--accent-bg)",
                  color: "#fff",
                }}
              >
                <Play size={14} />
                {reloading ? "Reloading..." : "Save & Reload Script"}
              </button>
            )}
          </div>
        ) : undefined
      }
    >
      {/* Hidden picker for importing a driver file (.py) or bundle (.zip) */}
      <input
        ref={driverFileInputRef}
        type="file"
        accept=".zip,.py"
        style={{ display: "none" }}
        onChange={handleImportDriverFile}
      />
      <PanelGroup direction="horizontal" style={{ height: "100%" }}>
        {/* File tree */}
        <Panel defaultSize={20} minSize={15} maxSize={35}>
          <ScriptFileTree
            scripts={scripts}
            drivers={pythonDrivers}
            selectedId={selectedId}
            selectedType={selectedType}
            loadErrors={scriptLoadErrors}
            onSelectScript={handleSelectScript}
            onSelectDriver={handleSelectDriver}
            onCreateScript={handleCreateScript}
            onCreateDriver={() => setShowCreateDriver(true)}
            onImportDriver={() => driverFileInputRef.current?.click()}
            onExportDriver={handleExportDriver}
            onDeleteScript={handleDeleteScript}
            onDeleteDriver={handleDeleteDriver}
          />
        </Panel>

        <PanelResizeHandle
          style={{
            width: 4,
            background: "var(--border-color)",
            cursor: "col-resize",
          }}
        />

        {/* Editor + Console */}
        <Panel defaultSize={80}>
          {selectedId ? (
            <PanelGroup direction="vertical">
              {/* Editor */}
              <Panel defaultSize={70} minSize={30}>
                <div style={{ height: "100%", overflow: "hidden" }}>
                  {loading ? (
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        height: "100%",
                        color: "var(--text-muted)",
                      }}
                    >
                      Loading...
                    </div>
                  ) : (
                    <ScriptEditor
                      source={source}
                      onChange={setSource}
                      runtimeErrors={runtimeErrors}
                      editorMode={selectedType ?? "script"}
                      onEditorReady={(editor) => {
                        editorInstanceRef.current = editor;
                        if (pendingLineRef.current) {
                          const line = pendingLineRef.current;
                          pendingLineRef.current = null;
                          setTimeout(() => {
                            editor.revealLineInCenter(line);
                            editor.setPosition({ lineNumber: line, column: 1 });
                            editor.focus();
                          }, 50);
                        }
                      }}
                    />
                  )}
                </div>
              </Panel>

              <PanelResizeHandle
                style={{
                  height: 4,
                  background: "var(--border-color)",
                  cursor: "row-resize",
                }}
              />

              {/* Console */}
              <Panel defaultSize={30} minSize={15}>
                {selectedType === "driver" ? (
                  <ScriptConsole
                    filterCategory="driver"
                    filterSource={`openavc_driver_${selectedId}`}
                    emptyText="Driver output will appear here. Click Save & Reload Driver or press Ctrl+Shift+R."
                    focusId={selectedId}
                    focusType="python_driver"
                  />
                ) : (
                  <ScriptConsole focusId={selectedId} focusType="script" />
                )}
              </Panel>
            </PanelGroup>
          ) : (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                height: "100%",
                color: "var(--text-muted)",
                gap: "var(--space-sm)",
                padding: "var(--space-xl)",
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: "var(--font-size-md)" }}>
                {scripts.length === 0 && pythonDrivers.length === 0
                  ? "Create your first script or driver"
                  : "Select a script or driver to edit"}
              </div>
              <div style={{ fontSize: "var(--font-size-sm)", maxWidth: 420, lineHeight: 1.5 }}>
                <strong>Scripts</strong> let you write Python logic that responds
                to events, state changes, and timers using the <strong>openavc</strong> module.
                <br /><br />
                <strong>Python Drivers</strong> let you build custom device drivers
                for complex protocols that need code beyond what the YAML Driver Builder supports.
              </div>
            </div>
          )}
        </Panel>
      </PanelGroup>

      {pendingConfirm && (
        <ConfirmDialog
          title={pendingConfirm.title}
          message={pendingConfirm.message}
          confirmLabel={pendingConfirm.confirmLabel}
          onConfirm={pendingConfirm.onConfirm}
          onCancel={() => setPendingConfirm(null)}
        />
      )}

      {showCreateDriver && (
        <CreateDriverDialog
          onSubmit={handleCreateDriver}
          onCancel={() => setShowCreateDriver(false)}
          existingIds={pythonDrivers.map(d => d.id)}
        />
      )}
    </ViewContainer>
  );
}

const actionBtnStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-xs)",
  padding: "var(--space-xs) var(--space-md)",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  color: "var(--text-primary)",
  fontSize: "var(--font-size-sm)",
  border: "none",
  cursor: "pointer",
  whiteSpace: "nowrap",
};
