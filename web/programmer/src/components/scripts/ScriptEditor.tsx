import { useRef, useCallback, useEffect } from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import { useProjectStore } from "../../store/projectStore";
import { useConnectionStore } from "../../store/connectionStore";

export interface RuntimeError {
  line: number;
  message: string;
}

interface ScriptEditorProps {
  source: string;
  onChange: (source: string) => void;
  onCreateVariable?: (id: string) => void;
  /** Called when the Monaco editor instance is ready, for external line navigation. */
  onEditorReady?: (editor: any) => void;
  /** Runtime errors to display as markers in the editor. */
  runtimeErrors?: RuntimeError[];
}

export function ScriptEditor({ source, onChange, onCreateVariable, onEditorReady, runtimeErrors }: ScriptEditorProps) {
  const disposablesRef = useRef<{ dispose(): void }[]>([]);
  const editorRef = useRef<any>(null);
  const monacoRef = useRef<any>(null);

  // Run diagnostics whenever source or project variables change
  const variables = useProjectStore((s) => s.project?.variables) ?? [];
  const varIds = variables.map((v) => v.id);

  useEffect(() => {
    if (!editorRef.current || !monacoRef.current) return;
    runDiagnostics(editorRef.current, monacoRef.current, varIds, runtimeErrors ?? []);
  }, [source, varIds.join(","), runtimeErrors]);

  const handleEditorDidMount: OnMount = useCallback((editor, monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
    onEditorReady?.(editor);

    // Clean up any previous registrations
    disposablesRef.current.forEach((d) => d.dispose());
    disposablesRef.current = [];

    // Register completions for the openavc API
    const disposable = monaco.languages.registerCompletionItemProvider("python", {
      triggerCharacters: [".", '"', "'", "@"],
      provideCompletionItems: (model: any, position: any) => {
        const textUntilPosition = model.getValueInRange({
          startLineNumber: position.lineNumber,
          startColumn: 1,
          endLineNumber: position.lineNumber,
          endColumn: position.column,
        });

        const suggestions: any[] = [];
        const range = {
          startLineNumber: position.lineNumber,
          startColumn: position.column,
          endLineNumber: position.lineNumber,
          endColumn: position.column,
        };

        // Import completions
        if (textUntilPosition.match(/from openavc import\s/)) {
          const imports = [
            "on_event", "on_state_change", "devices", "state",
            "events", "macros", "log", "after", "every", "cancel_timer",
          ];
          for (const item of imports) {
            suggestions.push({
              label: item,
              kind: monaco.languages.CompletionItemKind.Module,
              insertText: item,
              range,
            });
          }
        }

        // devices.* completions
        if (textUntilPosition.match(/devices\.\s*$/)) {
          suggestions.push(
            {
              label: "send",
              kind: monaco.languages.CompletionItemKind.Method,
              insertText: 'send("${1:device_id}", "${2:command}")',
              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              detail: "Send a command to a device",
              range,
            },
            {
              label: "list",
              kind: monaco.languages.CompletionItemKind.Method,
              insertText: "list()",
              detail: "List all device IDs",
              range,
            }
          );
        }

        // state.* completions
        if (textUntilPosition.match(/state\.\s*$/)) {
          suggestions.push(
            {
              label: "get",
              kind: monaco.languages.CompletionItemKind.Method,
              insertText: 'get("${1:key}")',
              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              detail: "Get a shared state value",
              range,
            },
            {
              label: "set",
              kind: monaco.languages.CompletionItemKind.Method,
              insertText: 'set("${1:key}", ${2:value})',
              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              detail: "Set a shared state value (visible to all UIs, scripts, and macros)",
              range,
            },
            {
              label: "get_namespace",
              kind: monaco.languages.CompletionItemKind.Method,
              insertText: 'get_namespace("${1:prefix}")',
              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              detail: "Get all keys under a prefix",
              range,
            }
          );
        }

        // macros.* completions
        if (textUntilPosition.match(/macros\.\s*$/)) {
          suggestions.push({
            label: "execute",
            kind: monaco.languages.CompletionItemKind.Method,
            insertText: 'execute("${1:macro_id}")',
            insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
            detail: "Execute a macro by ID",
            range,
          });
        }

        // events.* completions
        if (textUntilPosition.match(/events\.\s*$/)) {
          suggestions.push({
            label: "emit",
            kind: monaco.languages.CompletionItemKind.Method,
            insertText: 'emit("${1:event_name}")',
            insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
            detail: "Emit a custom event",
            range,
          });
        }

        // log.* completions
        if (textUntilPosition.match(/log\.\s*$/)) {
          for (const level of ["info", "warning", "error", "debug"]) {
            suggestions.push({
              label: level,
              kind: monaco.languages.CompletionItemKind.Method,
              insertText: `${level}(f"$\{1:message}")`,
              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              detail: `Log a ${level} message`,
              range,
            });
          }
        }

        // Dynamic device ID completions
        if (textUntilPosition.match(/devices\.send\(\s*["']\s*$/)) {
          const project = useProjectStore.getState().project;
          if (project) {
            for (const d of project.devices) {
              suggestions.push({
                label: d.id,
                kind: monaco.languages.CompletionItemKind.Value,
                insertText: d.id,
                detail: d.name,
                range,
              });
            }
          }
        }

        // Dynamic macro ID completions
        if (textUntilPosition.match(/macros\.execute\(\s*["']\s*$/)) {
          const project = useProjectStore.getState().project;
          if (project) {
            for (const m of project.macros) {
              suggestions.push({
                label: m.id,
                kind: monaco.languages.CompletionItemKind.Value,
                insertText: m.id,
                detail: m.name,
                range,
              });
            }
          }
        }

        // Dynamic state key completions — project variables, device state, ui overrides, system, plugin
        if (textUntilPosition.match(/state\.(get|set)\(\s*["']\s*$/) ||
            textUntilPosition.match(/@on_state_change\(\s*["']\s*$/)) {
          const project = useProjectStore.getState().project;
          if (project) {
            for (const v of project.variables) {
              suggestions.push({
                label: `var.${v.id}`,
                kind: monaco.languages.CompletionItemKind.Variable,
                insertText: `var.${v.id}`,
                detail: `${v.label} (${v.type})`,
                range,
              });
            }
            // UI override keys
            const overrideProps = ["label", "visible", "bg_color", "text_color", "opacity"];
            for (const page of project.ui?.pages ?? []) {
              for (const el of page.elements ?? []) {
                for (const prop of overrideProps) {
                  suggestions.push({
                    label: `ui.${el.id}.${prop}`,
                    kind: monaco.languages.CompletionItemKind.Variable,
                    insertText: `ui.${el.id}.${prop}`,
                    detail: `${el.label || el.type} — ${prop}`,
                    range,
                  });
                }
              }
            }
          }
          // Device state, system, and plugin keys from live state
          const liveState = useConnectionStore.getState().liveState;
          for (const key of Object.keys(liveState)) {
            if (key.startsWith("device.") || key.startsWith("system.") || key.startsWith("plugin.")) {
              suggestions.push({
                label: key,
                kind: monaco.languages.CompletionItemKind.Variable,
                insertText: key,
                range,
              });
            }
          }
        }

        // Dynamic event pattern completions
        if (textUntilPosition.match(/@on_event\(\s*["']\s*$/)) {
          const project = useProjectStore.getState().project;
          if (project) {
            // UI element events
            for (const page of project.ui?.pages ?? []) {
              for (const el of page.elements ?? []) {
                if (["button", "page_nav", "camera_preset"].includes(el.type)) {
                  suggestions.push({
                    label: `ui.press.${el.id}`,
                    kind: monaco.languages.CompletionItemKind.Event,
                    insertText: `ui.press.${el.id}`,
                    detail: `Button press — ${el.label || el.type}`,
                    range,
                  });
                }
                if (["slider", "select", "text_input"].includes(el.type)) {
                  suggestions.push({
                    label: `ui.change.${el.id}`,
                    kind: monaco.languages.CompletionItemKind.Event,
                    insertText: `ui.change.${el.id}`,
                    detail: `Value change — ${el.label || el.type}`,
                    range,
                  });
                }
              }
            }
            // Device connection events
            for (const d of project.devices) {
              suggestions.push(
                {
                  label: `device.${d.id}.connected`,
                  kind: monaco.languages.CompletionItemKind.Event,
                  insertText: `device.${d.id}.connected`,
                  detail: `${d.name} connected`,
                  range,
                },
                {
                  label: `device.${d.id}.disconnected`,
                  kind: monaco.languages.CompletionItemKind.Event,
                  insertText: `device.${d.id}.disconnected`,
                  detail: `${d.name} disconnected`,
                  range,
                },
              );
            }
          }
          // Common event patterns
          for (const pattern of ["custom.*", "system.startup", "system.shutdown", "schedule.*"]) {
            suggestions.push({
              label: pattern,
              kind: monaco.languages.CompletionItemKind.Event,
              insertText: pattern,
              range,
            });
          }
        }

        // Decorator completions
        if (textUntilPosition.match(/@\s*$/)) {
          suggestions.push(
            {
              label: "on_event",
              kind: monaco.languages.CompletionItemKind.Snippet,
              insertText: 'on_event("${1:event_pattern}")',
              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              detail: "Event handler decorator",
              range,
            },
            {
              label: "on_state_change",
              kind: monaco.languages.CompletionItemKind.Snippet,
              insertText: 'on_state_change("${1:key_pattern}")',
              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              detail: "State change handler decorator",
              range,
            }
          );
        }

        return { suggestions };
      },
    });

    disposablesRef.current.push(disposable);

    // Run initial diagnostics
    const currentVarIds = useProjectStore.getState().project?.variables.map((v) => v.id) ?? [];
    runDiagnostics(editor, monaco, currentVarIds, runtimeErrors ?? []);
  }, []);

  return (
    <Editor
      height="100%"
      language="python"
      theme="vs-dark"
      value={source}
      onChange={(value) => onChange(value ?? "")}
      onMount={handleEditorDidMount}
      options={{
        minimap: { enabled: false },
        fontSize: 13,
        scrollBeyondLastLine: false,
        wordWrap: "on",
        automaticLayout: true,
        tabSize: 4,
        insertSpaces: true,
        renderWhitespace: "selection",
        lineNumbers: "on",
        folding: true,
        bracketPairColorization: { enabled: true },
      }}
    />
  );
}

/**
 * Scan the editor content for state.get("var.X") and state.set("var.X", ...)
 * calls and flag any var.X where X is not a defined project variable.
 */
function runDiagnostics(editor: any, monaco: any, knownVarIds: string[], runtimeErrors: RuntimeError[] = []) {
  const model = editor.getModel();
  if (!model) return;

  const content = model.getValue();
  const markers: any[] = [];

  // Match state.get("var.xxx") and state.set("var.xxx", ...) patterns
  const pattern = /state\.(get|set)\(\s*["']var\.([a-zA-Z0-9_]+)["']/g;
  let match;

  while ((match = pattern.exec(content)) !== null) {
    const varId = match[2];
    if (!knownVarIds.includes(varId)) {
      // Find the position of the var.xxx string
      const startOffset = match.index + match[0].indexOf(`var.${varId}`);
      const endOffset = startOffset + `var.${varId}`.length;
      const startPos = model.getPositionAt(startOffset);
      const endPos = model.getPositionAt(endOffset);

      markers.push({
        severity: monaco.MarkerSeverity.Warning,
        message: `Variable "${varId}" is not defined in this project. Create it in the Project view or in a macro's Set Variable step.`,
        startLineNumber: startPos.lineNumber,
        startColumn: startPos.column,
        endLineNumber: endPos.lineNumber,
        endColumn: endPos.column,
      });
    }
  }

  // Also check @on_state_change("var.xxx") patterns
  const decoratorPattern = /@on_state_change\(\s*["']var\.([a-zA-Z0-9_]+)["']/g;
  while ((match = decoratorPattern.exec(content)) !== null) {
    const varId = match[1];
    if (!knownVarIds.includes(varId)) {
      const startOffset = match.index + match[0].indexOf(`var.${varId}`);
      const endOffset = startOffset + `var.${varId}`.length;
      const startPos = model.getPositionAt(startOffset);
      const endPos = model.getPositionAt(endOffset);

      markers.push({
        severity: monaco.MarkerSeverity.Warning,
        message: `Variable "${varId}" is not defined in this project. Create it in the Project view or in a macro's Set Variable step.`,
        startLineNumber: startPos.lineNumber,
        startColumn: startPos.column,
        endLineNumber: endPos.lineNumber,
        endColumn: endPos.column,
      });
    }
  }

  // Add runtime error markers
  for (const err of runtimeErrors) {
    if (err.line >= 1 && err.line <= model.getLineCount()) {
      markers.push({
        severity: monaco.MarkerSeverity.Error,
        message: err.message,
        startLineNumber: err.line,
        startColumn: 1,
        endLineNumber: err.line,
        endColumn: model.getLineLength(err.line) + 1,
      });
    }
  }

  monaco.editor.setModelMarkers(model, "openavc", markers);
}
