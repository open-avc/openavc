// Import orchestration for a parsed .avc, extracted from ProjectView so its
// order-of-operations contract is unit-testable with injected fakes. The point:
// persist the parsed project THROUGH the server first (the server validates the
// full shape via Pydantic and rejects a wrong/corrupt file), and adopt it into
// the live store ONLY once the server has accepted it — so a malformed import
// can never be set on the store and crash dependent views. The old handler set
// the raw parsed object on the store before any validation.
import type { DeviceNeedingLogin, PluginNeedingLogin, ProjectConfig } from "../api/types";

export interface ImportProjectDeps {
  /** ETag of the current project, for the save's optimistic-concurrency check. */
  getEtag: () => string | undefined;
  /** PUT the project; rejects (non-2xx) when the server can't validate it. */
  saveProject: (project: ProjectConfig, etag?: string) => Promise<unknown>;
  /** Reload the engine so the imported project's devices become active. */
  reloadProject: () => Promise<unknown>;
  /** Pull the now-validated project from the server into the live store. */
  forceReload: () => Promise<void>;
  /** True for a 409 version conflict (distinct user message). */
  isConflict: (e: unknown) => boolean;
  /** Preserve the server's actionable reason for a refused import. */
  errorMessage: (e: unknown) => string;
  onError: (message: string) => void;
}

/** What an accepted import named: the devices and plugins that wait for
 *  their login, and what became of the files beside the project it replaced
 *  (`previousFiles`, with the server's sentence for it in `notice`). */
export interface ImportedProject {
  needsLogin: DeviceNeedingLogin[];
  pluginsNeedLogin: PluginNeedingLogin[];
  previousFiles?: "kept" | "set_aside";
  notice?: string;
}

/** What the import's answer named, each field kept only in the shape the
 *  server sends it. */
function importedFrom(saved: unknown): ImportedProject {
  const answer = (saved ?? {}) as {
    needs_login?: unknown; plugins_need_login?: unknown; previous_files?: unknown; notice?: unknown;
  };
  const { needs_login: needsLogin, plugins_need_login: pluginsNeedLogin, previous_files: previous, notice } = answer;
  return {
    needsLogin: Array.isArray(needsLogin) ? (needsLogin as DeviceNeedingLogin[]) : [],
    pluginsNeedLogin: Array.isArray(pluginsNeedLogin) ? (pluginsNeedLogin as PluginNeedingLogin[]) : [],
    previousFiles: previous === "kept" || previous === "set_aside" ? previous : undefined,
    notice: typeof notice === "string" && notice ? notice : undefined,
  };
}

/**
 * Persist a parsed project through the server and adopt it into the store only
 * on success. Returns what the import's answer named when it was accepted and
 * adopted (`ImportedProject`), false when the server
 * rejected it (in which case the store is left untouched and an error message
 * has been surfaced).
 */
export async function importParsedProject(
  parsed: ProjectConfig,
  deps: ImportProjectDeps,
): Promise<false | ImportedProject> {
  try {
    const saved = await deps.saveProject(parsed, deps.getEtag());
    await deps.reloadProject();
    await deps.forceReload();
    return importedFrom(saved);
  } catch (e) {
    deps.onError(
      deps.isConflict(e)
        ? "Another session changed the project. Reload, then import again."
        : deps.errorMessage(e),
    );
    return false;
  }
}
