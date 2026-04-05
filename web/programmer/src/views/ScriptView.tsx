import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { Save, Play, FileCode, ChevronDown } from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { ScriptFileTree } from "../components/scripts/ScriptFileTree";
import { ScriptEditor, type RuntimeError } from "../components/scripts/ScriptEditor";
import { ScriptConsole } from "../components/scripts/ScriptConsole";
import { ConfirmDialog } from "../components/shared/ConfirmDialog";
import { SCRIPT_TEMPLATES } from "../components/scripts/scriptTemplates";
import { useProjectStore } from "../store/projectStore";
import { useNavigationStore } from "../store/navigationStore";
import { useLogStore } from "../store/logStore";
import * as api from "../api/restClient";
import { showError } from "../store/toastStore";

export function ScriptView() {
  const project = useProjectStore((s) => s.project);
  const load = useProjectStore((s) => s.load);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [source, setSource] = useState("");
  const [originalSource, setOriginalSource] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [showTemplates, setShowTemplates] = useState(false);
  const [pendingConfirm, setPendingConfirm] = useState<{ title: string; message: string; confirmLabel: string; onConfirm: () => void } | null>(null);
  const [scriptLoadErrors, setScriptLoadErrors] = useState<Record<string, string>>({});

  const editorInstanceRef = useRef<any>(null);
  const pendingLineRef = useRef<number | null>(null);

  // Fetch script load errors on mount
  useEffect(() => {
    api.getScriptErrors().then(setScriptLoadErrors).catch(() => {});
  }, []);

  // Consume pending focus from navigation store
  useEffect(() => {
    const focus = useNavigationStore.getState().consumeFocus();
    if (focus?.type === "script") {
      // Parse line number from detail (e.g., "line:12")
      if (focus.detail?.startsWith("line:")) {
        pendingLineRef.current = parseInt(focus.detail.slice(5), 10);
      }
      handleSelect(focus.id);
    }
  }, []);

  const scripts = project?.scripts ?? [];
  const isDirty = source !== originalSource;

  // Warn before closing tab with unsaved script changes
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (isDirty) { e.preventDefault(); }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);


  // Extract runtime errors from log entries for the selected script
  const runtimeErrors = useMemo((): RuntimeError[] => {
    if (!selectedId) return [];
    const entries = useLogStore.getState().logEntries;
    const errors: RuntimeError[] = [];
    const scriptFile = scripts.find((s) => s.id === selectedId)?.file ?? selectedId;
    for (const entry of entries) {
      if (entry.level !== "ERROR" || entry.category !== "script") continue;
      if (!entry.message.includes(selectedId) && !entry.message.includes(scriptFile)) continue;
      // Parse "line N" from traceback-style messages
      const lineMatch = entry.message.match(/line (\d+)/);
      if (lineMatch) {
        errors.push({ line: parseInt(lineMatch[1], 10), message: entry.message.split("\n")[0] });
      }
    }
    return errors;
  }, [selectedId, scripts]);

  const doSelectScript = useCallback(async (id: string) => {
    setSelectedId(id);
    setLoading(true);
    try {
      const result = await api.getScriptSource(id);
      setSource(result.source);
      setOriginalSource(result.source);
    } catch (e) {
      console.error("Failed to load script:", e);
      setSource(`# Error loading script: ${e}`);
      setOriginalSource("");
    } finally {
      setLoading(false);
    }
  }, []);

  const handleSelect = useCallback((id: string) => {
    if (source !== originalSource && selectedId) {
      setPendingConfirm({
        title: "Unsaved Changes",
        message: "You have unsaved changes. Switch scripts and discard them?",
        confirmLabel: "Discard & Switch",
        onConfirm: () => { setPendingConfirm(null); doSelectScript(id); },
      });
      return;
    }
    doSelectScript(id);
  }, [source, originalSource, selectedId, doSelectScript]);

  const handleSave = useCallback(async () => {
    if (!selectedId) return;
    setSaving(true);
    try {
      await api.saveScriptSource(selectedId, source);
      setOriginalSource(source);
    } catch (e) {
      console.error("Failed to save script:", e);
      showError(`Save failed: ${e}`);
    } finally {
      setSaving(false);
    }
  }, [selectedId, source]);

  const handleRun = useCallback(async () => {
    // Save first if dirty
    if (isDirty && selectedId) {
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

    // Reload scripts
    try {
      const result = await api.reloadScripts();
      setScriptLoadErrors(result.errors ?? {});
      // Add a synthetic log entry to show the result
      const errorCount = Object.keys(result.errors ?? {}).length;
      useLogStore.getState().addLogEntry({
        timestamp: Date.now() / 1000,
        level: errorCount > 0 ? "WARNING" : "INFO",
        source: "openavc.programmer",
        category: "script",
        message: errorCount > 0
          ? `Scripts reloaded: ${result.handlers} handler(s), ${errorCount} script(s) failed to load`
          : `Scripts reloaded: ${result.handlers} handler(s) registered`,
      });
    } catch (e) {
      useLogStore.getState().addLogEntry({
        timestamp: Date.now() / 1000,
        level: "ERROR",
        source: "openavc.programmer",
        category: "script",
        message: `Script reload failed: ${e}`,
      });
    }
  }, [selectedId, source, isDirty]);

  // Keyboard shortcut: Ctrl+Shift+R to save & reload scripts (9.4)
  const handleRunRef = useRef(handleRun);
  handleRunRef.current = handleRun;
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && e.key === "R") {
        e.preventDefault();
        handleRunRef.current();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const handleCreate = useCallback(
    async (id: string, file: string, description: string) => {
      try {
        await api.createScript({
          id,
          file,
          description,
          source: `"""${description || id}"""\nfrom openavc import on_event, state, log\n\n`,
        });
        await load();
        setSelectedId(id);
        // Load the new script
        const result = await api.getScriptSource(id);
        setSource(result.source);
        setOriginalSource(result.source);
      } catch (e) {
        showError(`Create failed: ${e}`);
      }
    },
    [load]
  );

  const handleDelete = useCallback(
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
            if (selectedId === id) {
              setSelectedId(null);
              setSource("");
              setOriginalSource("");
            }
          } catch (e) {
            showError(`Delete failed: ${e}`);
          }
        },
      });
    },
    [selectedId, scripts, load]
  );

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

  return (
    <ViewContainer
      title="Scripts"
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
                  {SCRIPT_TEMPLATES.map((t) => (
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
            <button
              onClick={handleRun}
              title="Save the current script and reload all script handlers (Ctrl+Shift+R)"
              style={{
                ...actionBtnStyle,
                background: "var(--accent)",
                color: "#fff",
              }}
            >
              <Play size={14} />
              Save &amp; Reload
            </button>
          </div>
        ) : undefined
      }
    >
      <PanelGroup direction="horizontal" style={{ height: "100%" }}>
        {/* File tree */}
        <Panel defaultSize={20} minSize={15} maxSize={35}>
          <ScriptFileTree
            scripts={scripts}
            selectedId={selectedId}
            loadErrors={scriptLoadErrors}
            onSelect={handleSelect}
            onCreate={handleCreate}
            onDelete={handleDelete}
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
                      onEditorReady={(editor) => {
                        editorInstanceRef.current = editor;
                        // Scroll to pending line if navigation requested it
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
                <ScriptConsole />
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
                {scripts.length === 0
                  ? "Create your first script"
                  : "Select a script to edit"}
              </div>
              <div style={{ fontSize: "var(--font-size-sm)", maxWidth: 420, lineHeight: 1.5 }}>
                Scripts let you write Python logic that responds to events,
                state changes, and timers. Use the <strong>openavc</strong> module
                to control devices, read/write state, and emit events.
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
