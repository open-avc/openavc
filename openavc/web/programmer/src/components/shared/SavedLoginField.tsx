/**
 * A login box that saves on its own: a plugin's password or API key, the
 * Inter-System mesh key.
 *
 * The browser never receives a saved login. The server sends the "saved on
 * this system" marker in its place, and this field shows it as "Saved on this
 * system" with Change, never as a value. A login typed here lives only in this
 * field until its own Save sends it, so a form that saves itself as you type
 * never carries part of a password, and nothing a parent holds ever has one.
 *
 * Edit Device's saved logins look the same (views/devices/DeviceDialogs.tsx),
 * but go with that dialog's one Save.
 */
import { useId, useState, type ReactNode } from "react";
import { KeyRound } from "lucide-react";
import { parseApiError } from "../../api/errors";

/** Where a login stands on this system:
 *  - `saved`: the project marks it saved here and it is;
 *  - `needs_login`: the project marks it saved, but this system has none
 *    (a restore onto another system, an import without logins);
 *  - `none`: nothing is saved yet;
 *  - `checking`: not known yet. */
export type SavedLoginState = "saved" | "needs_login" | "none" | "checking";

/** Keeps a browser's password manager from filling in or offering to save a
 *  device or plugin login (NeedsLoginBanner.tsx carries the same). */
export const PASSWORD_MANAGER_IGNORE = {
  "data-1p-ignore": true,
  "data-lpignore": "true",
  "data-bwignore": true,
} as const;

export const NOT_SAVED_HERE = "Not saved on this system. Enter it here.";
export const KEEP_SAVED_PLACEHOLDER = "Leave blank to keep the saved one";

const smallButton: React.CSSProperties = {
  padding: "2px var(--space-sm)",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  fontSize: "var(--font-size-sm)",
};

const inputStyle: React.CSSProperties = {
  flex: 1,
  // Below this the buttons wrap under the box rather than squeeze it (the
  // UI Builder's narrow Bindings panel).
  minWidth: 160,
  padding: "var(--space-sm) var(--space-md)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-surface)",
  color: "var(--text-primary)",
  fontSize: "var(--font-size-base)",
};

/** "Saved on this system" with Change, as Edit Device draws it. */
export function SavedOnThisSystem({
  label,
  onChange,
  actions,
}: {
  label: string;
  onChange: () => void;
  /** More buttons beside Change (the mesh key's Show). */
  actions?: ReactNode;
}) {
  const id = useId();
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", flexWrap: "wrap" }}>
      <KeyRound size={14} aria-hidden style={{ color: "var(--text-muted)", flexShrink: 0 }} />
      <span id={`${id}-saved`} style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>
        Saved on this system
      </span>
      {actions}
      <button
        type="button"
        aria-label={`Change ${label}`}
        aria-describedby={`${id}-saved`}
        onClick={onChange}
        style={smallButton}
      >
        Change
      </button>
    </div>
  );
}

export function SavedLoginField({
  label,
  state,
  onSave,
  placeholder,
  savedActions,
  savedView,
  saveOnLeave,
}: {
  label: string;
  state: SavedLoginState;
  /** Send the login typed. Resolve `false` (or throw) when it was not saved,
   *  and the box stays open with what was typed; a thrown error's reason is
   *  shown under it. */
  onSave: (value: string) => Promise<boolean | void>;
  /** For an empty box (nothing saved, or not saved here). */
  placeholder?: string;
  /** More buttons beside Change while the login is saved. */
  savedActions?: ReactNode;
  /** Drawn in place of "Saved on this system" while set (a shown mesh key). */
  savedView?: ReactNode;
  /** Save what was typed when the box is left for anywhere but its own
   *  buttons: on a surface whose other fields save themselves (a macro step,
   *  a control's action), its own Save is easy to miss. */
  saveOnLeave?: boolean;
}) {
  const [changing, setChanging] = useState(false);
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const noteId = useId();

  if (state === "checking") {
    return (
      <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
        Checking…
      </div>
    );
  }

  const close = () => {
    setChanging(false);
    setValue("");
    setError(null);
  };

  if (state === "saved" && !changing) {
    return savedView ? (
      <>{savedView}</>
    ) : (
      <SavedOnThisSystem label={label} onChange={() => setChanging(true)} actions={savedActions} />
    );
  }

  // A saved login being changed: blank keeps it. Otherwise there is nothing
  // to keep, and Save waits for something typed.
  const keeping = state === "saved";

  const save = async () => {
    if (value === "") {
      if (keeping) close();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const saved = await onSave(value);
      if (saved === false) return;
      close();
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <form
        style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-xs)", alignItems: "center" }}
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <input
          type="password"
          autoComplete="new-password"
          {...PASSWORD_MANAGER_IGNORE}
          aria-label={label}
          aria-describedby={state === "needs_login" ? noteId : undefined}
          autoFocus={changing}
          value={value}
          disabled={saving}
          placeholder={keeping ? KEEP_SAVED_PLACEHOLDER : placeholder}
          onChange={(e) => setValue(e.target.value)}
          onBlur={(e) => {
            if (saveOnLeave && value !== "" && !e.currentTarget.form?.contains(e.relatedTarget)) void save();
          }}
          style={inputStyle}
        />
        <button
          type="submit"
          aria-label={`Save ${label}`}
          disabled={saving || (!keeping && value === "")}
          style={{ ...smallButton, opacity: saving || (!keeping && value === "") ? 0.5 : 1 }}
        >
          {saving ? "Saving..." : "Save"}
        </button>
        {keeping && (
          <button type="button" onClick={close} disabled={saving} style={smallButton}>
            Cancel
          </button>
        )}
      </form>
      {state === "needs_login" && (
        <div id={noteId} style={{ fontSize: 11, color: "var(--text-muted)", marginTop: "var(--space-xs)" }}>
          {NOT_SAVED_HERE}
        </div>
      )}
      {error && (
        <div role="alert" style={{ fontSize: 11, color: "var(--color-error)", marginTop: "var(--space-xs)" }}>
          {error}
        </div>
      )}
    </div>
  );
}
