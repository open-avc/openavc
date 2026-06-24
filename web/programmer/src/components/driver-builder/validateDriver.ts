import type {
  DriverCommandDef,
  DriverDefinition,
  DriverDeviceSettingDef,
} from "../../api/types";

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

// Mirror of DISALLOWED_OPEN_PORTS in server/discovery/hints.py — a port_open
// hint on one of these matches every web/SSH host, so the runtime rejects it.
// Exported so the Discovery editor shows the rule inline at authoring time.
export const DISALLOWED_OPEN_PORTS: ReadonlySet<number> = new Set([
  22, 80, 443, 8000, 8080, 8443, 8888,
]);
// Child type ids and per-child field ids share the device state-key
// namespace, so they follow the same lowercase-identifier rule.
const CHILD_ID_RE = /^[a-z][a-z0-9_]*$/;

// ── Transport ↔ command-shape routing ──────────────────────────────────
// The runtime routes each command/setting-write by SHAPE, not by the
// driver's transport (configurable.py): anything with an `address` goes to
// the OSC sender, else `path`/`method` goes to HTTP, else the raw `send`
// string. A mis-shaped command (e.g. an OSC address left behind after the
// transport was switched to TCP) is refused by the sender's transport
// guard at runtime — the command is dead with only a log line. These
// helpers give the editor the same routing knowledge so stale shapes are
// flagged at author time and scrubbed on a transport switch.

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

/** True when a field's value carries authored content worth telling the
 *  user about before removing (vs. an empty seed like `send: ""`). */
