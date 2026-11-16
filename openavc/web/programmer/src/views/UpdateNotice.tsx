/**
 * The Dashboard's notice of what loading the running project changed in it,
 * when an earlier release saved it (the server's record,
 * core/project_update.py). It stays, through restarts, until somebody
 * dismisses it; its button opens Validate in the UI Builder, since nobody
 * presses Validate after an update.
 */
import { useCallback, useEffect, useState } from "react";
import { Info } from "lucide-react";
import * as api from "../api/restClient";
import type { UpdateNote } from "../api/projectClient";
import { parseApiError } from "../api/errors";
import { useConnectionStore } from "../store/connectionStore";
import { useNavigationStore } from "../store/navigationStore";
import { useProjectStore } from "../store/projectStore";
import { showError } from "../store/toastStore";
import { useUIBuilderStore } from "../store/uiBuilderStore";
import { useProjectIssues } from "./ui-builder/projectIssues";
import { updateSummary } from "./updateSummary";

/** The record's notes, fetched again whenever the server writes or clears it. */
function useUpdateNotes(): [UpdateNote[], () => void] {
  const at = useConnectionStore((s) => String(s.liveState["system.project_update_at"] ?? ""));
  const [notes, setNotes] = useState<UpdateNote[]>([]);
  useEffect(() => {
    let current = true;
    api.getUpdateNotice()
      .then((notice) => { if (current) setNotes(notice.notes); })
      .catch(() => { /* A notice that cannot be read shows nothing. */ });
    return () => { current = false; };
  }, [at]);
  const dismiss = useCallback(() => {
    api.dismissUpdateNotice()
      .then(() => setNotes([]))
      .catch((e) => showError(`Could not dismiss the notice: ${parseApiError(e)}`));
  }, []);
  return [notes, dismiss];
}

function openValidate() {
  useUIBuilderStore.getState().requestValidate(true);
  useNavigationStore.getState().navigateTo("ui-builder");
}

function goTo(note: UpdateNote) {
  useNavigationStore.getState().navigateTo("ui-builder", {
    type: "element", id: note.element_id, detail: `page:${note.page_id}`,
  });
}

function NoteRow({ note }: { note: UpdateNote }) {
  const reachable = !!(note.page_id && note.element_id);
  return (
    <li
      onClick={reachable ? () => goTo(note) : undefined}
      style={{ cursor: reachable ? "pointer" : "default", padding: "2px 0" }}
    >
      <span style={{ color: note.kind === "found" ? "#ff9800" : "var(--text-primary)" }}>{note.message}</span>
      <span style={{ color: "var(--text-muted)", fontSize: 11, marginLeft: 6 }}>{note.location}</span>
    </li>
  );
}

const BUTTON: React.CSSProperties = {
  background: "var(--bg-hover)",
  border: "1px solid var(--border-color)",
  color: "var(--text-primary)",
  borderRadius: "var(--border-radius)",
  padding: "4px 12px",
  fontSize: "var(--font-size-sm)",
  cursor: "pointer",
};

export function UpdateNotice() {
  const [notes, dismiss] = useUpdateNotes();
  const project = useProjectStore((s) => s.project);
  // Validate runs only while there is a notice to count for.
  const issues = useProjectIssues(notes.length ? project : null);
  if (!notes.length) return null;
  const summary = updateSummary(notes, issues?.length ?? null);

  return (
    <div
      role="status"
      style={{
        background: "var(--bg-surface)",
        border: "1px solid var(--accent)",
        borderLeft: "3px solid var(--accent)",
        borderRadius: "var(--border-radius)",
        padding: "var(--space-lg)",
        marginBottom: "var(--space-lg)",
        display: "flex",
        alignItems: "flex-start",
        gap: "var(--space-md)",
      }}
    >
      <Info size={18} style={{ color: "var(--accent)", flexShrink: 0, marginTop: 2 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: "var(--font-size-sm)" }}>
          {summary ? `This project was updated: ${summary}` : "This project was updated"}
        </div>
        <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 13, maxHeight: 180, overflowY: "auto" }}>
          {notes.map((note, i) => <NoteRow key={i} note={note} />)}
        </ul>
        <div style={{ display: "flex", gap: "var(--space-sm)", marginTop: "var(--space-md)" }}>
          <button type="button" onClick={openValidate} style={BUTTON}>Open Validate</button>
          <button type="button" onClick={dismiss} style={{ ...BUTTON, background: "transparent" }}>Dismiss</button>
        </div>
      </div>
    </div>
  );
}
