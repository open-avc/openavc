import type {
  DriverCommandDef,
  DriverDefinition,
  DriverDeviceSettingDef,
} from "../../api/types";
import type { DriverValidationIssue } from "../../api/driverClient";
// Contract constant tables, generated from the platform's driver-contract
// registry (types.gen.ts).
import { DISALLOWED_OPEN_PORTS } from "../../api/types";

// Re-exported for the Discovery editor, which shows the rule inline at
// authoring time.
export { DISALLOWED_OPEN_PORTS };

// ─────────────────────────────────────────────────────────────────────────
// The Driver Builder does not know the driver contract. It asks.
//
// Every rule about what a driver file may contain lives in one place, on the
// platform: `avcdriver_semantic.validate_driver_definition`, which is what the
// save route, the driver loader, the AI tool and the community catalog all
// run. The editor gets those rules from POST /api/driver-definitions/validate
// and renders what comes back (see `issuesFromServer` below).
//
// This module used to carry a second copy of that contract — ~200 rules
// written separately in TypeScript. Two copies of one contract drift, and
// they did: a driver the editor called broken saved fine, and three drivers
// the platform ships could not be opened in the editor at all.
//
// What is left here is everything that carries NO contract knowledge:
//
//   * `commandRoute` / `scrubForTransport` / `oscArgValueIssue` — editor
//     mechanics. Which sender the runtime will route a command to, what a
//     transport switch has to strip, whether an OSC argument box holds a
//     number. The Live Test panel and the OSC argument editor show these
//     inline as you type; they are not "is this driver valid" questions.
//   * `issuesFromServer` — the path → tab mapping.
//   * `validateDriver` — the two things the endpoint structurally cannot
//     answer (it validates one definition, in isolation, with no view of the
//     driver library) plus publishing advice that never blocks anything.
//
// Adding a rule about what a driver file may contain? It goes in `spec.py` /
// `avcdriver_semantic.py`, and both the editor and the platform get it. Never
// here — that is the mirror growing back.
// ─────────────────────────────────────────────────────────────────────────

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

const PLACEHOLDER_RE = /\{(\w+)\}/g;

// ── Transport ↔ command-shape routing ──────────────────────────────────
// The runtime routes each command/setting-write by SHAPE, not by the
// driver's transport (configurable.py): anything with an `address` goes to
// the OSC sender, else `path`/`method` goes to HTTP, else the raw `send`
// string. These helpers give the editor the same routing knowledge, so the
// Live Test panel can say which sender a command will reach and a transport
// switch can strip the fields the new sender would ignore.

export type CommandRoute = "osc" | "http" | "raw";

/** Fields that route a definition to a specific sender, or ride along with it. */
const OSC_SHAPE_FIELDS = ["address", "args"] as const;
const HTTP_SHAPE_FIELDS = ["method", "path", "body", "headers", "query_params"] as const;
const RAW_SHAPE_FIELDS = ["send", "string"] as const;

/** Which sender the runtime will route this command/write to (shape-based,
 *  mirroring configurable.py's `_is_osc_command` / `_is_http_command`). */
export function commandRoute(cmd: {
  address?: string;
  path?: string;
  method?: string;
}): CommandRoute {
  if (cmd.address !== undefined) return "osc";
  if (cmd.path !== undefined || cmd.method !== undefined) return "http";
  return "raw";
}

/** True when a wire field carries authored content — worth telling the user
 *  about before removing, and enough to give a command something to send
 *  (vs. an empty seed like `send: ""`).
 *
 *  Only ever asked about wire fields (send/string, address/args,
 *  method/path/body/headers/query_params), which is why a string is tested
 *  for emptiness and NOT trimmed. Whitespace is a wire value: a device whose
 *  documented keepalive is a bare line feed authors `send: "\n"`, and the
 *  runtime sends it. Trimming here made the transport scrub throw such a
 *  keepalive away without saying so, and (before the rules moved to the
 *  server) locked that driver out of the editor entirely. */
