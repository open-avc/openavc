/**
 * Validate's report: what the toolbar's Validate found, one row per issue with
 * where it is. A row that names a page goes there, and to its control when it
 * names one, and closes the report; a row with no page (a master's) stays put.
 *
 * Validate asks the platform one question it cannot answer itself: whether
 * each chosen command has the parameters its driver requires. Everything else
 * is derived from the project in hand, so a request that fails still leaves a
 * full report; it just cannot speak to that one thing.
 */
import { useCallback, useState } from "react";
import type { ProjectConfig } from "../../api/types";
import { commandIssues } from "../../api/commandIssues";
import {
  projectCommandActions,
  validateProject,
  type ValidationIssue,
} from "../../components/ui-builder/uiBuilderHelpers";
import { Modal } from "../../components/shared/Modal";
import { useUIBuilderStore } from "../../store/uiBuilderStore";

/** How a row is marked: its tag, the tag's colour, the row's fill and edge. */
const ERROR_LOOK = { tag: "ERR", colour: "var(--color-error)", fill: "rgba(244,67,54,0.08)", edge: "rgba(244,67,54,0.2)" };
const WARNING_LOOK = { tag: "WARN", colour: "#ff9800", fill: "rgba(255,152,0,0.08)", edge: "rgba(255,152,0,0.2)" };

/** The report Validate last made (null while none is open), the run that
 *  makes one, and closing it. */
export function useValidation(project: ProjectConfig | null) {
  const [issues, setIssues] = useState<ValidationIssue[] | null>(null);
  const run = useCallback(async () => {
    if (!project) return;
    const found = await commandIssues(projectCommandActions(project));
    setIssues(validateProject(project, found));
  }, [project]);
  const close = useCallback(() => setIssues(null), []);
  return { issues, run, close };
}

/** The count in the heading, after "Project Validation". */
function countSays(count: number): string {
  if (count === 0) return "(No Issues)";
  return `(${count} issue${count === 1 ? "" : "s"})`;
}

function goTo(issue: ValidationIssue, close: () => void) {
  if (!issue.pageId) return;
  useUIBuilderStore.getState().selectPage(issue.pageId);
  if (issue.elementId) useUIBuilderStore.getState().selectElement(issue.elementId);
  close();
}

function IssueRow({ issue, onClose }: { issue: ValidationIssue; onClose: () => void }) {
  const look = issue.severity === "error" ? ERROR_LOOK : WARNING_LOOK;
  return (
    <div
      style={{
        display: "flex", gap: 8, padding: "6px 8px", borderRadius: 4, fontSize: 12,
        background: look.fill,
        border: `1px solid ${look.edge}`,
        cursor: issue.pageId ? "pointer" : "default",
      }}
      onClick={() => goTo(issue, onClose)}
    >
      <span style={{ color: look.colour, fontWeight: 600, flexShrink: 0 }}>
        {look.tag}
      </span>
      <div>
        <div style={{ fontWeight: 500 }}>{issue.message}</div>
        <div style={{ color: "var(--text-muted)", fontSize: 11 }}>{issue.location}</div>
      </div>
    </div>
  );
}

export function ValidationReport({ issues, onClose }: { issues: ValidationIssue[]; onClose: () => void }) {
  return (
    <Modal
      onClose={onClose}
      label="Validation Results"
      overlayStyle={{ background: "rgba(0,0,0,0.5)" }}
      panelStyle={{
        borderRadius: 8,
        border: "1px solid var(--border-color)",
        padding: 20, minWidth: 400, maxWidth: 600, maxHeight: "70vh",
        display: "flex", flexDirection: "column", gap: 12,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>
          Project Validation {countSays(issues.length)}
        </h3>
        <button onClick={onClose} style={{ background: "transparent", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: 16 }}>&times;</button>
      </div>
      {issues.length === 0 ? (
        <div style={{ color: "var(--color-success)", fontSize: 13, padding: "12px 0" }}>
          All bindings, pages, devices, and macros are valid.
        </div>
      ) : (
        <div style={{ overflowY: "auto", display: "flex", flexDirection: "column", gap: 4 }}>
          {issues.map((issue, i) => (
            <IssueRow key={i} issue={issue} onClose={onClose} />
          ))}
        </div>
      )}
    </Modal>
  );
}