function hasContent(value: unknown): boolean {
  if (value == null) return false;
  if (typeof value === "string") return value.trim() !== "";
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
      // A write emptied by the scrub is dropped entirely — the runtime
      // treats a missing write as a read-only setting, which is the honest
      // state once its wire format is gone.
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

  // ── Child entity types ───────────────────────────────────────────────
  const childTypes = draft.child_entity_types ?? {};
  const childTypeNames = new Set(Object.keys(childTypes));
  for (const [typeName, typeDef] of Object.entries(childTypes)) {
    if (!CHILD_ID_RE.test(typeName)) {
      issues.push({
        severity: "error",
        section: "behavior",
        field: `child_entity_types.${typeName}`,
        message: `Child type "${typeName}" must start with a lowercase letter and use only lowercase letters, digits, and underscores.`,
      });
    }
    if (!typeDef.label?.trim()) {
      issues.push({
        severity: "warning",
        section: "behavior",
        field: `child_entity_types.${typeName}`,
        message: `Child type "${typeName}" has no label. Integrators see this in the Child Entities tab.`,
      });
    }

    // id_format sanity. v1 only supports integer IDs; the runtime raises
    // on anything else, so flag a non-integer type as an error.
    const idf = typeDef.id_format ?? { type: "integer" };
    if (idf.type !== "integer") {
      issues.push({
        severity: "error",
        section: "behavior",
        field: `child_entity_types.${typeName}.id_format`,
        message: `Child type "${typeName}" id_format.type must be "integer" (only integer IDs are supported).`,
      });
    }
    if (
      typeof idf.min === "number" &&
      typeof idf.max === "number" &&
      idf.max < idf.min
    ) {
      issues.push({
        severity: "error",
        section: "behavior",
        field: `child_entity_types.${typeName}.id_format`,
        message: `Child type "${typeName}" id_format.max (${idf.max}) is less than min (${idf.min}).`,
      });
    }
    if (typeof idf.pad_width === "number" && idf.pad_width < 0) {
      issues.push({
        severity: "error",
        section: "behavior",
        field: `child_entity_types.${typeName}.id_format`,
        message: `Child type "${typeName}" id_format.pad_width can't be negative.`,
      });
    }

    // State fields.
    const stateVars = typeDef.state_variables ?? {};
    const fieldNames = Object.keys(stateVars);
    if (fieldNames.length === 0) {
      issues.push({
        severity: "warning",
        section: "behavior",
        field: `child_entity_types.${typeName}`,
        message: `Child type "${typeName}" declares no state fields. Each child would only carry the platform's online/label keys.`,
      });
    }
    for (const fieldName of fieldNames) {
      if (!CHILD_ID_RE.test(fieldName)) {
        issues.push({
          severity: "error",
          section: "behavior",
          field: `child_entity_types.${typeName}.${fieldName}`,
          message: `Field "${fieldName}" in child type "${typeName}" must use lowercase letters, digits, and underscores only.`,
        });
      }
    }

    // summary_fields / label_field must reference declared fields. `online`
    // and `label` are platform-injected, so they're always valid targets.
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

  // ── Commands: param-name legality + placeholder coverage ─────────────
  const configKeys = new Set([
    ...Object.keys(draft.config_schema ?? {}),
    ...BASELINE_CONFIG_KEYS,
  ]);

  for (const [cmdName, cmd] of Object.entries(draft.commands ?? {})) {
    const declaredParams = new Set(Object.keys(cmd.params ?? {}));

    // child_id params must name a declared child type, else the runtime
    // command picker has nothing to populate the dropdown from.
    for (const [paramName, paramDef] of Object.entries(cmd.params ?? {})) {
      if (paramDef.type !== "child_id") continue;
      if (!paramDef.child_type) {
        issues.push({
          severity: "error",
          section: "behavior",
          command: cmdName,
          param: paramName,
          message: `Parameter "${paramName}" in command "${cmdName}" is a Child ID but no child type is selected.`,
        });
      } else if (!childTypeNames.has(paramDef.child_type)) {
        issues.push({
          severity: "error",
          section: "behavior",
          command: cmdName,
          param: paramName,
          message: `Parameter "${paramName}" in command "${cmdName}" references child type "${paramDef.child_type}", which isn't declared in Child Entity Types.`,
        });
      }
    }

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

    // Transport ↔ shape consistency. The runtime routes by shape and its
    // senders refuse a transport mismatch, so a stale shape (usually left
    // behind by a transport switch in an older builder, an import, or
    // hand-edited YAML) is a dead command at runtime.
    issues.push(
      ...shapeMismatchIssues(
        cmd as unknown as Record<string, unknown>,
        draft.transport,
        `Command "${cmdName}"`,
        cmdName,
      ),
    );

    // OSC argument values: numeric tags crash the send on an empty or
    // non-numeric value (the builder seeds new args with value "").
    if (draft.transport === "osc" && commandRoute(cmd) === "osc") {
      (cmd.args ?? []).forEach((arg, i) => {
        const problem = oscArgValueIssue(arg.type, arg.value);
        if (problem) {
          issues.push({
            severity: "error",
            section: "behavior",
            command: cmdName,
            message: `Command "${cmdName}" OSC argument ${i + 1} ${problem}.`,
          });
        }
      });
    }
  }

  // ── Device settings: write shapes follow the same routing rules ───────
  for (const [settingName, setting] of Object.entries(
    draft.device_settings ?? {},
  )) {
    const write = setting.write;
    if (!write || Object.keys(write).length === 0) continue; // read-only
    issues.push(
      ...shapeMismatchIssues(
        write as Record<string, unknown>,
        draft.transport,
        `Device setting "${settingName}" write`,
        undefined,
      ),
    );
    if (draft.transport === "osc" && commandRoute(write) === "osc") {
      (write.args ?? []).forEach((arg, i) => {
        const problem = oscArgValueIssue(arg.type, arg.value);
        if (problem) {
          issues.push({
            severity: "error",
            section: "behavior",
            message: `Device setting "${settingName}" OSC argument ${i + 1} ${problem}.`,
          });
        }
      });
    }
  }

  // ── Auth login handshake ─────────────────────────────────────────────
  // Mirror the runtime's load-time rules (validate_driver_definition in
  // driver_loader.py) so authors see these in the Connection tab rather than
  // only as a save rejection. A misdeclared handshake silently connects
  // unauthenticated or breaks the transport's data path at runtime.
  const auth = draft.auth;
  if (auth) {
    if (auth.type && auth.type !== "telnet_login") {
      issues.push({
        severity: "error",
        section: "connection",
        field: "auth.type",
        message: `Login handshake type "${auth.type}" isn't supported (only "telnet_login").`,
      });
    }
    if (
      draft.transport &&
      draft.transport !== "tcp" &&
      draft.transport !== "serial"
    ) {
      issues.push({
        severity: "error",
        section: "connection",
        field: "auth",
        message: `Login handshake only works on TCP or serial transports, not ${draft.transport}. Disable it or change the transport.`,
      });
    }
    if (!auth.username_prompt?.trim()) {
      issues.push({
        severity: "error",
        section: "connection",
        field: "auth.username_prompt",
        message:
          "Login handshake needs a username prompt to watch for, or it connects unauthenticated.",
      });
    }
    if (!auth.password_prompt?.trim()) {
      issues.push({
        severity: "error",
        section: "connection",
        field: "auth.password_prompt",
        message:
          "Login handshake needs a password prompt to watch for, or it connects unauthenticated.",
      });
    }
  }

  // ── Discovery hints (mirror server/discovery/hints.py rules so the user
  //    sees them here, not as an opaque 422 at save) ──────────────────────
  validateDiscovery(draft, issues);

  return issues;
}

/** A discovery list entry is blank if it's an empty string, or an object whose
 *  primary identifying field is empty/whitespace. */
function hasBlankEntry(arr: unknown[] | undefined, primaryKey?: string): boolean {
  return (arr ?? []).some((e) => {
    if (typeof e === "string") return e.trim() === "";
    if (e && typeof e === "object" && primaryKey) {
      const v = (e as Record<string, unknown>)[primaryKey];
      return typeof v === "string" && v.trim() === "";
    }
    return false;
  });
}

function validateDiscovery(
  draft: DriverDefinition,
  issues: ValidationIssue[],
): void {
  const disc = draft.discovery;
  if (!disc) return;

  // Disallowed open ports — match every web/SSH host, rejected at runtime.
  for (const port of disc.port_open ?? []) {
    if (DISALLOWED_OPEN_PORTS.has(port)) {
      issues.push({
        severity: "error",
        section: "discovery",
        field: "port_open",
        message: `Port ${port} is too common to identify a device — it matches every web/SSH host. Remove it.`,
      });
    }
  }

  // Blank rows the runtime rejects (added-but-not-filled).
  const blankChecks: [unknown[] | undefined, string | undefined, string, string][] = [
    [disc.oui, undefined, "oui", "OUI list"],
    [disc.hostname, undefined, "hostname", "Hostname list"],
    [disc.manufacturer_alias, undefined, "manufacturer_alias", "Manufacturer alias list"],
    [disc.mdns, "service", "mdns", "mDNS fingerprint"],
    [disc.ssdp, "device_type", "ssdp", "SSDP fingerprint"],
    [disc.amx_ddp, "make", "amx_ddp", "AMX DDP fingerprint"],
  ];
  for (const [arr, primaryKey, field, label] of blankChecks) {
    if (hasBlankEntry(arr, primaryKey)) {
      issues.push({
        severity: "error",
        section: "discovery",
        field,
        message: `${label} has a blank entry — fill it in or remove the row.`,
      });
    }
  }

  // A probe may declare at most one response matcher (runtime: exactly one of
  // expect / expect_regex / expect_hex; more than one is rejected).
  for (const [field, probe] of [
    ["tcp_probe", disc.tcp_probe],
    ["udp_probe", disc.udp_probe],
  ] as const) {
    if (!probe) continue;
    const declared = (["expect", "expect_regex", "expect_hex"] as const).filter(
      (k) => probe[k] !== undefined && probe[k] !== "",
    );
    if (declared.length > 1) {
      issues.push({
        severity: "error",
        section: "discovery",
        field,
        message: `A ${field === "tcp_probe" ? "TCP" : "UDP"} probe can declare only one matcher — pick one of substring, regex, or hex prefix (found ${declared.join(", ")}).`,
      });
    }
  }
}

/** Issues for one command/setting-write whose shape doesn't match the
 *  driver transport (dead at runtime), plus warnings for authored leftovers
 *  the matching sender ignores. */
function shapeMismatchIssues(
  obj: Record<string, unknown>,
  transport: string,
  label: string,
  command: string | undefined,
): ValidationIssue[] {
  const out: ValidationIssue[] = [];
  const route = commandRoute(
    obj as { address?: string; path?: string; method?: string },
  );
  const present = (fields: readonly string[]) =>
    fields.filter((f) => hasContent(obj[f]));
  const tn = (transport || "tcp").toUpperCase();

  if (route === "osc" && transport !== "osc") {
    out.push({
      severity: "error",
      section: "behavior",
      command,
      message: `${label} has OSC fields (address/args) but the driver transport is ${tn}. The runtime sends anything with an address as OSC, which fails on a non-OSC transport. Remove the OSC fields or set the transport to OSC.`,
    });
  } else if (route === "http" && transport !== "http") {
    const fields = present(HTTP_SHAPE_FIELDS);
    out.push({
      severity: "error",
      section: "behavior",
      command,
      message: `${label} has HTTP fields (${fields.join(", ") || "method/path"}) but the driver transport is ${tn}. The runtime sends anything with a method or path as an HTTP request, which fails on a non-HTTP transport. Remove the HTTP fields or set the transport to HTTP.`,
    });
  } else if (route === "raw" && transport === "osc") {
    out.push({
      severity: "error",
      section: "behavior",
      command,
      message: `${label} has no OSC address. OSC messages are an address path plus typed arguments — set the address.`,
    });
  } else if (route === "raw" && transport === "http") {
    out.push({
      severity: "error",
      section: "behavior",
      command,
      message: `${label} has no HTTP method or path, so it can't be sent as an HTTP request.`,
    });
  } else {
    if (route === "osc" && !String(obj.address ?? "").trim()) {
      out.push({
        severity: "error",
        section: "behavior",
        command,
        message: `${label} has an empty OSC address. Set the address path (e.g. /ch/01/mix/fader).`,
      });
    }
    // Route matches the transport — flag authored leftovers the sender
    // ignores (typically residue from a transport switch in older builds
    // or hand-edited YAML).
    const stray: string[] =
      route === "osc"
        ? [...present(RAW_SHAPE_FIELDS), ...present(HTTP_SHAPE_FIELDS)]
        : route === "http"
          ? [...present(RAW_SHAPE_FIELDS)]
          : [...present(["body", "headers", "query_params"])];
    if (stray.length > 0) {
      out.push({
        severity: "warning",
        section: "behavior",
        command,
        message: `${label} has ${stray.join(", ")} which the ${route === "raw" ? tn : route.toUpperCase()} sender ignores — usually leftovers from a transport switch. Remove them to keep the driver clean.`,
      });
    }
  }
  return out;
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
