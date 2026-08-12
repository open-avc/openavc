/**
 * The editor for one file in the project's `ui/` folder.
 *
 * Its own component rather than a mode on `ScriptEditor`, because that one is
 * Python all the way down — its completions, its `var.` diagnostics and its
 * language are all the scripting API, and none of it means anything in an
 * HTML file. What the two share is Monaco itself and its options, which is
 * the part that was worth not writing twice.
 *
 * A control carries files nobody types into (an image, a font). They still
 * belong in the tree — they are part of the control and they travel with the
 * project — so the tree lists them and this says plainly why there is no
 * editor rather than opening one on bytes.
 */
import Editor from "@monaco-editor/react";
import { languageForUiPath } from "./customUiFiles";

export interface CustomUiEditorProps {
  /** Path inside `ui/`, e.g. `room_map/index.html`. */
  path: string;
  source: string;
  onChange: (source: string) => void;
}

export function CustomUiEditor({ path, source, onChange }: CustomUiEditorProps) {
  const language = languageForUiPath(path);

  if (!language) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          gap: "var(--space-sm)",
          padding: "var(--space-xl)",
          textAlign: "center",
          color: "var(--text-muted)",
        }}
      >
        <div style={{ fontSize: "var(--font-size-md)", color: "var(--text-primary)" }}>
          {path}
        </div>
        <div style={{ fontSize: "var(--font-size-sm)", maxWidth: 420, lineHeight: 1.5 }}>
          This file travels with the project and your control can load it, but there
          is nothing here to edit. Replace it by dropping a new one in.
        </div>
      </div>
    );
  }

  return (
    <Editor
      height="100%"
      language={language}
      theme="vs-dark"
      value={source}
      onChange={(value) => onChange(value ?? "")}
      options={{
        minimap: { enabled: false },
        fontSize: 13,
        scrollBeyondLastLine: false,
        wordWrap: "on",
        automaticLayout: true,
        tabSize: 2,
        insertSpaces: true,
        renderWhitespace: "selection",
        lineNumbers: "on",
        folding: true,
        bracketPairColorization: { enabled: true },
      }}
    />
  );
}
