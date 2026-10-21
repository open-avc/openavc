import { useRef } from "react";
import type { CSSProperties } from "react";
import { Upload, X } from "lucide-react";
import type { DriverParamDef } from "../../api/types";
import { acceptAttribute, describeSize, fileRefusal } from "./commandFiles";
import { InlineError } from "./InlineError";

/** A command's file param: a file picker showing the chosen file and its size,
 *  and the reason a file will not do (too large, the wrong kind) before it is
 *  sent. Without `onFile` the surface cannot send a file at all, and says
 *  where it can. */
export function FileParamInput({
  name,
  def,
  file,
  onFile,
  onName,
  style,
}: {
  name: string;
  def: Partial<DriverParamDef>;
  file: File | null;
  onFile?: (file: File | null) => void;
  /** The form's value for this param: the file's name, or "" for none. */
  onName: (value: string) => void;
  style?: CSSProperties;
}) {
  const input = useRef<HTMLInputElement>(null);
  if (!onFile) {
    return (
      <div style={{ fontSize: 11, color: "var(--text-muted)", ...style }}>
        The file is chosen when this command is sent from the device's page.
      </div>
    );
  }
  const choose = (picked: File | null) => {
    onFile(picked);
    onName(picked ? picked.name : "");
  };
  const refusal = file ? fileRefusal(name, def, file) : null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, ...style }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", minWidth: 0 }}>
        <button
          type="button"
          onClick={() => input.current?.click()}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            padding: "var(--space-xs) var(--space-md)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            background: "var(--bg-hover)",
            color: "var(--text-primary)",
            flexShrink: 0,
          }}
        >
          <Upload size={14} /> {file ? "Change file" : "Choose file"}
        </button>
        <span
          style={{
            fontSize: "var(--font-size-sm)",
            color: file ? "var(--text-primary)" : "var(--text-muted)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={file?.name}
        >
          {file ? `${file.name}, ${describeSize(file.size)}` : "No file chosen"}
        </span>
        {file && (
          <button
            type="button"
            aria-label={`Clear ${file.name}`}
            title="Clear"
            onClick={() => choose(null)}
            style={{ display: "flex", padding: 2, color: "var(--text-muted)", flexShrink: 0 }}
          >
            <X size={12} />
          </button>
        )}
        <input
          ref={input}
          type="file"
          accept={acceptAttribute(def)}
          data-testid={`file-param-${name}`}
          style={{ display: "none" }}
          onChange={(e) => {
            const picked = e.target.files?.[0] ?? null;
            // Cleared so choosing the same file again still counts as a choice.
            e.target.value = "";
            choose(picked);
          }}
        />
      </div>
      {refusal && (
        <InlineError
          message={refusal}
          style={{ padding: "2px 8px", marginTop: 0, fontSize: 11, borderRadius: 4 }}
        />
      )}
    </div>
  );
}