function hasContent(value: unknown): boolean {
  if (value == null) return false;
  if (typeof value === "string") return value !== "";
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return true;
}

export interface TransportScrubRemoval {
  /** "power_on" for a command, "volume (setting)" for a device setting. */
  name: string;
  fields: string[];
}

export interface TransportScrubResult {
  commands: Record<string, DriverCommandDef>;
  device_settings?: Record<string, DriverDeviceSettingDef>;
  /** Fields with authored content that the scrub removed — show these in a
   *  confirm prompt before applying. Empty seeds are scrubbed silently. */
  removals: TransportScrubRemoval[];
}

/** Strip wire-format fields that don't apply to `nextTransport` from every
 *  command and device-setting write, so a transport switch can't leave
 *  invisible stale shapes behind (the form only renders the active
 *  transport's fields, so a leftover `address` on a TCP driver is
 *  uneditable and silently kills the command at runtime). */
export function scrubForTransport(
  draft: DriverDefinition,
  nextTransport: string,
): TransportScrubResult {
  const dropFields: string[] =
    nextTransport === "osc"
      ? [...HTTP_SHAPE_FIELDS, ...RAW_SHAPE_FIELDS]
      : nextTransport === "http"
        ? [...OSC_SHAPE_FIELDS, ...RAW_SHAPE_FIELDS]
        : [...OSC_SHAPE_FIELDS, ...HTTP_SHAPE_FIELDS];

  const removals: TransportScrubRemoval[] = [];

  const scrubObject = (
    obj: Record<string, unknown>,
    displayName: string,
    keepSendKey: boolean,
  ): Record<string, unknown> => {
    const next: Record<string, unknown> = { ...obj };
    const removed: string[] = [];
    for (const field of dropFields) {
      if (!(field in next)) continue;
      if (hasContent(next[field])) removed.push(field);
      // `send` is a required key on commands (every seed carries it), so
      // clear it instead of deleting; everything else is dropped outright.
      if (field === "send" && keepSendKey) {
        next[field] = "";
      } else {
        delete next[field];
      }
    }
    if (removed.length > 0) removals.push({ name: displayName, fields: removed });
    return next;
  };

  const commands: Record<string, DriverCommandDef> = {};
  for (const [name, cmd] of Object.entries(draft.commands ?? {})) {
    commands[name] = scrubObject(
      cmd as unknown as Record<string, unknown>,
      name,
      true,
    ) as unknown as DriverCommandDef;
  }

  let device_settings: Record<string, DriverDeviceSettingDef> | undefined;
  if (draft.device_settings && Object.keys(draft.device_settings).length > 0) {
    device_settings = {};
    for (const [name, setting] of Object.entries(draft.device_settings)) {
      if (!setting.write) {
        device_settings[name] = setting;
        continue;
      }
      const nextWrite = scrubObject(
        setting.write as unknown as Record<string, unknown>,
        `${name} (setting)`,
        false,
      );
      // A write emptied by the scrub is dropped entirely — its wire format
      // can't survive the transport switch, so it's honestly gone. The
      // platform still requires a write block on every device setting, so
      // its validation flags the setting until it's re-authored.
      const scrubbed = { ...setting } as DriverDeviceSettingDef;
      if (Object.keys(nextWrite).length > 0) {
        scrubbed.write = nextWrite as DriverDeviceSettingDef["write"];
      } else {
        delete scrubbed.write;
      }
      device_settings[name] = scrubbed;
    }
  }

  return { commands, device_settings, removals };
}

/** Author-time check for a single OSC argument value, mirroring the
 *  runtime's coercion (configurable.py `_build_osc_args`): numeric tags
 *  crash the send on a non-numeric value, int64 additionally rejects
 *  fractions. Values containing a {placeholder} resolve at send time and
 *  can't be checked statically. Returns a message, or null when fine. */
