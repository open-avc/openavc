import { useEffect, useState } from "react";

export interface RenameResult {
  ok: boolean;
  reason?: string;
}

/**
 * An id input that buffers keystrokes locally and commits the rename on blur
 * (or Enter). Renaming on every keystroke remounts the row — editors key rows
 * by the id being edited — which drops focus and swallows intermediate
 * collisions/empties. Committing on blur keeps the row mounted while typing.
 * Mirrors CommandBuilder's draftName pattern. Shared by the Driver Builder
 * editors (child entity types, device settings).
 */
export function IdRenameInput({
  value,
  sanitize,
  onCommit,
  style,
  placeholder,
  "data-testid": testid,
}: {
  value: string;
  sanitize: (raw: string) => string;
  onCommit: (next: string) => RenameResult;
  style?: React.CSSProperties;
  placeholder?: string;
  "data-testid"?: string;
}) {
  const [draft, setDraft] = useState(value);
  const [error, setError] = useState<string | null>(null);

  // Re-sync if the canonical value changes from outside (e.g. parent rename).
  useEffect(() => {
    setDraft(value);
    setError(null);
  }, [value]);

  const commit = () => {
    if (draft === value) {
      setError(null);
      return;
    }
    const result = onCommit(draft);
    if (result.ok) {
      setError(null);
      setDraft(value); // re-sync no-op renames (sanitized === current)
    } else {
      setError(result.reason ?? "Invalid id.");
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <input
        data-testid={testid}
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(sanitize(e.target.value))}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") {
            setDraft(value);
            setError(null);
            (e.target as HTMLInputElement).blur();
          }
        }}
        style={{
          width: "100%",
          ...style,
          borderColor: error ? "var(--color-error)" : style?.borderColor,
        }}
      />
      {error && (
        <div style={{ fontSize: 11, color: "var(--color-error)" }}>{error}</div>
      )}
    </div>
  );
}
