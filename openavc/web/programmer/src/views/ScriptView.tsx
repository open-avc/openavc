import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { ChevronDown } from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { headerButton, headerPrimaryButton } from "../components/layout/headerActions";
import { ScriptFileTree } from "../components/scripts/ScriptFileTree";
import { ScriptEditor, type RuntimeError } from "../components/scripts/ScriptEditor";
import { ScriptConsole } from "../components/scripts/ScriptConsole";
import { ConfirmDialog } from "../components/shared/ConfirmDialog";
import { CreateDriverDialog } from "../components/scripts/CreateDriverDialog";
import { SCRIPT_TEMPLATES } from "../components/scripts/scriptTemplates";
import { DRIVER_TEMPLATES } from "../components/scripts/driverTemplates";
import { CustomUiEditor } from "../components/scripts/CustomUiEditor";
import { isEditableUiPath, starterUiContent } from "../components/scripts/customUiFiles";
import { useProjectStore } from "../store/projectStore";
import { useNavigationStore } from "../store/navigationStore";
import { useLogStore } from "../store/logStore";
import { useUiFilesStore } from "../store/uiFilesStore";
import { extractScriptRuntimeErrors, latestScriptErrorId } from "../components/scripts/scriptRuntimeErrors";
import * as api from "../api/restClient";
import {
  deleteCustomUiFile,
  listCustomUiFiles,
  readCustomUiFile,
  uploadCustomUiFiles,
  writeCustomUiFile,
  type CustomUiFile,
} from "../api/customUiClient";
import { filesFromList, type DroppedFile } from "../components/shared/dropFiles";
import { showError, showSuccess } from "../store/toastStore";
import type { PythonDriverInfo } from "../api/types";

type EditorTarget = "script" | "driver" | "ui";

