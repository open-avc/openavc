// Device logins in a project's export and import.
//
// A login saved on this system stays here unless somebody asks for it in an
// export: then the export holds it sealed with a passphrase they type, and an
// import of that file asks for the passphrase before the project opens. A
// skipped or wrong passphrase leaves those devices on Needs login, as an
// export without logins does. The server does the sealing and opening
// (core/login_bundle.py); the passphrase is sent in a request body and is
// never kept.

import { useId, useState } from "react";
import { Dialog } from "../components/shared/Dialog";
import { parseApiError } from "../api/errors";
import * as api from "../api/restClient";

/** The shortest passphrase the server accepts (MIN_PASSPHRASE in
 *  core/login_bundle.py). */
export const MIN_PASSPHRASE = 8;

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  marginBottom: "var(--space-xs)",
};
const inputStyle: React.CSSProperties = { width: "100%", marginBottom: "var(--space-md)" };
const noteStyle: React.CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  lineHeight: 1.4,
  marginBottom: "var(--space-md)",
};
const btnStyle: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-md)",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  fontSize: "var(--font-size-sm)",
  cursor: "pointer",
};
const primaryStyle: React.CSSProperties = {
  ...btnStyle,
  background: "var(--accent-bg)",
  color: "var(--text-on-accent)",
};

/** A button's look, dimmed while it cannot be pressed. */
function looks(style: React.CSSProperties, disabled: boolean): React.CSSProperties {
  return disabled ? { ...style, opacity: 0.5, cursor: "not-allowed" } : style;
}

/** Why a passphrase pair cannot seal yet, or null when it can. */
export function passphraseProblem(passphrase: string, again: string): string | null {
  if (passphrase.normalize("NFC").length < MIN_PASSPHRASE) {
    return `Use at least ${MIN_PASSPHRASE} characters.`;
  }
  if (again !== passphrase) return "The two passphrases don't match.";
  return null;
}

/** Export, for a project with device logins saved on this system: include
 *  them sealed with a passphrase, or leave them out. `exportProject` is
 *  given the passphrase, or undefined for an export without logins. */
export function ExportProjectDialog({
  onClose,
  exportProject,
}: {
  onClose: () => void;
  exportProject: (loginsPassphrase: string | undefined) => Promise<void>;
}) {
  const id = useId();
  const [include, setInclude] = useState(false);
  const [passphrase, setPassphrase] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const problem = include ? passphraseProblem(passphrase, again) : null;
  const mismatch = include && again !== "" && again !== passphrase;

  const submit = async () => {
    if (problem) return;
    setBusy(true);
    setError(null);
    try {
      await exportProject(include ? passphrase : undefined);
      onClose();
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog title="Export Project" onClose={onClose}>
      <label
        style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", marginBottom: "var(--space-md)" }}
      >
        <input
          type="checkbox"
          checked={include}
          onChange={(e) => {
            setInclude(e.target.checked);
            setError(null);
          }}
        />
        <span style={{ fontSize: "var(--font-size-sm)" }}>Include device logins</span>
      </label>
      {include && (
        <>
          <label htmlFor={`${id}-passphrase`} style={labelStyle}>Passphrase</label>
          <input
            id={`${id}-passphrase`}
            type="password"
            autoComplete="new-password"
            autoFocus
            aria-describedby={`${id}-length`}
            style={{ ...inputStyle, marginBottom: "var(--space-xs)" }}
            value={passphrase}
            onChange={(e) => setPassphrase(e.target.value)}
          />
          <p id={`${id}-length`} style={{ ...noteStyle, color: "var(--text-muted)" }}>
            At least {MIN_PASSPHRASE} characters.
          </p>
          <label htmlFor={`${id}-again`} style={labelStyle}>Passphrase again</label>
          <input
            id={`${id}-again`}
            type="password"
            autoComplete="new-password"
            style={inputStyle}
            value={again}
            onChange={(e) => setAgain(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void submit();
            }}
          />
          {mismatch && (
            <p role="alert" style={{ ...noteStyle, color: "var(--color-error)" }}>
              The two passphrases don't match.
            </p>
          )}
          <p style={noteStyle}>
            Anyone with this file and the passphrase can read these logins. A forgotten
            passphrase can't be recovered.
          </p>
        </>
      )}
      {error && (
        <p role="alert" style={{ ...noteStyle, color: "var(--color-error)" }}>{error}</p>
      )}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)" }}>
        <button onClick={onClose} style={btnStyle}>Cancel</button>
        <button
          onClick={() => void submit()}
          style={looks(primaryStyle, busy || problem !== null)}
          disabled={busy || problem !== null}
        >
          {busy ? "Exporting..." : "Export"}
        </button>
      </div>
    </Dialog>
  );
}

/** The day a bundle was sealed, the way the prompt names it. */
export function sealedDay(sealedAt: string): string {
  const when = new Date(sealedAt);
  if (Number.isNaN(when.getTime())) return sealedAt;
  return when.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/** An import whose file holds sealed device logins: the passphrase brings
 *  them in for library copy `libraryId`, Skip leaves them out. `onDone` is
 *  called once either has happened. */
export function ImportLoginsDialog({
  libraryId,
  sealedAt,
  onDone,
}: {
  libraryId: string;
  sealedAt: string;
  onDone: (outcome: "added" | "skipped") => void;
}) {
  const id = useId();
  const [passphrase, setPassphrase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const skip = async () => {
    try {
      await api.skipImportedLogins(libraryId);
    } catch {
      /* forgotten by the server on its own within the hour */
    }
    onDone("skipped");
  };

  const bringIn = async () => {
    if (passphrase === "") return;
    setBusy(true);
    setError(null);
    try {
      await api.addImportedLogins(libraryId, passphrase);
      onDone("added");
    } catch (e) {
      setError(parseApiError(e));
      setBusy(false);
    }
  };

  return (
    <Dialog title="Device Logins" onClose={() => void skip()}>
      <p style={noteStyle}>
        This export includes device logins, sealed on {sealedDay(sealedAt)}. Enter its passphrase
        to bring them in, or skip.
      </p>
      <label htmlFor={`${id}-passphrase`} style={labelStyle}>Passphrase</label>
      <input
        id={`${id}-passphrase`}
        type="password"
        autoComplete="off"
        autoFocus
        style={inputStyle}
        value={passphrase}
        onChange={(e) => {
          setPassphrase(e.target.value);
          setError(null);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") void bringIn();
        }}
      />
      {error && (
        <p role="alert" style={{ ...noteStyle, color: "var(--color-error)" }}>{error}</p>
      )}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)" }}>
        <button onClick={() => void skip()} style={looks(btnStyle, busy)} disabled={busy}>Skip</button>
        <button
          onClick={() => void bringIn()}
          style={looks(primaryStyle, busy || passphrase === "")}
          disabled={busy || passphrase === ""}
        >
          {busy ? "Bringing them in..." : "Bring them in"}
        </button>
      </div>
    </Dialog>
  );
}