export function oscArgValueIssue(type: string, value: string): string | null {
  if (type === "T" || type === "F" || type === "N" || type === "s") return null;
  const v = (value ?? "").trim();
  if (v === "") return "needs a numeric value (it is sent as a number)";
  if (v.includes("{")) return null; // parameter placeholder — resolved at send
  const n = Number(v);
  if (!Number.isFinite(n)) return `"${v}" is not a number`;
  if (type === "h" && !Number.isInteger(n)) {
    return `"${v}" must be a whole number for Int64 (h)`;
  }
  return null;
}

// ── The server's issues, placed on the editor's tabs ─────────────────────
//
// Every issue the platform returns carries a `path` saying where in the
// definition it belongs ("commands.mute", "config_schema.host",
// "responses[2]", "" for a whole-driver rule). The editor needs one thing
// from that: which tab to show it on. This table is the whole mapping — read
// off DriverEditor's own layout, keyed by the first segment of the path.
//
// It covers every top-level field in the contract, because the platform's
// unknown-key check reports against any of them, and a root with no entry
// here would quietly land on General. `tests/test_driver_builder_validate.py`
// fails when the contract grows a field this table doesn't place, so adding
// one is a change that has to say which tab it belongs to.

const SECTION_BY_PATH_ROOT: Record<string, IssueSection> = {
  // General — identity, catalog metadata, publishing.
  id: "general",
  name: "general",
  version: "general",
  author: "general",
  description: "general",
  category: "general",
  manufacturer: "general",
  tags: "general",
  help: "general",
  compatible_models: "general",
  min_platform_version: "general",
  source_url: "general",
  protocols: "general",
  ports: "general",
  simulated: "general",
  verified: "general",
  deprecated: "general",
  replacement_id: "general",
  inline_protocol: "general",
  ir_codes: "general",

  // Connection — everything about opening and holding the link.
  transport: "connection",
  transports: "connection",
  delimiter: "connection",
  bridge: "connection",
  command_prefix: "connection",
  command_suffix: "connection",
  auth: "connection",
  push: "connection",
  liveness: "connection",
  // "Connect Sequence" lives on the Connection tab, not Behavior.
  on_connect: "connection",
  frame_parser: "connection",
  send_frame: "connection",
  config_schema: "connection",
  default_config: "connection",
  config_derived: "connection",

  // Behavior — what the driver does once it's talking.
  state_variables: "behavior",
  child_entity_types: "behavior",
  commands: "behavior",
  actions: "behavior",
  quick_actions: "behavior",
  web_ui: "behavior",
  responses: "behavior",
  polling: "behavior",
  device_settings: "behavior",

  // Tabs of their own.
  discovery: "discovery",
  simulator: "simulation",
};

/** First path segment: "commands.mute" → "commands", "responses[2]" →
 *  "responses", "" → "" (a rule about the whole driver). */
