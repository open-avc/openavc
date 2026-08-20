/**
 * A custom control points at one page in the project's `ui/` folder.
 *
 * The picker lists what is actually on disk rather than asking for a path,
 * because a typo here is a blank box with no error anywhere. Files come in
 * from right here too — dropped, or picked, a folder, a single file, or a
 * `.zip` of a control — so building one never means leaving the page you are
 * laying out. Writing the code itself is the Code view's job; this is the
 * door for the person placing the control.
 *
 * A custom PAGE uses the same door, with the same two fields — the only thing
 * that differs is what the first row is called.
 */
import { useCallback, useEffect, useState } from "react";
import {
  listCustomUiFiles,
  uploadCustomUiFiles,
  type CustomUiFile,
} from "../../../api/customUiClient";
import { filesFromDataTransfer, filesFromList } from "../../shared/dropFiles";
import { useUiFilesStore } from "../../../store/uiFilesStore";
import { FieldRow } from "./FieldRow";

/** The two fields this writes, shared by the element and the page that use it. */
export interface CustomFilePatch {
  custom_file?: string;
  custom_config?: Record<string, unknown>;
}

export function CustomControlConfig({
  file,
  config,
  onChange,
  label = "Control",
  settingsLabel = "Settings passed to the control (JSON):",
}: {
  file: string;
  config: Record<string, unknown>;
  onChange: (patch: CustomFilePatch) => void;
  label?: string;
  settingsLabel?: string;
}) {
  const [files, setFiles] = useState<CustomUiFile[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const listing = await listCustomUiFiles();
      setFiles(listing.files);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  // Only pages are entry points; the CSS, JS and images beside them are
  // fetched by the page itself, so offering them here would just be noise.
  const pages = files.filter((f) => /\.html?$/i.test(f.path));

  const upload = async (picked: { file: File; folder: string }[]) => {
    if (picked.length === 0) return;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const result = await uploadCustomUiFiles(picked);
      await refresh();
      // The files changed, not the project, so nothing else would tell the
      // design canvas to draw the new version.
      useUiFilesStore.getState().bump();
      setNote(
        result.skipped.length
          ? `Skipped ${result.skipped.length} file(s) this folder can't hold.`
          : null,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    // A dropped FOLDER only gives up its contents through the filesystem
    // entry API, and the files it yields carry no relative path of their own —
    // so the walk hands back the folder each file sat in. See dropFiles.ts.
    const dropped = await filesFromDataTransfer(e.dataTransfer);
    await upload(dropped);
  };

  return (
    <>
      <FieldRow label={label}>
        <select
          value={file}
          onChange={(e) => onChange({ custom_file: e.target.value })}
          style={{ flex: 1 }}
        >
          <option value="">Choose a page...</option>
          {/* Keep a file the project already names visible even if it is gone,
              so choosing something else is a decision and not a surprise. */}
          {file && !pages.some((p) => p.path === file) && (
            <option value={file}>{file} (missing)</option>
          )}
          {pages.map((f) => (
            <option key={f.path} value={f.path}>{f.path}</option>
          ))}
        </select>
      </FieldRow>

      <FieldRow label="Add files">
        <label
          data-testid="custom-ui-drop"
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { void handleDrop(e); }}
          style={{
            flex: 1, fontSize: 11, cursor: busy ? "wait" : "pointer",
            border: `1px dashed ${dragOver ? "var(--accent)" : "var(--border-color)"}`,
            borderRadius: 4,
            background: dragOver ? "var(--bg-hover)" : "transparent",
            padding: "6px 8px", textAlign: "center", color: "var(--text-muted)",
          }}
        >
          {busy ? "Uploading..." : dragOver ? "Drop to add" : "Drop files here, or choose files or a .zip"}
          <input
            type="file"
            multiple
            disabled={busy}
            onChange={(e) => { void upload(filesFromList(e.target.files)); e.target.value = ""; }}
            style={{ display: "none" }}
          />
        </label>
      </FieldRow>

      <div style={{ fontSize: 10, color: "var(--text-muted)", padding: "2px 0" }}>
        Files live in the project and travel with it. Every panel can read them,
        so keep passwords and customer data out.
      </div>

      {error && (
        <div style={{ fontSize: 10, color: "var(--color-error)", padding: "2px 0" }}>{error}</div>
      )}
      {note && (
        <div style={{ fontSize: 10, color: "var(--text-muted)", padding: "2px 0" }}>{note}</div>
      )}

      <div style={{ fontSize: 10, color: "var(--text-muted)", padding: "2px 0" }}>
        {settingsLabel}
      </div>
      <textarea
        value={JSON.stringify(config, null, 2)}
        onChange={(e) => {
          try { onChange({ custom_config: JSON.parse(e.target.value) }); } catch { /* invalid JSON */ }
        }}
        rows={3}
        style={{ width: "100%", fontSize: 11, fontFamily: "monospace", resize: "vertical" }}
      />
    </>
  );
}
