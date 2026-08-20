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
  /**
   * What the last save reported about this file, one sentence each.
   *
   * A control runs in a sandboxed frame in the panel, not in this process, so
   * there is nothing to show in a console and no way to tell from here whether
   * it works. What the server can say is what will go wrong in a real space --
   * a script loaded from the internet, storage that throws in a sandbox, a
   * page sized in pixels -- and this is where that lands.
   */
  warnings?: string[];
}

export function CustomUiEditor({ path, source, onChange, warnings }: CustomUiEditorProps) {
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
        <div style={{ fontSize: "var(--font-size-base)", color: "var(--text-primary)" }}>
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
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ flex: 1, minHeight: 0 }}>
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
      </div>
      {warnings && warnings.length > 0 && (
        <div
          role="status"
          style={{
            flexShrink: 0,
            maxHeight: "40%",
            overflowY: "auto",
            borderTop: "1px solid var(--border-color)",
            background: "var(--color-warning-bg)",
            padding: "var(--space-sm) var(--space-md)",
          }}
        >
          <div
            style={{
              fontSize: "var(--font-size-sm)",
              fontWeight: 600,
              color: "var(--color-warning)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Saved. {warnings.length} thing{warnings.length === 1 ? "" : "s"} to fix
            before this goes on a panel
          </div>
          <ul
            style={{
              margin: 0,
              paddingLeft: "1.1rem",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-xs)",
            }}
          >
            {warnings.map((warning) => (
              <li
                key={warning}
                style={{
                  fontSize: "var(--font-size-sm)",
                  color: "var(--text-secondary)",
                  lineHeight: 1.5,
                }}
              >
                {warning}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
