import { useRef, useCallback, useEffect } from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import { useProjectStore } from "../../store/projectStore";
import { useConnectionStore } from "../../store/connectionStore";
import {
  ensurePluginScriptApiLoaded,
  getCachedPluginScriptApi,
} from "./pluginScriptApiCache";
import { scriptCompletions, type CompletionKind, type ScriptCompletion } from "./scriptCompletions";
import { scriptMarkers } from "./scriptDiagnostics";

export interface RuntimeError {
  line: number;
  message: string;
}

interface ScriptEditorProps {
  source: string;
  onChange: (source: string) => void;
  /** Called when the Monaco editor instance is ready, for external line navigation. */
  onEditorReady?: (editor: any) => void;
  /** Runtime errors to display as markers in the editor. */
  runtimeErrors?: RuntimeError[];
  /** Handlers the platform says nothing can ever reach, marked as warnings. */
  lintWarnings?: RuntimeError[];
  /** Editor mode — controls IntelliSense and diagnostics. */
  editorMode?: "script" | "driver";
}

export function ScriptEditor({ source, onChange, onEditorReady, runtimeErrors, lintWarnings, editorMode = "script" }: ScriptEditorProps) {
  const disposablesRef = useRef<{ dispose(): void }[]>([]);
  const editorRef = useRef<any>(null);
  const monacoRef = useRef<any>(null);
  const editorModeRef = useRef(editorMode);
  useEffect(() => { editorModeRef.current = editorMode; }, [editorMode]);

  // Run diagnostics whenever source or project variables change
  const variables = useProjectStore((s) => s.project?.variables) ?? [];
  const varIds = variables.map((v) => v.id);

  useEffect(() => {
    if (!editorRef.current || !monacoRef.current) return;
    runDiagnostics(editorRef.current, monacoRef.current, varIds, runtimeErrors ?? [], lintWarnings ?? []);
  }, [source, varIds.join(","), runtimeErrors, lintWarnings]);

  const handleEditorDidMount: OnMount = useCallback((editor, monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
    onEditorReady?.(editor);

    // Kick off plugin script API fetch so completions for plugins.<id>.<method>
    // can resolve. Runs once per session; the cache is module-level.
    ensurePluginScriptApiLoaded();

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
        const range = {
          startLineNumber: position.lineNumber,
          startColumn: position.column,
          endLineNumber: position.lineNumber,
          endColumn: position.column,
        };
        const completions = scriptCompletions(textUntilPosition, {
          project: useProjectStore.getState().project,
          liveState: useConnectionStore.getState().liveState,
          pluginMethods: getCachedPluginScriptApi(),
        });
        return { suggestions: completions.map((c) => toMonacoItem(monaco, c, range)) };
      },
    });

    disposablesRef.current.push(disposable);

    // Register driver-mode completions (self.*, self.transport.*, BaseDriver methods)
    const driverDisposable = monaco.languages.registerCompletionItemProvider("python", {
      triggerCharacters: [".", '"', "'"],
      provideCompletionItems: (model: any, position: any) => {
        // Only provide driver completions when in driver mode
        const fullSource = model.getValue();
        const isDriver = editorModeRef.current === "driver" || fullSource.includes("BaseDriver") || fullSource.includes("DRIVER_INFO");
        if (!isDriver) return { suggestions: [] };

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

        // self.* completions
        if (textUntilPosition.match(/self\.\s*$/)) {
          suggestions.push(
            {
              label: "set_state",
              kind: monaco.languages.CompletionItemKind.Method,
              insertText: 'set_state("${1:property}", ${2:value})',
              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              detail: "(property: str, value) -> None",
              documentation: { value: 'Set a device state variable. Auto-namespaced to device.{device_id}.{property}.\n\n```python\nself.set_state("power", True)\nself.set_state("input", "hdmi1")\n```' },
              range,
            },
            {
              label: "get_state",
              kind: monaco.languages.CompletionItemKind.Method,
              insertText: 'get_state("${1:property}")',
              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              detail: "(property: str) -> Any",
              documentation: { value: 'Read a device state variable.\n\n```python\npower = self.get_state("power")\n```' },
              range,
            },
            {
              label: "set_states",
              kind: monaco.languages.CompletionItemKind.Method,
              insertText: 'set_states(${1:updates})',
              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              detail: "(updates: dict) -> None",
              documentation: { value: 'Set multiple state variables atomically.\n\n```python\nself.set_states({"power": True, "input": "hdmi1"})\n```' },
              range,
            },
            {
              label: "transport",
              kind: monaco.languages.CompletionItemKind.Property,
              insertText: "transport",
              detail: "Transport instance (TCP, serial, HTTP)",
              range,
            },
            {
              label: "config",
              kind: monaco.languages.CompletionItemKind.Property,
              insertText: "config",
              detail: "dict: device configuration from project",
              range,
            },
            {
              label: "device_id",
              kind: monaco.languages.CompletionItemKind.Property,
              insertText: "device_id",
              detail: "str: unique device identifier",
              range,
            },
            {
              label: "events",
              kind: monaco.languages.CompletionItemKind.Property,
              insertText: "events",
              detail: "EventBus: emit and listen for events",
              range,
            },
            {
              label: "connected",
              kind: monaco.languages.CompletionItemKind.Property,
              insertText: "connected",
              detail: "bool: True if transport is connected",
              range,
            },
          );
        }

        // self.transport.* completions
        if (textUntilPosition.match(/self\.transport\.\s*$/)) {
          suggestions.push(
            {
              label: "send",
              kind: monaco.languages.CompletionItemKind.Method,
              insertText: "send(${1:data})",
              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              detail: "(data: bytes) -> None",
              documentation: { value: 'Send raw bytes to the device.\n\n```python\nawait self.transport.send(b"POWER ON\\r\\n")\n```' },
              range,
            },
            {
              label: "send_and_wait",
              kind: monaco.languages.CompletionItemKind.Method,
              insertText: "send_and_wait(${1:data}, timeout=${2:5.0})",
              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              detail: "(data: bytes, timeout: float) -> bytes",
              documentation: { value: 'Send data and wait for a response.\n\n```python\nresponse = await self.transport.send_and_wait(b"STATUS?\\r\\n", timeout=3.0)\n```' },
              range,
            },
            {
              label: "close",
              kind: monaco.languages.CompletionItemKind.Method,
              insertText: "close()",
              detail: "() -> None",
              documentation: { value: "Close the transport connection." },
              range,
            },
            {
              label: "connected",
              kind: monaco.languages.CompletionItemKind.Property,
              insertText: "connected",
              detail: "bool: transport connection state",
              range,
            },
          );
        }

        // self.events.* completions
        if (textUntilPosition.match(/self\.events\.\s*$/)) {
          suggestions.push(
            {
              label: "emit",
              kind: monaco.languages.CompletionItemKind.Method,
              insertText: 'emit("${1:event_name}")',
              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              detail: "(event: str, payload?: dict) -> None",
              range,
            },
          );
        }

        // Import completions for drivers
        if (textUntilPosition.match(/from openavc\.drivers\.base import\s/)) {
          suggestions.push({
            label: "BaseDriver",
            kind: monaco.languages.CompletionItemKind.Class,
            insertText: "BaseDriver",
            range,
          });
        }

        if (textUntilPosition.match(/from openavc\.utils\.logger import\s/)) {
          suggestions.push({
            label: "get_logger",
            kind: monaco.languages.CompletionItemKind.Function,
            insertText: "get_logger",
            range,
          });
        }

        return { suggestions };
      },
    });

    disposablesRef.current.push(driverDisposable);

    // Run initial diagnostics
    const currentVarIds = useProjectStore.getState().project?.variables.map((v) => v.id) ?? [];
    runDiagnostics(editor, monaco, currentVarIds, runtimeErrors ?? [], lintWarnings ?? []);
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

