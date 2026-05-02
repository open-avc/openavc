import type { DriverDefinition, DriverCommandDef } from "../../api/types";

export type IssueSection =
  | "general"
  | "connection"
  | "behavior"
  | "discovery"
  | "simulation"
  | "test";

export interface ValidationIssue {
  severity: "error" | "warning";
  section: IssueSection;
  message: string;
  /** Identity anchor for inline rendering — caller decides what to do. */
  field?: string;
  command?: string;
  param?: string;
}

/** Built-in transport config keys that the runtime injects automatically. */
const BASELINE_CONFIG_KEYS = new Set([
  "host",
  "port",
  "baudrate",
  "parity",
  "bytesize",
  "stopbits",
  "poll_interval",
  "inter_command_delay",
  "username",
  "password",
  "timeout",
  "token",
  "api_key",
]);

const ID_RE = /^[a-z][a-z0-9_]*$/;
const PARAM_NAME_RE = /^[a-zA-Z_][a-zA-Z0-9_]*$/;
const PLACEHOLDER_RE = /\{(\w+)\}/g;

/**
 * Validate a driver draft against the runtime contract.
 *
 * Returns a flat list of issues; consumers slice by section to render.
 * Errors block save (caller's responsibility); warnings flag publish-quality
 * problems (missing description, etc.) without blocking.
 *
 * @param draft   The current draft.
 * @param siblings Other saved definitions — used for ID collision detection.
 *                 Pass an empty array for a brand-new draft with no peers.
 * @param originalId The id this draft was loaded under (null for a new draft).
 *                   Lets us skip the "duplicate id" warning when the user is
 *                   editing in place without renaming.
 */
export function validateDriver(
  draft: DriverDefinition,
  siblings: DriverDefinition[],
  originalId: string | null,
): ValidationIssue[] {
  const issues: ValidationIssue[] = [];

  // ── Identity ──────────────────────────────────────────────────────────
  if (!draft.id) {
    issues.push({
      severity: "error",
      section: "general",
      field: "id",
      message: "Driver ID is required.",
    });
  } else if (!ID_RE.test(draft.id)) {
    issues.push({
      severity: "error",
      section: "general",
      field: "id",
      message:
        "ID must start with a lowercase letter and use only lowercase letters, digits, and underscores.",
    });
  } else if (
    draft.id !== originalId &&
    siblings.some((s) => s.id === draft.id && s.id !== originalId)
  ) {
    issues.push({
      severity: "error",
      section: "general",
      field: "id",
      message: `Another driver named "${draft.id}" already exists. Choose a different ID.`,
    });
  }

  if (!draft.name?.trim()) {
    issues.push({
      severity: "error",
      section: "general",
      field: "name",
      message: "Driver name is required.",
    });
  }

  // ── Publish-quality warnings ──────────────────────────────────────────
  if (!draft.description?.trim()) {
    issues.push({
      severity: "warning",
      section: "general",
      field: "description",
      message:
        "Description is empty. Required for community drivers — describe the device family in one sentence.",
    });
  }
  if (!draft.version?.trim()) {
    issues.push({
      severity: "warning",
      section: "general",
      field: "version",
      message: "Version is empty. Use semver (e.g. 1.0.0).",
    });
  }
  if (!draft.author?.trim()) {
    issues.push({
      severity: "warning",
      section: "general",
      field: "author",
      message: "Author is empty. Required for community drivers.",
    });
  }
  if (!draft.help?.overview?.trim()) {
    issues.push({
      severity: "warning",
      section: "general",
      field: "help.overview",
      message:
        "Help overview is empty. Integrators see this in the Add Device dialog — explain what the device is.",
    });
  }

  // ── Commands: param-name legality + placeholder coverage ─────────────
  const configKeys = new Set([
    ...Object.keys(draft.config_schema ?? {}),
    ...BASELINE_CONFIG_KEYS,
  ]);

  for (const [cmdName, cmd] of Object.entries(draft.commands ?? {})) {
    const declaredParams = new Set(Object.keys(cmd.params ?? {}));

    // Param-name legality. The renamer used to silently strip illegal
    // characters; flag the residue so the user understands what got
    // trimmed.
    for (const paramName of declaredParams) {
      if (!PARAM_NAME_RE.test(paramName)) {
        issues.push({
          severity: "error",
          section: "behavior",
          command: cmdName,
          param: paramName,
          message: `Parameter "${paramName}" in command "${cmdName}" has illegal characters. Use letters, digits, and underscores only.`,
        });
      }
    }

    // Walk every wire-format string and collect placeholders. Anything
    // not in declared params or config keys is undeclared — almost
    // always a typo that would silently leave a literal {token} on
    // the wire.
    const wireStrings = collectWireStrings(cmd);
    const seen = new Set<string>();
    for (const wire of wireStrings) {
      let m: RegExpExecArray | null;
      const re = new RegExp(PLACEHOLDER_RE.source, "g");
      while ((m = re.exec(wire))) {
        const token = m[1];
        if (seen.has(token)) continue;
        seen.add(token);
        if (declaredParams.has(token) || configKeys.has(token)) continue;
        issues.push({
          severity: "warning",
          section: "behavior",
          command: cmdName,
          message: `Command "${cmdName}" references {${token}} but no parameter or config field of that name is declared.`,
        });
      }
    }
  }

  return issues;
}

/** Concatenate every wire-format field that supports {placeholders}. */
function collectWireStrings(cmd: DriverCommandDef): string[] {
  const out: string[] = [];
  const push = (s: string | undefined | null) => {
    if (s) out.push(s);
  };

  push(cmd.send);
  push(cmd.string);
  push(cmd.path);
  push(cmd.body);
  push(cmd.address);

  if (cmd.headers) {
    for (const [k, v] of Object.entries(cmd.headers)) {
      out.push(`${k}: ${v}`);
    }
  }
  if (cmd.query_params) {
    for (const [k, v] of Object.entries(cmd.query_params)) {
      out.push(`${k}=${v}`);
    }
  }
  if (cmd.args) {
    for (const a of cmd.args) {
      if (a.value) out.push(a.value);
    }
  }
  return out;
}

/** Filter helpers used by editors and tab badges. */
export function issuesFor(
  issues: ValidationIssue[],
  section: IssueSection,
): ValidationIssue[] {
  return issues.filter((i) => i.section === section);
}

export function hasError(issues: ValidationIssue[]): boolean {
  return issues.some((i) => i.severity === "error");
}

export function hasWarning(issues: ValidationIssue[]): boolean {
  return issues.some((i) => i.severity === "warning");
}
