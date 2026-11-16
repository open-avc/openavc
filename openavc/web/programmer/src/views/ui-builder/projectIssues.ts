/**
 * What Validate finds in a project, asked once per version of it: the report
 * the toolbar's Validate opens, the count its button carries and the count the
 * Dashboard's update notice gives all come from here, so they never disagree.
 *
 * A version is the project object the store holds; an edit replaces it, so a
 * cached answer belongs to the version it was asked of. Validate pressed asks
 * again (`fresh`), since the platform's answer about commands can change
 * without the project changing (a driver installed).
 */
import { useEffect, useState } from "react";
import type { ProjectConfig } from "../../api/types";
import { commandIssues } from "../../api/commandIssues";
import {
  projectCommandActions,
  validateProject,
  type ValidationIssue,
} from "../../components/ui-builder/uiBuilderHelpers";

const asked = new WeakMap<ProjectConfig, Promise<ValidationIssue[]>>();

/** Validate's issues for this version of the project. */
export function projectIssues(project: ProjectConfig, fresh = false): Promise<ValidationIssue[]> {
  const known = fresh ? undefined : asked.get(project);
  if (known) return known;
  const found = commandIssues(projectCommandActions(project)).then((issues) => validateProject(project, issues));
  asked.set(project, found);
  return found;
}

/** How long an edit has to rest before the count is worked out again. */
export const ISSUE_COUNT_DELAY_MS = 600;

/** Validate's issues for the project as it stands, worked out once edits
 *  pause; null until the first answer. */
export function useProjectIssues(project: ProjectConfig | null): ValidationIssue[] | null {
  const [issues, setIssues] = useState<ValidationIssue[] | null>(null);
  useEffect(() => {
    if (!project) return;
    let current = true;
    const timer = setTimeout(() => {
      void projectIssues(project).then((found) => {
        if (current) setIssues(found);
      });
    }, ISSUE_COUNT_DELAY_MS);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [project]);
  return project ? issues : null;
}