/** Monaco's kind for each kind of script completion. */
function monacoKind(monaco: any, kind: CompletionKind): number {
  const kinds = monaco.languages.CompletionItemKind;
  const table: Record<CompletionKind, number> = {
    module: kinds.Module,
    function: kinds.Function,
    method: kinds.Method,
    class: kinds.Class,
    value: kinds.Value,
    variable: kinds.Variable,
    event: kinds.Event,
    snippet: kinds.Snippet,
  };
  return table[kind];
}

/** A script completion as the Monaco item that inserts it at `range`. */
function toMonacoItem(monaco: any, c: ScriptCompletion, range: unknown) {
  const rules = monaco.languages.CompletionItemInsertTextRule;
  return {
    label: c.label,
    kind: monacoKind(monaco, c.kind),
    insertText: c.insertText,
    insertTextRules: c.snippet ? rules.InsertAsSnippet : rules.None,
    detail: c.detail,
    documentation: markdown(c.documentation),
    range,
  };
}

function markdown(text: string | undefined) {
  return text === undefined ? undefined : { value: text };
}

/** Mark undefined variables, the script's runtime errors and its lint warnings. */
function runDiagnostics(
  editor: any,
  monaco: any,
  knownVarIds: string[],
  runtimeErrors: RuntimeError[] = [],
  lintWarnings: RuntimeError[] = [],
) {
  const model = editor.getModel();
  if (!model) return;
  const severity = { warning: monaco.MarkerSeverity.Warning, error: monaco.MarkerSeverity.Error };
  const markers = scriptMarkers(model, severity, knownVarIds, runtimeErrors, lintWarnings);
  monaco.editor.setModelMarkers(model, "openavc", markers);
}
