import { useState, useCallback, useRef, useEffect } from "react";
import { ViewContainer } from "../components/layout/ViewContainer";
import { MacroList } from "../components/macros/MacroList";
import { MacroEditor } from "../components/macros/MacroEditor";
import { ConfirmDialog } from "../components/shared/ConfirmDialog";
import { macroToScript, generateId } from "../components/macros/macroHelpers";
import { useProjectStore } from "../store/projectStore";
import { useNavigationStore } from "../store/navigationStore";
import * as api from "../api/restClient";
import type { MacroConfig } from "../api/types";
import { showError, showInfo } from "../store/toastStore";

export function MacroView() {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const updateWithUndo = useProjectStore((s) => s.updateWithUndo);
  const save = useProjectStore((s) => s.save);

  // Consume pending focus from navigation store (on mount)
  const [selectedId, setSelectedId] = useState<string | null>(() => {
    const focus = useNavigationStore.getState().consumeFocus();
    return focus?.type === "macro" ? focus.id : null;
  });

  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [scriptPreview, setScriptPreview] = useState<{ source: string; scriptId: string; fileName: string } | null>(null);

  const macros = project?.macros ?? [];
  const devices = project?.devices ?? [];
  const selectedMacro = macros.find((m) => m.id === selectedId) ?? null;

  const handleAdd = useCallback(() => {
    if (!project) return;
    const id = generateId("macro");
    const newMacro: MacroConfig = {
      id,
      name: "New Macro",
      steps: [],
    };
    update({ macros: [...macros, newMacro] });
    setSelectedId(id);
    // Auto-save
    setTimeout(() => useProjectStore.getState().save(), 100);
  }, [project, macros, update]);

  const handleDelete = useCallback(
    (id: string) => {
      setConfirmDeleteId(id);
    },
    []
  );

  const doDelete = useCallback(
    (id: string) => {
      const macro = macros.find((m) => m.id === id);
      updateWithUndo({ macros: macros.filter((m) => m.id !== id) }, `Delete macro "${macro?.name || id}"`);
      if (selectedId === id) setSelectedId(null);
      setTimeout(() => useProjectStore.getState().save(), 100);
      setConfirmDeleteId(null);
    },
    [macros, selectedId, updateWithUndo]
  );

  const macroSaveTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const handleUpdate = useCallback(
    (updated: MacroConfig) => {
      const current = useProjectStore.getState().project?.macros ?? [];
      update({
        macros: current.map((m) => (m.id === updated.id ? updated : m)),
      });
      // Debounced auto-save (timer stored in ref to survive useCallback recreation)
      clearTimeout(macroSaveTimer.current);
      macroSaveTimer.current = setTimeout(() => {
        useProjectStore.getState().save();
      }, 1500);
    },
    [update]
  );

  // Flush pending save on unmount to prevent data loss
  useEffect(() => {
    return () => {
      if (macroSaveTimer.current) {
        clearTimeout(macroSaveTimer.current);
        useProjectStore.getState().save();
      }
    };
  }, []);

  // Show preview before converting (9.6)
  const handleConvertToScript = useCallback(() => {
    if (!selectedMacro || !project) return;
    const source = macroToScript(selectedMacro, devices);
    const scriptId = selectedMacro.id.replace(/^macro_/, "script_");
    const fileName = `${scriptId}.py`;
    setScriptPreview({ source, scriptId, fileName });
  }, [selectedMacro, project, devices]);

  const handleConfirmConvert = useCallback(async () => {
    if (!scriptPreview || !selectedMacro) return;
    try {
      await api.createScript({
        id: scriptPreview.scriptId,
        file: scriptPreview.fileName,
        description: `Generated from macro "${selectedMacro.name}"`,
        source: scriptPreview.source,
      });
      await useProjectStore.getState().load();
      setScriptPreview(null);
      showInfo(
        `Script "${scriptPreview.scriptId}" created! ` +
        `Important: The original macro and its triggers are still active. ` +
        `To avoid duplicate actions, delete this macro or disable its triggers ` +
        `before enabling the script.`
      );
    } catch (e) {
      showError(`Failed to create script: ${e}`);
    }
  }, [scriptPreview, selectedMacro]);

  return (
    <ViewContainer title="Macros">
      <div style={{ display: "flex", height: "100%" }}>
        <div style={{ width: 280, flexShrink: 0 }}>
          <MacroList
            macros={macros}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onAdd={handleAdd}
            onDelete={handleDelete}
          />
        </div>
        <div style={{ flex: 1, overflow: "hidden" }}>
          {selectedMacro ? (
            <MacroEditor
              macro={selectedMacro}
              allMacros={macros}
              devices={devices.map((d) => ({ id: d.id, name: d.name }))}
              onUpdate={handleUpdate}
              onConvertToScript={handleConvertToScript}
            />
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
                {macros.length === 0
                  ? "Create your first macro"
                  : "Select a macro to edit"}
              </div>
              <div style={{ fontSize: "var(--font-size-sm)", maxWidth: 400, lineHeight: 1.5 }}>
                Macros are reusable sequences of actions — power on devices,
                switch inputs, set variables, and more. They can be triggered
                from the UI, from scripts, or from other macros.
              </div>
            </div>
          )}
        </div>
      </div>
      {confirmDeleteId && (
        <ConfirmDialog
          title="Delete Macro"
          message={`Delete macro "${macros.find((m) => m.id === confirmDeleteId)?.name}"?`}
          confirmLabel="Delete"
          onConfirm={() => doDelete(confirmDeleteId)}
          onCancel={() => setConfirmDeleteId(null)}
        />
      )}

      {/* Script conversion preview dialog (9.6) */}
      {scriptPreview && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.6)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 100,
          }}
          onClick={() => setScriptPreview(null)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: "var(--bg-surface)",
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              width: "min(700px, 90vw)",
              maxHeight: "80vh",
              display: "flex",
              flexDirection: "column",
              boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
            }}
          >
            {/* Header */}
            <div style={{
              padding: "var(--space-md)",
              borderBottom: "1px solid var(--border-color)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}>
              <div>
                <div style={{ fontWeight: 600, color: "var(--text-primary)", fontSize: "var(--font-size-md)" }}>
                  Convert to Script
                </div>
                <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                  This will create <code style={{ fontFamily: "var(--font-mono)" }}>{scriptPreview.fileName}</code> &mdash; review the generated code below.
                </div>
              </div>
            </div>
            {/* Code preview */}
            <div style={{ flex: 1, overflow: "auto", padding: 0 }}>
              <pre style={{
                margin: 0,
                padding: "var(--space-md)",
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                lineHeight: 1.5,
                color: "var(--text-primary)",
                background: "var(--bg-primary)",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}>
                {scriptPreview.source}
              </pre>
            </div>
            {/* Actions */}
            <div style={{
              padding: "var(--space-md)",
              borderTop: "1px solid var(--border-color)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}>
              <div style={{ fontSize: 11, color: "var(--text-muted)", maxWidth: 400, lineHeight: 1.4 }}>
                The original macro and its triggers will remain active. Disable or delete the macro after verifying the script works.
              </div>
              <div style={{ display: "flex", gap: "var(--space-sm)" }}>
                <button
                  onClick={() => setScriptPreview(null)}
                  style={{
                    padding: "var(--space-xs) var(--space-md)",
                    borderRadius: "var(--border-radius)",
                    background: "var(--bg-hover)",
                    color: "var(--text-primary)",
                    fontSize: "var(--font-size-sm)",
                    border: "none",
                    cursor: "pointer",
                  }}
                >
                  Cancel
                </button>
                <button
                  onClick={handleConfirmConvert}
                  style={{
                    padding: "var(--space-xs) var(--space-md)",
                    borderRadius: "var(--border-radius)",
                    background: "var(--accent)",
                    color: "#fff",
                    fontSize: "var(--font-size-sm)",
                    border: "none",
                    cursor: "pointer",
                  }}
                >
                  Create Script
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </ViewContainer>
  );
}