function pathRoot(path: string): string {
  const cut = path.search(/[.[]/);
  return cut === -1 ? path : path.slice(0, cut);
}

/** Turn the platform's issue list into what the editor renders.
 *
 *  A whole-driver rule (empty path) goes to General, which is where a
 *  driver's identity is edited and the tab the editor opens on. */
export function issuesFromServer(
  issues: DriverValidationIssue[],
): ValidationIssue[] {
  return issues.map((issue) => ({
    severity: issue.severity === "warning" ? "warning" : "error",
    section: SECTION_BY_PATH_ROOT[pathRoot(issue.path ?? "")] ?? "general",
    field: issue.path || undefined,
    message: issue.message,
  }));
}

// ── What the Builder still checks for itself ─────────────────────────────

/** Wire fields the routed sender will ignore — leftovers from a transport
 *  switch or a hand-edited file. Advice, never a refusal: the platform loads
 *  the driver and the command still works, it just carries dead fields. */
function strayWireFields(obj: Record<string, unknown>): {
  route: CommandRoute;
  stray: string[];
} {
  const route = commandRoute(
    obj as { address?: string; path?: string; method?: string },
  );
  const present = (fields: readonly string[]) =>
    fields.filter((f) => hasContent(obj[f]));
  const stray =
    route === "osc"
      ? [...present(RAW_SHAPE_FIELDS), ...present(HTTP_SHAPE_FIELDS)]
      : route === "http"
        ? [...present(RAW_SHAPE_FIELDS)]
        : [...present(["body", "headers", "query_params"])];
  return { route, stray };
}

/**
 * Validate a driver draft against everything the *editor* knows.
 *
 * That is deliberately almost nothing. The contract rules come from the
 * server (`issuesFromServer`); this covers the two things it cannot:
 *
 *   * a duplicate driver id — the endpoint validates one definition in
 *     isolation and has no view of the library the editor is holding;
 *   * publishing and housekeeping advice that must never block a save.
 *
 * Throws on a draft whose field types are unexpected — call
 * `validateDriverSafely` instead of this, from anywhere a throw would be
 * user-visible.
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

  // ── Identity collision ────────────────────────────────────────────────
  // Not a contract rule — a fact about the driver library this editor is
  // showing, which the server's per-definition check cannot see. Saving
  // anyway is refused by the create route with a 409; this says so first.
  if (
    draft.id &&
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

  // ── Housekeeping advice ───────────────────────────────────────────────
  // Everything below is a warning, and every one of them describes a driver
  // the platform loads and runs. They are here rather than in the contract
  // because there is nothing to refuse — a child type with no label works,
  // it just shows up blank in the Child Entities tab.

  for (const [typeName, typeDef] of Object.entries(
    draft.child_entity_types ?? {},
  )) {
    if (!typeDef || typeof typeDef !== "object" || Array.isArray(typeDef)) {
      continue; // shape is the platform's call
    }
    if (!typeDef.label?.trim()) {
      issues.push({
        severity: "warning",
        section: "behavior",
        field: `child_entity_types.${typeName}`,
        message: `Child type "${typeName}" has no label. Integrators see this in the Child Entities tab.`,
      });
    }
    const fieldNames = Object.keys(typeDef.state_variables ?? {});
    if (fieldNames.length === 0) {
      issues.push({
        severity: "warning",
        section: "behavior",
        field: `child_entity_types.${typeName}`,
        message: `Child type "${typeName}" declares no state fields. Each child would only carry the platform's online/label keys.`,
      });
    }
    // summary_fields / label_field naming a field that isn't declared:
    // the row simply renders nothing. `online` and `label` are
    // platform-injected, so they're always valid targets.
    const fieldSet = new Set([...fieldNames, "online", "label"]);
    for (const sf of typeDef.summary_fields ?? []) {
      if (!fieldSet.has(sf)) {
        issues.push({
          severity: "warning",
          section: "behavior",
          field: `child_entity_types.${typeName}.summary_fields`,
          message: `Child type "${typeName}" summary field "${sf}" isn't a declared state field.`,
        });
      }
    }
    if (typeDef.label_field && !fieldSet.has(typeDef.label_field)) {
      issues.push({
        severity: "warning",
        section: "behavior",
        field: `child_entity_types.${typeName}.label_field`,
        message: `Child type "${typeName}" name field "${typeDef.label_field}" isn't a declared state field.`,
      });
    }
  }

  // A default stored as the wrong primitive (e.g. "5" on an integer field)
  // exports wrong-typed YAML that anything reading default_config directly
  // trips over.
  for (const [fieldName, rawDef] of Object.entries(draft.config_schema ?? {})) {
    if (!rawDef || typeof rawDef !== "object") continue;
    const fieldDef = rawDef as { type?: string; secret?: boolean };
    if (fieldDef.secret === true) continue; // the platform warns about these
    const configDefault = (draft.default_config ?? {})[fieldName];
    if (configDefault === undefined || configDefault === null || configDefault === "") {
      continue;
    }
    const declaredType = fieldDef.type ?? "string";
    if (declaredType === "boolean" && typeof configDefault !== "boolean") {
      issues.push({
        severity: "warning",
        section: "connection",
        field: `config_schema.${fieldName}`,
        message: `Config field "${fieldName}" default is ${typeof configDefault} but the field type is boolean — re-enter the default in the Config editor so it saves as true/false.`,
      });
    } else if (
      (declaredType === "integer" || declaredType === "number" || declaredType === "float") &&
      typeof configDefault !== "number"
    ) {
      issues.push({
        severity: "warning",
        section: "connection",
        field: `config_schema.${fieldName}`,
        message: `Config field "${fieldName}" default is ${typeof configDefault} but the field type is ${declaredType} — re-enter the default in the Config editor so it saves as a number.`,
      });
    }
  }

  // Substitutable config tokens mirror the runtime's config dict: declared
  // schema fields, default_config-only keys, computed (config_derived)
  // values, and the baseline transport keys the runtime injects.
  const configKeys = new Set([
    ...Object.keys(draft.config_schema ?? {}),
    ...Object.keys(draft.default_config ?? {}),
    ...Object.keys(draft.config_derived ?? {}),
    ...BASELINE_CONFIG_KEYS,
  ]);

  for (const [cmdName, cmd] of Object.entries(draft.commands ?? {})) {
    const declaredParams = new Set(Object.keys(cmd.params ?? {}));

    // An undeclared {token} leaves a literal "{token}" on the wire.
    const seen = new Set<string>();
    for (const wire of collectWireStrings(cmd)) {
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

    const { route, stray } = strayWireFields(
      cmd as unknown as Record<string, unknown>,
    );
    if (stray.length > 0) {
      issues.push({
        severity: "warning",
        section: "behavior",
        command: cmdName,
        message: `Command "${cmdName}" has ${stray.join(", ")} which the ${route.toUpperCase()} sender ignores — usually leftovers from a transport switch. Remove them to keep the driver clean.`,
      });
    }
  }

  for (const [settingName, setting] of Object.entries(
    draft.device_settings ?? {},
  )) {
    const write = setting?.write as Record<string, unknown> | undefined;
    if (!write || typeof write !== "object") continue;
    const { route, stray } = strayWireFields(write);
    if (stray.length > 0) {
      issues.push({
        severity: "warning",
        section: "behavior",
        message: `Device setting "${settingName}" write has ${stray.join(", ")} which the ${route.toUpperCase()} sender ignores — usually leftovers from a transport switch. Remove them to keep the driver clean.`,
      });
    }
  }

  // A duplicate bridge port id: the runtime keeps the last declaration, so
  // the earlier one silently never resolves.
  const bridgePorts = (draft.bridge as { ports?: unknown } | undefined)?.ports;
  if (Array.isArray(bridgePorts)) {
    const seenIds = new Set<string>();
    for (const entry of bridgePorts) {
      const id = (entry as Record<string, unknown> | null)?.id;
      if (typeof id !== "string" || !id.trim()) continue;
      if (seenIds.has(id)) {
        issues.push({
          severity: "warning",
          section: "connection",
          field: "bridge",
          message: `Bridge port "${id}" duplicates another port's id — the runtime keeps only the last declaration.`,
        });
      }
      seenIds.add(id);
    }
  }

  // Computed config values: an unknown {token} makes the value always empty
  // (derive_config treats a missing field as ""), and a name that collides
  // with a declared config field silently replaces the configured one.
  const derived = draft.config_derived;
  if (derived && typeof derived === "object" && !Array.isArray(derived)) {
    const declared = new Set([
      ...Object.keys(draft.config_schema ?? {}),
      ...Object.keys(draft.default_config ?? {}),
    ]);
    const tokenKeys = new Set([
      ...declared,
      ...BASELINE_CONFIG_KEYS,
      ...Object.keys(derived),
    ]);
    for (const [name, template] of Object.entries(derived)) {
      if (declared.has(name) || BASELINE_CONFIG_KEYS.has(name)) {
        issues.push({
          severity: "warning",
          section: "connection",
          field: `config_derived.${name}`,
          message: `Computed field "${name}" has the same name as a config field — the computed value silently replaces the configured one when the device connects. Rename one of them.`,
        });
      }
      if (typeof template !== "string") continue;
      // Templates accept {name} and {name:format_spec} — the same token shape
      // the runtime substitutes (compiled_protocol.derive_config).
      const re = /\{(\w+)(?::[^{}]*)?\}/g;
      const seen = new Set<string>();
      let m: RegExpExecArray | null;
      while ((m = re.exec(template))) {
        const token = m[1];
        if (seen.has(token) || tokenKeys.has(token)) continue;
        seen.add(token);
        issues.push({
          severity: "warning",
          section: "connection",
          field: `config_derived.${name}`,
          message: `Computed field "${name}" references {${token}}, but no config field or computed field of that name exists — the computed value would always be empty.`,
        });
      }
    }
  }

  // An IR code-set device emits through a bridge's IR port. The platform
  // accepts any transport; the pairing is advice the field doc gives.
  if (draft.ir_codes === true && draft.transport !== "bridge") {
    issues.push({
      severity: "warning",
      section: "general",
      field: "ir_codes",
      message:
        "An IR code-set device emits through an IR bridge port and has no address of its own — set the transport to Bridge so it's added from a bridge's IR port.",
    });
  }

  // Link URLs and the web_ui template substitute {host}, {port} and declared
  // config fields; an unknown token is left verbatim in the opened URL.
  const urlKeys = new Set([...configKeys]);
  const checkUrlTemplate = (
    where: string,
    field: string,
    section: IssueSection,
    template: string,
  ) => {
    const seen = new Set<string>();
    const re = new RegExp(PLACEHOLDER_RE.source, "g");
    let m: RegExpExecArray | null;
    while ((m = re.exec(template))) {
      const token = m[1];
      if (seen.has(token)) continue;
      seen.add(token);
      if (urlKeys.has(token)) continue;
      issues.push({
        severity: "warning",
        section,
        field,
        message: `${where} references {${token}}, but only {host}, {port}, and declared config fields are substituted — the placeholder would be left in the opened URL.`,
      });
    }
  };
  if (typeof draft.web_ui === "string" && draft.web_ui) {
    checkUrlTemplate(
      "The web interface URL template",
      "web_ui",
      "behavior",
      draft.web_ui,
    );
  }
  for (const action of draft.actions ?? []) {
    if (action?.kind !== "link" || typeof action.url !== "string") continue;
    checkUrlTemplate(
      `Action "${action.id ?? action.label ?? "link"}"'s URL`,
      "actions",
      "behavior",
      action.url,
    );
  }

  return issues;
}

/** `validateDriver`, guarded.
 *
 *  A driver file is arbitrary YAML and the editor validates inside a render,
 *  so one unexpected value type (`author: 2026` parses as a number) would
 *  blank the whole editor with no way back. Every caller must come through
 *  here; `tests/test_driver_builder_validate.py` fails if one doesn't. */
export function validateDriverSafely(
  draft: DriverDefinition,
  siblings: DriverDefinition[],
  originalId: string | null,
): ValidationIssue[] {
  try {
    return validateDriver(draft, siblings, originalId);
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    return [
      {
        severity: "error",
        section: "general",
        field: "id",
        message:
          "This driver file has a value in a form the editor cannot read, so " +
          "it could not be fully checked. It is usually a field that needs " +
          "quotes in the YAML — a version, date or author written as a bare " +
          `number is the common one. (${detail})`,
      },
    ];
  }
}

/** Concatenate every wire-format field that supports {placeholders}. */
function collectWireStrings(cmd: DriverCommandDef): string[] {
  const out: string[] = [];
  const push = (s: string | undefined | null) => {
    if (s) out.push(s);
  };

  push(cmd.send);
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