export function ScriptView() {
  const scripts = useProjectStore((s) => s.project?.scripts) ?? [];
  const load = useProjectStore((s) => s.load);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedType, setSelectedType] = useState<EditorTarget | null>(null);
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
  const [uiFiles, setUiFiles] = useState<CustomUiFile[]>([]);
  // What the last save of a custom UI file reported. The server is the only
  // thing that can answer it — a control runs in a sandboxed frame in the
  // panel, so there is no console here and no way to tell from the markup
  // alone that a script came from the internet or a page is sized in pixels.
  const [uiWarnings, setUiWarnings] = useState<string[]>([]);

  const editorInstanceRef = useRef<any>(null);
  const pendingLineRef = useRef<number | null>(null);
  const driverFileInputRef = useRef<HTMLInputElement>(null);
  const uiFileInputRef = useRef<HTMLInputElement>(null);

  // Fetch script load errors and Python drivers on mount
  useEffect(() => {
    api.getScriptErrors().then(setScriptLoadErrors).catch(() => {});
    loadPythonDrivers();
    void loadUiFiles();
  }, []);

  // The project's ui/ folder — custom controls, edited here the way scripts
  // are. Anything that writes into that folder also bumps uiFilesStore, which
  // is what redraws the control on the UI Builder's design canvas: its markup
  // is not project data, so nothing else would.
  const loadUiFiles = useCallback(async (): Promise<CustomUiFile[]> => {
    try {
      const listing = await listCustomUiFiles();
      setUiFiles(listing.files);
      return listing.files;
    } catch {
      // An unreachable listing is not worth a toast on mount; the section
      // simply shows empty and the next write reports for itself.
      return [];
    }
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

  const isDirty = source !== originalSource;

  // Warn before closing tab with unsaved changes
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (isDirty) { e.preventDefault(); }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  // Narrow subscription to the (rapidly-updating) log store: just the id of the
  // most recent script error. A primitive, so it re-renders this view only when
  // a new script error is logged — not on every log line — and re-runs the
  // marker memo below then, which a plain getState() read never did.
  const scriptErrorId = useLogStore((s) => latestScriptErrorId(s.logEntries));

  // Extract runtime errors from log entries for the selected item.
  const runtimeErrors = useMemo((): RuntimeError[] => {
    if (!selectedId) return [];
    if (selectedType === "ui") return [];
    if (selectedType === "driver") return driverReloadErrors;
    const scriptFile = scripts.find((s) => s.id === selectedId)?.file ?? selectedId;
    return extractScriptRuntimeErrors(useLogStore.getState().logEntries, selectedId, scriptFile);
    // scriptErrorId is the reactive trigger: a new script error bumps it, re-running this memo.
  }, [selectedId, selectedType, scripts, driverReloadErrors, scriptErrorId]);

  // --- Selection handlers ---

  const doSelect = useCallback(async (id: string, type: EditorTarget) => {
    setSelectedId(id);
    setSelectedType(type);
    setDriverReloadErrors([]);
    // Last file's warnings belong to last file.
    setUiWarnings([]);
    setLoading(true);
    try {
      if (type === "ui") {
        // An image or a font in a control's folder is a real file with nothing
        // to type into; the editor says so rather than showing its bytes.
        let text = "";
        if (isEditableUiPath(id)) {
          try {
            text = await readCustomUiFile(id);
          } catch (e) {
            // Never leave an error message sitting in the buffer as if it were
            // the file: saving would write it over the author's control.
            showError(`Could not open ${id}: ${e instanceof Error ? e.message : e}`);
            setSelectedId(null);
            setSelectedType(null);
            setSource("");
            setOriginalSource("");
            return;
          }
        }
        setSource(text);
        setOriginalSource(text);
      } else {
        const result = type === "script"
          ? await api.getScriptSource(id)
          : await api.getPythonDriverSource(id);
        setSource(result.source);
        setOriginalSource(result.source);
      }
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

  const handleSelectUiFile = useCallback((path: string) => {
    if (isDirty && selectedId) {
      setPendingConfirm({
        title: "Unsaved Changes",
        message: "You have unsaved changes. Switch and discard them?",
        confirmLabel: "Discard & Switch",
        onConfirm: () => { setPendingConfirm(null); doSelect(path, "ui"); },
      });
      return;
    }
    doSelect(path, "ui");
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
      if (selectedType === "ui") {
        const saved = await writeCustomUiFile(selectedId, source);
        // The save is already a round trip, so the review runs there and this
        // renders what it said. Cleared when there is nothing, so a fixed file
        // stops showing the problem it no longer has.
        setUiWarnings(saved.warnings ?? []);
        // The file changed, not the project, so this is the only thing that
        // tells the design canvas to draw the new version.
        useUiFilesStore.getState().bump();
        await loadUiFiles();
      } else if (selectedType === "script") {
        await api.saveScriptSource(selectedId, source);
      } else {
        // Plain Save keeps work in progress in whatever state it is in — but
        // says so when what it kept will not load. The running driver goes on
        // working, so nothing else would tell you until a restart dropped it
        // and took its devices offline.
        const res = await api.savePythonDriverSource(selectedId, source);
        if (res.syntax_error) {
          showError(`Saved, but this driver will not load: ${res.syntax_error}`);
          setDriverReloadErrors(
            res.line ? [{ line: res.line, message: res.syntax_error }] : []
          );
        } else {
          setDriverReloadErrors([]);
        }
      }
      setOriginalSource(source);
    } catch (e) {
      console.error(`Failed to save ${selectedType}:`, e);
      showError(`Save failed: ${e}`);
    } finally {
      setSaving(false);
    }
  }, [selectedId, selectedType, source, loadUiFiles]);

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
        showSuccess(`Script reloaded, ${result.handlers ?? 0} handler(s)`);
        useLogStore.getState().addLogEntry({
          timestamp: Date.now() / 1000,
          level: "INFO",
          source: "openavc.programmer",
          device: "",
          category: "script",
          message: `Script '${selectedId}' reloaded, ${result.handlers ?? 0} handler(s) registered`,
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

    // Save first if dirty — but refuse to persist source that will not parse.
    // "Save & Reload" means "make this the live driver"; writing a file that
    // cannot load leaves the running process working while the copy on disk is
    // dead, and the next restart drops the driver and its devices with it.
    if (isDirty) {
      setSaving(true);
      try {
        const res = await api.savePythonDriverSource(selectedId, source, true);
        if (res.status === "error") {
          showError(
            `Not saved. ${res.error}. The file on disk is unchanged and still loads.`
          );
          if (res.line) {
            setDriverReloadErrors([
              { line: res.line, message: res.error ?? "Syntax error" },
            ]);
          }
          setSaving(false);
          return;
        }
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
          ? `Driver reloaded, ${devCount} device(s) reconnected`
          : "Driver reloaded");
        useLogStore.getState().addLogEntry({
          timestamp: Date.now() / 1000,
          level: "INFO",
          source: "openavc.programmer",
          device: "",
          category: "driver",
          message: devCount > 0
            ? `Driver '${result.driver_id}' reloaded, ${devCount} device(s) reconnected: ${result.devices_reconnected!.join(", ")}`
            : `Driver '${result.driver_id}' reloaded, no devices affected`,
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
        const extra = activated.length > 0 ? `, connected ${activated.length} waiting device(s)` : "";
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

  // --- Custom control (ui/) handlers ---

  const handleCreateUiFile = useCallback(
    async (path: string) => {
      try {
        // A new page starts from the skeleton with both message directions
        // already wired: an empty HTML file is a blank box with nothing to
        // react to, and the bridge is the part worth not retyping.
        await writeCustomUiFile(path, starterUiContent(path));
        useUiFilesStore.getState().bump();
        await loadUiFiles();
        doSelect(path, "ui");
      } catch (e) {
        // The server owns what may live in ui/, so its refusal is the message
        // worth showing — it names the rule that was broken.
        showError(e instanceof Error ? e.message : String(e));
      }
    },
    [loadUiFiles, doSelect],
  );

  const handleUploadUiFiles = useCallback(
    async (dropped: DroppedFile[]) => {
      if (dropped.length === 0) return;
      try {
        const result = await uploadCustomUiFiles(dropped);
        useUiFilesStore.getState().bump();
        await loadUiFiles();
        if (result.skipped.length > 0) {
          showError(
            `Added ${result.written.length} file(s). Skipped ${result.skipped.length} this folder can't hold.`,
          );
        } else {
          showSuccess(`Added ${result.written.length} file(s) to custom controls`);
        }
      } catch (e) {
        showError(`Upload failed: ${e instanceof Error ? e.message : String(e)}`);
      }
    },
    [loadUiFiles],
  );

  const handleImportUiFilesFromPicker = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const picked = filesFromList(e.target.files);
      // Reset the input so the same file can be re-picked later.
      if (uiFileInputRef.current) uiFileInputRef.current.value = "";
      void handleUploadUiFiles(picked);
    },
    [handleUploadUiFiles],
  );

  const handleDeleteUiFile = useCallback(
    (path: string) => {
      setPendingConfirm({
        title: "Delete File",
        message: `Delete "${path}"? Any custom control or page pointing at it will stop drawing.`,
        confirmLabel: "Delete",
        onConfirm: async () => {
          setPendingConfirm(null);
          try {
            await deleteCustomUiFile(path);
            useUiFilesStore.getState().bump();
            await loadUiFiles();
            if (selectedId === path && selectedType === "ui") {
              setSelectedId(null);
              setSelectedType(null);
              setSource("");
              setOriginalSource("");
            }
          } catch (e) {
            showError(`Delete failed: ${e instanceof Error ? e.message : String(e)}`);
          }
        },
      });
    },
    [selectedId, selectedType, loadUiFiles],
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
            {/* Templates dropdown — Python only; a control's starter markup
                comes with the file when it is created. */}
            <div style={{ position: "relative", display: selectedType === "ui" ? "none" : undefined }}>
              <button
                onClick={() => setShowTemplates(!showTemplates)}
                style={headerButton}
              >
                Templates
                <ChevronDown size={12} />
              </button>
              {showTemplates && (
                <div
                  style={{
                    position: "absolute",
                    top: "100%",
                    right: 0,
                    marginTop: "var(--space-xs)",
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
                      <div style={{ fontWeight: "var(--font-weight-medium)", color: "var(--text-primary)" }}>
                        {t.name}
                      </div>
                      <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)" }}>
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
                ...(selectedType === "ui" ? headerPrimaryButton : headerButton),
                opacity: isDirty ? 1 : 0.5,
              }}
            >
              {saving ? "Saving..." : "Save"}
            </button>

            {selectedType === "ui" ? null : selectedType === "driver" ? (
              <button
                onClick={handleReloadDriver}
                disabled={reloading}
                title="Save and hot-reload the driver (Ctrl+Shift+R)"
                style={headerPrimaryButton}
              >
                {reloading ? "Reloading..." : "Save & Reload Driver"}
              </button>
            ) : (
              <button
                onClick={handleReloadScript}
                disabled={reloading}
                title="Save and hot-reload this script. Other scripts keep running (Ctrl+Shift+R)"
                style={headerPrimaryButton}
              >
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
      {/* Hidden picker for adding custom control files (or a .zip of one) */}
      <input
        ref={uiFileInputRef}
        type="file"
        multiple
        style={{ display: "none" }}
        onChange={handleImportUiFilesFromPicker}
      />
      <PanelGroup direction="horizontal" style={{ height: "100%" }}>
        {/* File tree */}
        <Panel defaultSize={20} minSize={15} maxSize={35}>
          <ScriptFileTree
            scripts={scripts}
            drivers={pythonDrivers}
            uiFiles={uiFiles}
            selectedId={selectedId}
            selectedType={selectedType}
            loadErrors={scriptLoadErrors}
            onSelectScript={handleSelectScript}
            onSelectDriver={handleSelectDriver}
            onSelectUiFile={handleSelectUiFile}
            onCreateScript={handleCreateScript}
            onCreateDriver={() => setShowCreateDriver(true)}
            onCreateUiFile={(path) => { void handleCreateUiFile(path); }}
            onImportDriver={() => driverFileInputRef.current?.click()}
            onImportUiFiles={() => uiFileInputRef.current?.click()}
            onExportDriver={handleExportDriver}
            onDeleteScript={handleDeleteScript}
            onDeleteDriver={handleDeleteDriver}
            onDeleteUiFile={handleDeleteUiFile}
            onDropUiFiles={(files) => { void handleUploadUiFiles(files); }}
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
          {selectedId && selectedType === "ui" ? (
            // No console for a custom control: it runs in a sandboxed frame in
            // the panel, not in this process, so there is no server-side output
            // to show. What it reports through openavc:error surfaces where it
            // is running -- in the element's own box and as a toast.
            <div style={{ height: "100%", overflow: "hidden" }}>
              {loading ? (
                <div style={loadingStyle}>Loading...</div>
              ) : (
                <CustomUiEditor
                  path={selectedId}
                  source={source}
                  onChange={setSource}
                  warnings={uiWarnings}
                />
              )}
            </div>
          ) : selectedId ? (
            <PanelGroup direction="vertical">
              {/* Editor */}
              <Panel defaultSize={70} minSize={30}>
                <div style={{ height: "100%", overflow: "hidden" }}>
                  {loading ? (
                    <div style={loadingStyle}>Loading...</div>
                  ) : (
                    <ScriptEditor
                      source={source}
                      onChange={setSource}
                      runtimeErrors={runtimeErrors}
                      editorMode={selectedType === "driver" ? "driver" : "script"}
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
                fontSize: "var(--font-size-sm)",
                lineHeight: "var(--line-relaxed)",
              }}
            >
              <div style={{ fontSize: "var(--font-size-base)" }}>
                {scripts.length === 0 && pythonDrivers.length === 0 && uiFiles.length === 0
                  ? "Create your first script, driver or control"
                  : "Select a file to edit"}
              </div>
              <div style={{ fontSize: "var(--font-size-sm)", maxWidth: 420, lineHeight: "var(--line-base)" }}>
                <strong>Scripts</strong> let you write Python logic that responds
                to events, state changes, and timers using the <strong>openavc</strong> module.
                <br /><br />
                <strong>Python Drivers</strong> let you build custom device drivers
                for complex protocols that need code beyond what the YAML Driver Builder supports.
                <br /><br />
                <strong>Custom Controls</strong> are pages you write yourself (HTML, CSS
                and JavaScript) that run inside one element's box on a panel. Place one
                from the UI Builder's palette.
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

const loadingStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  height: "100%",
  color: "var(--text-muted)",
};

