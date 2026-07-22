import type {
  DriverCommandDef,
  DriverDefinition,
  DriverDeviceSettingDef,
} from "../../api/types";
// Contract constant tables, generated from the platform's driver-contract
// registry (types.gen.ts) — the runtime, the catalog validator, and this
// validator all read the same tables, so they can't drift apart.
import {
  ACTION_AVAILABILITIES,
  ACTION_KINDS_YAML,
  AUTH_TRANSPORTS,
  DISALLOWED_OPEN_PORTS,
  FRAME_HEADER_SIZES,
  LENGTH_ENDIANS,
  LIVENESS_TRANSPORTS,
  PUSH_FRAME_PARSER_TYPES,
  PUSH_KEYS_BY_TYPE,
  STATE_VAR_TYPES,
  STRUCT_LENGTH_SIZES,
  VISIBLE_WHEN_OPERATORS,
} from "../../api/types";

// Re-exported for the Discovery editor, which shows the rule inline at
// authoring time.
export { DISALLOWED_OPEN_PORTS };

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
      // A write emptied by the scrub is dropped entirely — its wire format
      // can't survive the transport switch, so it's honestly gone. The
      // runtime loader still requires a write block on every device
      // setting, so validation flags the setting as an error until it's
      // re-authored for the new transport.
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
  // Config fields a query's `when:` gate may name (either block declares them).
  const gateFieldNames = new Set([
    ...Object.keys(draft.config_schema ?? {}),
    ...Object.keys(draft.default_config ?? {}),
  ]);
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

    // id_format sanity (mirror driver_loader.py): integer or string local
    // ids. The runtime raises on anything else.
    const idf = typeDef.id_format ?? { type: "integer" };
    if (idf.type !== "integer" && idf.type !== "string") {
      issues.push({
        severity: "error",
        section: "behavior",
        field: `child_entity_types.${typeName}.id_format`,
        message: `Child type "${typeName}" id_format.type must be "integer" or "string".`,
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

    // Instances roster (mirror driver_loader.py): exactly one source; count
    // sane; *_from must name a declared config field. A bad block would
    // silently register nothing at runtime.
    const inst = typeDef.instances;
    if (inst) {
      const configFields = new Set([
        ...Object.keys(draft.config_schema ?? {}),
        ...Object.keys(draft.default_config ?? {}),
      ]);
      const sources = (["count", "count_from", "ids_from", "ids"] as const).filter(
        (k) => inst[k] !== undefined,
      );
      if (sources.length !== 1) {
        issues.push({
          severity: "error",
          section: "behavior",
          field: `child_entity_types.${typeName}.instances`,
          message: `Child type "${typeName}" instances must declare exactly one of a fixed count, a count config field, an ID-list config field, or a fixed ID list.`,
        });
      } else if (sources[0] === "ids") {
        const ids = inst.ids;
        if (!Array.isArray(ids) || ids.length === 0) {
          issues.push({
            severity: "error",
            section: "behavior",
            field: `child_entity_types.${typeName}.instances`,
            message: `Child type "${typeName}" instances ids must be a non-empty list of literal child IDs.`,
          });
        } else if (
          idf.type !== "string" &&
          ids.some((v) => !/^\d+$/.test(String(v).trim()))
        ) {
          issues.push({
            severity: "error",
            section: "behavior",
            field: `child_entity_types.${typeName}.instances`,
            message: `Child type "${typeName}" declares integer ids, but the instances ids list has a non-integer entry.`,
          });
        }
      } else if (sources[0] === "count") {
        const count = inst.count as number;
        if (!Number.isInteger(count) || count < 1) {
          issues.push({
            severity: "error",
            section: "behavior",
            field: `child_entity_types.${typeName}.instances`,
            message: `Child type "${typeName}" instances count must be a whole number of at least 1.`,
          });
        } else if (typeof idf.max === "number" && count > idf.max) {
          issues.push({
            severity: "error",
            section: "behavior",
            field: `child_entity_types.${typeName}.instances`,
            message: `Child type "${typeName}" instances count (${count}) exceeds id_format.max (${idf.max}).`,
          });
        }
        if (idf.type === "string") {
          issues.push({
            severity: "error",
            section: "behavior",
            field: `child_entity_types.${typeName}.instances`,
            message: `Child type "${typeName}" instances count requires integer ids (id_format.type is "string" — use an ID-list config field).`,
          });
        }
      } else {
        const fieldName = inst[sources[0]] as string;
        if (!fieldName || !configFields.has(fieldName)) {
          issues.push({
            severity: "error",
            section: "behavior",
            field: `child_entity_types.${typeName}.instances`,
            message: `Child type "${typeName}" instances reads config field "${fieldName || "(none)"}", which isn't declared in the driver's config.`,
          });
        }
        if (sources[0] === "count_from" && idf.type === "string") {
          issues.push({
            severity: "error",
            section: "behavior",
            field: `child_entity_types.${typeName}.instances`,
            message: `Child type "${typeName}" instances count field requires integer ids (id_format.type is "string" — use an ID-list config field).`,
          });
        }
      }
    }
  }

  // ── Config fields: defaults must be safe and typed. A secret field with a
  //    default exports the credential in plain text inside the shareable
  //    .avcdriver (the Config editor can't author one, but an imported or
  //    hand-edited file can carry one in). A default stored as the wrong
  //    primitive (e.g. "5" on an integer field) exports wrong-typed YAML that
  //    anything reading default_config directly trips over. ────────────────
  for (const [fieldName, rawDef] of Object.entries(draft.config_schema ?? {})) {
    if (!rawDef || typeof rawDef !== "object") continue;
    const fieldDef = rawDef as {
      type?: string;
      secret?: boolean;
      default?: unknown;
    };
    const configDefault = (draft.default_config ?? {})[fieldName];
    const hasValue = (v: unknown) => v !== undefined && v !== null && v !== "";
    if (fieldDef.secret === true) {
      if (hasValue(configDefault) || hasValue(fieldDef.default)) {
        issues.push({
          severity: "error",
          section: "connection",
          field: `config_schema.${fieldName}`,
          message: `Config field "${fieldName}" is secret but has a default value — remove the default. A secret default is exported in plain text inside the driver file.`,
        });
      }
      continue;
    }
    if (!hasValue(configDefault)) continue;
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

  // ── Top-level state variables: the runtime hard-requires a label on every
  //    one (driver_loader.py) and rejects an unknown type. A cleared label or
  //    bad type otherwise only surfaces as an unanchored save-time 422, so
  //    flag it inline in the Behavior tab where the editor lives. ───────────
  for (const [varName, varDef] of Object.entries(draft.state_variables ?? {})) {
    if (!varDef || typeof varDef !== "object") continue;
    if (!varDef.label?.trim()) {
      issues.push({
        severity: "error",
        section: "behavior",
        field: `state_variables.${varName}`,
        message: `State variable "${varName}" needs a label — the runtime rejects a state variable with no label.`,
      });
    }
    if (varDef.type && !STATE_VAR_TYPES.has(varDef.type)) {
      issues.push({
        severity: "error",
        section: "behavior",
        field: `state_variables.${varName}`,
        message: `State variable "${varName}" has unknown type "${varDef.type}" — use string, integer, number, boolean, enum, or float.`,
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

    // Wire value maps (mirror driver_loader.py): every row needs both a
    // value and a wire value — an empty row would silently never translate.
    for (const [paramName, paramDef] of Object.entries(cmd.params ?? {})) {
      if (paramDef.map === undefined) continue;
      const rows = Object.entries(paramDef.map ?? {});
      const rowsOk =
        rows.length > 0 &&
        rows.every(
          ([k, v]) =>
            k !== "" &&
            (typeof v === "string" || typeof v === "number") &&
            String(v) !== "",
        );
      if (!rowsOk) {
        issues.push({
          severity: "error",
          section: "behavior",
          command: cmdName,
          param: paramName,
          message: `Parameter "${paramName}" in command "${cmdName}" has a wire value map with empty rows — each row needs a value and what to send.`,
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

    // Every command needs SOME wire format — the runtime loader rejects a
    // command with no send/string, no path/method, and no address. For OSC
    // and HTTP transports the shape check below already reports the missing
    // address / method-path, so only the raw-transport case (a tcp/serial/udp
    // command whose send was left blank — the builder seeds `send: ""`) slips
    // through; flag it here so it doesn't save as a no-op that 422s at load.
    const cmdRecord = cmd as unknown as Record<string, unknown>;
    if (
      commandRoute(cmd) === "raw" &&
      draft.transport !== "osc" &&
      draft.transport !== "http" &&
      !hasContent(cmdRecord.send) &&
      !hasContent(cmdRecord.string)
    ) {
      issues.push({
        severity: "error",
        section: "behavior",
        command: cmdName,
        message: `Command "${cmdName}" has nothing to send — set the ${(draft.transport || "tcp").toUpperCase()} send string.`,
      });
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

    // Declared command semantics — mirror avcdriver_semantic.py. `sets` /
    // `query_for` drive the auto-generated simulator, so a dangling name
    // would silently declare nothing: every key must name a declared state
    // variable, and a "{param}" value must name a declared parameter. A
    // command addressed to exactly one child (a single child_id param whose
    // type is declared) may name that child type's state variables instead —
    // the effect applies to the addressed child.
    const childParamTypes = Object.values(cmd.params ?? {})
      .filter((p) => p && typeof p === "object" && p.type === "child_id")
      .map((p) => p.child_type);
    const singleChildType =
      childParamTypes.length === 1 ? childParamTypes[0] : undefined;
    const childTargetVars: Record<string, unknown> =
      singleChildType !== undefined && childTypeNames.has(singleChildType)
        ? (childTypes[singleChildType]?.state_variables ?? {})
        : {};
    const hasChildTargetVars = Object.keys(childTargetVars).length > 0;
    const declaredStateVars = draft.state_variables ?? {};
    const setsRaw = cmdRecord.sets;
    if (
      setsRaw != null &&
      (typeof setsRaw !== "object" || Array.isArray(setsRaw))
    ) {
      issues.push({
        severity: "error",
        section: "behavior",
        command: cmdName,
        message: `Command "${cmdName}": "sets" must be a mapping of state variable to value.`,
      });
    } else if (setsRaw != null) {
      for (const [varName, setValue] of Object.entries(
        setsRaw as Record<string, unknown>,
      )) {
        if (
          !(varName in declaredStateVars) &&
          !(varName in childTargetVars)
        ) {
          issues.push({
            severity: "error",
            section: "behavior",
            command: cmdName,
            message: hasChildTargetVars
              ? `Command "${cmdName}" sets "${varName}" which is not a declared state variable (device-level or of the addressed child type).`
              : `Command "${cmdName}" sets "${varName}" which is not a declared state variable.`,
          });
        }
        if (
          typeof setValue === "string" &&
          (setValue.includes("{") || setValue.includes("}"))
        ) {
          const ref = /^\{([^{}]+)\}$/.exec(setValue);
          if (!ref || !declaredParams.has(ref[1])) {
            issues.push({
              severity: "error",
              section: "behavior",
              command: cmdName,
              message: `Command "${cmdName}": sets.${varName} value "${setValue}" must be a literal or a bare {param} reference to a declared parameter.`,
            });
          }
        }
      }
    }
    const queryForRaw = cmdRecord.query_for;
    if (queryForRaw != null) {
      if (typeof queryForRaw !== "string" || !queryForRaw) {
        issues.push({
          severity: "error",
          section: "behavior",
          command: cmdName,
          message: `Command "${cmdName}": "query_for" must name a state variable.`,
        });
      } else if (
        !(queryForRaw in declaredStateVars) &&
        !(queryForRaw in childTargetVars)
      ) {
        issues.push({
          severity: "error",
          section: "behavior",
          command: cmdName,
          message: hasChildTargetVars
            ? `Command "${cmdName}": query_for "${queryForRaw}" is not a declared state variable (device-level or of the addressed child type).`
            : `Command "${cmdName}": query_for "${queryForRaw}" is not a declared state variable.`,
        });
      }
    }
  }

  // ── Device settings: write shapes follow the same routing rules ───────
  for (const [settingName, setting] of Object.entries(
    draft.device_settings ?? {},
  )) {
    const write = setting.write;
    if (!write || Object.keys(write).length === 0) {
      // The runtime loader hard-requires a write block on every device
      // setting (driver_loader.py: "a device setting must be writable") —
      // a missing or emptied write (e.g. after a transport switch scrubbed
      // its wire format) would 422 at load time.
      issues.push({
        severity: "error",
        section: "behavior",
        message: `Device setting "${settingName}": missing write block (send / path / address) - a device setting must be writable.`,
      });
      continue;
    }
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

  // ── Responses: mirror the runtime's structural rules (driver_loader.py).
  //    A response is OSC (an `address`) or text (a `pattern`/`match`); the
  //    builder's free-text fields let either go malformed, and nothing else
  //    in this validator looks at responses, so a bad one only shows as a
  //    save-time 422. An `address` response is OSC-only — flag one left on a
  //    non-OSC transport (it would never match), and require the '/'-rooted
  //    path the runtime demands. ────────────────────────────────────────────
  (draft.responses ?? []).forEach((resp, i) => {
    const label = `Response ${i + 1}`;
    const tn = (draft.transport || "tcp").toUpperCase();
    // Throttle (any response kind): the runtime rejects a zero/negative/
    // non-numeric value, since it would silently disable either the rule or
    // the throttle.
    if (
      resp.throttle !== undefined &&
      (typeof resp.throttle !== "number" || !(resp.throttle > 0))
    ) {
      issues.push({
        severity: "error",
        section: "behavior",
        message: `${label} throttle must be a positive number of seconds.`,
      });
    }
    // `require:` scopes a json rule to bodies carrying the named key(s) —
    // mirror avcdriver_semantic.py: it only applies to json: true rules, and
    // a blank entry would silently disable the rule (never matches).
    const isJsonRule = !!(resp as { json?: unknown }).json;
    const requireVal = (resp as { require?: unknown }).require;
    if (requireVal !== undefined && requireVal !== null) {
      if (!isJsonRule) {
        issues.push({
          severity: "error",
          section: "behavior",
          message: `${label}: "require" only applies to JSON body rules — remove it, or switch the rule to JSON body.`,
        });
      }
      if (typeof requireVal === "string") {
        if (!requireVal.trim()) {
          issues.push({
            severity: "error",
            section: "behavior",
            message: `${label}: "require" must name a JSON key.`,
          });
        }
      } else if (Array.isArray(requireVal)) {
        if (
          requireVal.length === 0 ||
          !requireVal.every((k) => typeof k === "string" && k.trim())
        ) {
          issues.push({
            severity: "error",
            section: "behavior",
            message: `${label}: "require" entries must be non-empty JSON key names.`,
          });
        }
      } else {
        issues.push({
          severity: "error",
          section: "behavior",
          message: `${label}: "require" must be a JSON key name or a list of them.`,
        });
      }
    }
    if (resp.address !== undefined) {
      if (draft.transport && draft.transport !== "osc") {
        issues.push({
          severity: "error",
          section: "behavior",
          message: `${label} has an OSC address but the driver transport is ${tn}. The runtime reads an address response as OSC, so it never matches on a non-OSC transport — remove the address or set the transport to OSC.`,
        });
      } else if (!resp.address.trim().startsWith("/")) {
        issues.push({
          severity: "error",
          section: "behavior",
          message: `${label} OSC address must start with "/" (e.g. /main/volume).`,
        });
      }
    } else if (isJsonRule) {
      // json-body rules parse the whole reply as JSON and map fields by
      // key/path — no regex pattern (mirror avcdriver_semantic.py). They
      // need a set map or mappings list to do anything.
      const setVal = (resp as { set?: unknown }).set;
      const hasSet =
        typeof setVal === "object" && setVal !== null && !Array.isArray(setVal);
      const mappingsVal = (resp as { mappings?: unknown }).mappings;
      const hasMappings = Array.isArray(mappingsVal);
      // The runtime dispatches OSC transports by address before json rules
      // are ever consulted (configurable.py handle_data), so a json rule on
      // an OSC driver never fires.
      if (draft.transport === "osc") {
        issues.push({
          severity: "error",
          section: "behavior",
          message: `${label} is a JSON body rule but the driver transport is OSC — the runtime reads OSC messages by address, so this rule never matches.`,
        });
      }
      if (!hasSet && !hasMappings) {
        issues.push({
          severity: "error",
          section: "behavior",
          message: `${label} is a JSON response but maps no fields — add a set map (state variable to JSON key/path) or a mappings list.`,
        });
      } else {
        // Row checks against what the runtime will actually read
        // (build_json_mappings: a non-empty mappings list wins, else set).
        // A dangling state name or blank field path makes the row silently
        // do nothing, and an unknown type coerces as plain string.
        const declaredVars = draft.state_variables ?? {};
        const checkJsonRow = (state: string, path: string, type: unknown) => {
          if (!state || !(state in declaredVars)) {
            issues.push({
              severity: "error",
              section: "behavior",
              message: `${label}: JSON field row sets "${state || "(none)"}", which isn't a declared state variable.`,
            });
          }
          if (!path.trim()) {
            issues.push({
              severity: "error",
              section: "behavior",
              message: `${label}: row for "${state || "(none)"}" reads no JSON field — add the field path (e.g. status.power).`,
            });
          }
          if (
            type !== undefined &&
            (typeof type !== "string" || !STATE_VAR_TYPES.has(type))
          ) {
            issues.push({
              severity: "error",
              section: "behavior",
              message: `${label}: row for "${state || "(none)"}" has unknown type "${String(type)}".`,
            });
          }
        };
        const mappingRows = hasMappings ? (mappingsVal as unknown[]) : [];
        const setEntries = hasSet
          ? Object.entries(setVal as Record<string, unknown>)
          : [];
        if (mappingRows.length > 0) {
          for (const m of mappingRows) {
            if (typeof m !== "object" || m === null) continue;
            const row = m as { state?: unknown; key?: unknown; type?: unknown };
            checkJsonRow(
              typeof row.state === "string" ? row.state : "",
              row.key == null ? "" : String(row.key),
              row.type,
            );
          }
        } else if (setEntries.length > 0) {
          for (const [state, spec] of setEntries) {
            if (
              spec !== null &&
              typeof spec === "object" &&
              !Array.isArray(spec)
            ) {
              const s = spec as { key?: unknown; path?: unknown; type?: unknown };
              // The runtime defaults a missing key/path to the state name.
              const path = s.key ?? s.path ?? state;
              checkJsonRow(state, String(path), s.type);
            } else {
              checkJsonRow(state, String(spec), undefined);
            }
          }
        } else {
          issues.push({
            severity: "error",
            section: "behavior",
            message: `${label} is a JSON response but maps no fields — add at least one row.`,
          });
        }
      }
    } else if (!resp.pattern?.trim() && !resp.match?.trim()) {
      issues.push({
        severity: "error",
        section: "behavior",
        message: `${label} has no pattern to match — add a match pattern, or an OSC address for an OSC driver.`,
      });
    }

    // child_set routing (mirror driver_loader.py): declared type, declared
    // props, in-range capture refs (regex) or address segments + positional
    // args (OSC); not on json responses.
    const childSet = resp.child_set;
    if (childSet !== undefined) {
      if (resp.address !== undefined) {
        // OSC form: id from {segment: N} or a literal; values from {arg: N}
        // or literals. No capture groups exist on an address match.
        if (!Array.isArray(childSet) || childSet.length === 0) {
          issues.push({
            severity: "error",
            section: "behavior",
            message: `${label}: child_set must contain at least one routing entry.`,
          });
          return;
        }
        const addrText = (resp.address ?? "").trim();
        const stripped = addrText.replace(/^\/+|\/+$/g, "");
        const nsegs = stripped ? stripped.split("/").length : null;
        childSet.forEach((entry, j) => {
          const eLabel = `routing entry ${j + 1}`;
          if (!entry.type || !childTypeNames.has(entry.type)) {
            issues.push({
              severity: "error",
              section: "behavior",
              message: `${label}: ${eLabel} routes to child type "${entry.type || "(none)"}", which isn't declared.`,
            });
            return;
          }
          if (entry.id === undefined || entry.id === null || entry.id === "") {
            issues.push({
              severity: "error",
              section: "behavior",
              message: `${label}: ${eLabel} needs an ID — an address segment like seg:1, or a literal child ID.`,
            });
          } else if (typeof entry.id === "object") {
            const spec = entry.id as { segment?: unknown; map?: unknown };
            const seg = spec.segment;
            if (typeof seg !== "number" || !Number.isInteger(seg) || seg < 0) {
              issues.push({
                severity: "error",
                section: "behavior",
                message: `${label}: ${eLabel} ID needs an address segment index (seg:1 = the second /-separated part; OSC rules have no capture groups).`,
              });
            } else if (nsegs !== null && seg >= nsegs) {
              issues.push({
                severity: "error",
                section: "behavior",
                message: `${label}: ${eLabel} ID segment ${seg} is past the end of the address (${nsegs} segment${nsegs === 1 ? "" : "s"}).`,
              });
            }
            if (spec.map !== undefined) {
              const entriesOk =
                typeof spec.map === "object" &&
                spec.map !== null &&
                Object.keys(spec.map as object).length > 0 &&
                Object.entries(spec.map as Record<string, unknown>).every(
                  ([k, v]) =>
                    k !== "" &&
                    (typeof v === "string" || typeof v === "number") &&
                    String(v) !== "",
                );
              if (!entriesOk) {
                issues.push({
                  severity: "error",
                  section: "behavior",
                  message: `${label}: ${eLabel} wire-ID map rows must each have a wire ID and a child ID.`,
                });
              } else if (
                (childTypes[entry.type]?.id_format?.type ?? "integer") ===
                "integer"
              ) {
                for (const v of Object.values(
                  spec.map as Record<string, string | number>,
                )) {
                  if (!/^\d+$/.test(String(v).trim())) {
                    issues.push({
                      severity: "error",
                      section: "behavior",
                      message: `${label}: ${eLabel} wire-ID map value "${v}" isn't an integer, but child type "${entry.type}" uses integer IDs.`,
                    });
                    break;
                  }
                }
              }
            }
          } else if (typeof entry.id === "string" && entry.id.startsWith("$")) {
            issues.push({
              severity: "error",
              section: "behavior",
              message: `${label}: ${eLabel} ID "${entry.id}" — OSC rules have no capture groups; use an address segment (seg:1) or a literal.`,
            });
          }
          const props = new Set(
            Object.keys(childTypes[entry.type]?.state_variables ?? {}),
          );
          const stateMap = entry.state ?? {};
          if (Object.keys(stateMap).length === 0) {
            issues.push({
              severity: "error",
              section: "behavior",
              message: `${label}: ${eLabel} maps no properties — add at least one.`,
            });
          }
          for (const [prop, expr] of Object.entries(stateMap)) {
            if (!props.has(prop)) {
              issues.push({
                severity: "error",
                section: "behavior",
                message: `${label}: ${eLabel} maps "${prop}", which isn't a declared field of child type "${entry.type}".`,
              });
            }
            if (typeof expr === "string" && expr.startsWith("$")) {
              issues.push({
                severity: "error",
                section: "behavior",
                message: `${label}: ${eLabel} value for "${prop}" — OSC rules have no capture groups; use a positional argument (arg:0) or a literal.`,
              });
            } else if (typeof expr === "object" && expr !== null) {
              const pe = expr as { arg?: unknown; value?: unknown };
              if (pe.arg === undefined && pe.value === undefined) {
                issues.push({
                  severity: "error",
                  section: "behavior",
                  message: `${label}: ${eLabel} value for "${prop}" needs a positional argument (arg:0) or a literal value.`,
                });
              } else if (
                pe.arg !== undefined &&
                (typeof pe.arg !== "number" ||
                  !Number.isInteger(pe.arg) ||
                  pe.arg < 0)
              ) {
                issues.push({
                  severity: "error",
                  section: "behavior",
                  message: `${label}: ${eLabel} value for "${prop}" arg must be a whole number of 0 or more.`,
                });
              }
            }
          }
        });
        return;
      }
      if (isJsonRule) {
        issues.push({
          severity: "error",
          section: "behavior",
          message: `${label}: child entity routing isn't supported on JSON responses.`,
        });
        return;
      }
      if (!Array.isArray(childSet) || childSet.length === 0) {
        issues.push({
          severity: "error",
          section: "behavior",
          message: `${label}: child_set must contain at least one routing entry.`,
        });
        return;
      }
      // Count the pattern's capture groups when it compiles cleanly (it may
      // contain {config} placeholders substituted at runtime — skip then).
      const patternText = resp.pattern ?? resp.match ?? "";
      let ngroups: number | null = null;
      try {
        ngroups = new RegExp(patternText + "|").exec("")!.length - 1;
      } catch {
        ngroups = null;
      }
      const checkRef = (where: string, ref: string) => {
        const group = parseInt(ref.slice(1), 10);
        if (!Number.isInteger(group) || group < 1) {
          issues.push({
            severity: "error",
            section: "behavior",
            message: `${label}: ${where} capture reference "${ref}" must be $1, $2, ...`,
          });
        } else if (ngroups !== null && group > ngroups) {
          issues.push({
            severity: "error",
            section: "behavior",
            message: `${label}: ${where} capture reference $${group} exceeds the pattern's ${ngroups} capture group${ngroups === 1 ? "" : "s"}.`,
          });
        }
      };
      childSet.forEach((entry, j) => {
        const eLabel = `routing entry ${j + 1}`;
        if (!entry.type || !childTypeNames.has(entry.type)) {
          issues.push({
            severity: "error",
            section: "behavior",
            message: `${label}: ${eLabel} routes to child type "${entry.type || "(none)"}", which isn't declared.`,
          });
          return;
        }
        if (entry.id === undefined || entry.id === null || entry.id === "") {
          issues.push({
            severity: "error",
            section: "behavior",
            message: `${label}: ${eLabel} needs an ID — a capture reference like $1, or a literal child ID.`,
          });
        } else if (typeof entry.id === "object") {
          // Long form {group, map}: wire-ID translation on a capture ref.
          const spec = entry.id as { group?: unknown; map?: unknown };
          const gref = spec.group;
          if (typeof gref === "number" && Number.isInteger(gref)) {
            checkRef(`${eLabel} ID`, `$${gref}`);
          } else if (typeof gref === "string" && gref.startsWith("$")) {
            checkRef(`${eLabel} ID`, gref);
          } else {
            issues.push({
              severity: "error",
              section: "behavior",
              message: `${label}: ${eLabel} wire-ID map needs a capture group (which capture holds the wire ID).`,
            });
          }
          if (spec.map !== undefined) {
            const entriesOk =
              typeof spec.map === "object" &&
              spec.map !== null &&
              Object.keys(spec.map as object).length > 0 &&
              Object.entries(spec.map as Record<string, unknown>).every(
                ([k, v]) =>
                  k !== "" &&
                  (typeof v === "string" || typeof v === "number") &&
                  String(v) !== "",
              );
            if (!entriesOk) {
              issues.push({
                severity: "error",
                section: "behavior",
                message: `${label}: ${eLabel} wire-ID map rows must each have a wire ID and a child ID.`,
              });
            } else if (
              (childTypes[entry.type]?.id_format?.type ?? "integer") ===
              "integer"
            ) {
              for (const v of Object.values(
                spec.map as Record<string, string | number>,
              )) {
                if (!/^\d+$/.test(String(v).trim())) {
                  issues.push({
                    severity: "error",
                    section: "behavior",
                    message: `${label}: ${eLabel} wire-ID map value "${v}" isn't an integer, but child type "${entry.type}" uses integer IDs.`,
                  });
                  break;
                }
              }
            }
          }
        } else if (typeof entry.id === "string" && entry.id.startsWith("$")) {
          checkRef(`${eLabel} ID`, entry.id);
        }
        const props = new Set(
          Object.keys(childTypes[entry.type]?.state_variables ?? {}),
        );
        const stateMap = entry.state ?? {};
        if (Object.keys(stateMap).length === 0) {
          issues.push({
            severity: "error",
            section: "behavior",
            message: `${label}: ${eLabel} maps no properties — add at least one.`,
          });
        }
        for (const [prop, expr] of Object.entries(stateMap)) {
          if (!props.has(prop)) {
            issues.push({
              severity: "error",
              section: "behavior",
              message: `${label}: ${eLabel} maps "${prop}", which isn't a declared field of child type "${entry.type}".`,
            });
          }
          if (typeof expr === "string" && expr.startsWith("$")) {
            checkRef(`${eLabel} value for "${prop}"`, expr);
          }
        }
      });
    }
  });

  // ── Per-child query templates (each_child) in polling.queries/on_connect:
  //    mirror driver_loader.py so a bad entry shows here, not as a 422. ────
  const checkEachChildEntries = (
    fieldName: string,
    entries: unknown[] | undefined,
    allowOscDict: boolean,
  ) => {
    (entries ?? []).forEach((q, i) => {
      if (typeof q !== "object" || q === null) return;
      const entry = q as Record<string, unknown>;
      // `when: <config_field>` gates the entry on a truthy config value. A
      // field name that doesn't exist would disable the entry forever, so a
      // typo is an error, not a quiet no-op.
      if ("when" in entry) {
        const when = entry.when;
        if (typeof when !== "string" || !when) {
          issues.push({
            severity: "error",
            section: "behavior",
            message: `${fieldName} entry ${i + 1}: "when" must name a config field.`,
          });
        } else if (!gateFieldNames.has(when)) {
          issues.push({
            severity: "error",
            section: "behavior",
            message: `${fieldName} entry ${i + 1}: "when" field "${when}" isn't a declared config field, so this entry would never run.`,
          });
        }
      }
      if (!("each_child" in entry)) {
        if (allowOscDict && "address" in entry) return;
        if (typeof entry.send === "string" && entry.send) return;
        issues.push({
          severity: "error",
          section: "behavior",
          message: `${fieldName} entry ${i + 1}: unrecognized entry — expected a query string or a per-child template.`,
        });
        return;
      }
      const ctype = entry.each_child;
      if (typeof ctype !== "string" || !childTypeNames.has(ctype)) {
        issues.push({
          severity: "error",
          section: "behavior",
          message: `${fieldName} entry ${i + 1}: per-child type "${String(ctype)}" isn't a declared child type.`,
        });
      } else if (!childTypes[ctype]?.instances) {
        issues.push({
          severity: "error",
          section: "behavior",
          message: `${fieldName} entry ${i + 1}: child type "${ctype}" has no Instances rule, so a per-child query would never send anything.`,
        });
      }
      const send = entry.send;
      if (typeof send !== "string" || !send) {
        issues.push({
          severity: "error",
          section: "behavior",
          message: `${fieldName} entry ${i + 1}: per-child query needs a send template.`,
        });
      } else if (!/\{child_id(?::[^{}]*)?\}/.test(send)) {
        issues.push({
          severity: "error",
          section: "behavior",
          message: `${fieldName} entry ${i + 1}: the send template must contain {child_id} so each child gets its own query (a format spec like {child_id:02d} works too).`,
        });
      }
      // `query_for` on a per-child entry names one of that child type's own
      // state variables — each child answers the query from its own state.
      if ("query_for" in entry && typeof ctype === "string" && childTypeNames.has(ctype)) {
        const qf = entry.query_for;
        const childVars = childTypes[ctype]?.state_variables ?? {};
        if (typeof qf !== "string" || !qf || !(qf in childVars)) {
          issues.push({
            severity: "error",
            section: "behavior",
            message: `${fieldName} entry ${i + 1}: "Reports" must name a state variable of child type "${ctype}".`,
          });
        }
      }
    });
  };
  checkEachChildEntries("Poll query", draft.polling?.queries, false);
  checkEachChildEntries("on_connect", draft.on_connect, true);

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
    if (draft.transport && !AUTH_TRANSPORTS.has(draft.transport)) {
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

  // ── Push notifications + connection watchdog — mirror driver_loader.py's
  //    push:/liveness: rules so a misdeclared block shows in the Connection
  //    tab at author time. At runtime a bad push block silently never
  //    delivers a frame, and a bad liveness block either never arms or tears
  //    healthy devices down. ────────────────────────────────────────────────
  // ── Actions, quick actions, and web_ui — mirror avcdriver_semantic.py's
  //    validate_actions so a bad promotion shows in the Behavior tab at
  //    author time instead of as a save-time 422. ─────────────────────────
  validateActions(draft, issues);

  validatePush(draft, issues);
  validateLiveness(draft, issues);

  // ── Frame parser (binary protocols) — mirror driver_loader.py's
  //    validate_driver_definition so a bad header_size/length shows in the
  //    Connection tab at author time, not as a ValueError raised in connect()
  //    that wedges the device in a permanent reconnect loop. ───────────────
  validateFrameParser(draft, issues);
  validateSendFrame(draft, issues);

  // ── Discovery hints (mirror server/discovery/hints.py rules so the user
  //    sees them here, not as an opaque 422 at save) ──────────────────────
  validateDiscovery(draft, issues);

  return issues;
}

/** Mirror server/transport/multicast_listener.py's is_multicast_group: an
 *  IPv4 literal in 224.0.0.0/4. */
function isMulticastGroup(value: string): boolean {
  const parts = value.split(".");
  if (parts.length !== 4) return false;
  const octets = parts.map((p) =>
    /^\d{1,3}$/.test(p) ? parseInt(p, 10) : NaN,
  );
  if (octets.some((o) => Number.isNaN(o) || o > 255)) return false;
  return octets[0] >= 224 && octets[0] <= 239;
}

/** Mirror server/drivers/avcdriver_semantic.py's validate_actions: the
 *  `actions` + `quick_actions` promotion blocks and the `web_ui` flag. A bad
 *  entry either 422s at save (loader-validated) or renders a button that
 *  404s on click, so every rule surfaces here first. Link URLs and a
 *  web_ui template get the same {placeholder} coverage check commands get —
 *  the runtime substitutes {host}/{port}/{config_key} and leaves unknown
 *  tokens verbatim in the opened URL. */
function validateActions(
  draft: DriverDefinition,
  issues: ValidationIssue[],
): void {
  const commandIds = new Set(Object.keys(draft.commands ?? {}));

  // Keys a link URL / web_ui template may substitute: declared config fields
  // plus the runtime-injected connection keys (host, port, ...).
  const urlKeys = new Set([
    ...Object.keys(draft.config_schema ?? {}),
    ...Object.keys(draft.default_config ?? {}),
    ...BASELINE_CONFIG_KEYS,
  ]);
  const checkUrlTemplate = (where: string, field: string, template: string) => {
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
        section: "behavior",
        field,
        message: `${where} references {${token}}, but only {host}, {port}, and declared config fields are substituted — the placeholder would be left in the opened URL.`,
      });
    }
  };

  // web_ui: boolean, or a URL template string.
  if (typeof draft.web_ui === "string" && draft.web_ui) {
    checkUrlTemplate("The web interface URL template", "web_ui", draft.web_ui);
  }

  // quick_actions: legacy sugar — every entry must name a declared command.
  const quick = draft.quick_actions;
  if (quick !== undefined) {
    if (!Array.isArray(quick)) {
      issues.push({
        severity: "error",
        section: "behavior",
        field: "quick_actions",
        message: "Quick actions must be a list of command ids.",
      });
    } else {
      quick.forEach((cid, i) => {
        if (typeof cid !== "string" || !cid) {
          issues.push({
            severity: "error",
            section: "behavior",
            field: "quick_actions",
            message: `Quick action ${i + 1} must be a non-empty command id.`,
          });
        } else if (commandIds.size > 0 && !commandIds.has(cid)) {
          issues.push({
            severity: "error",
            section: "behavior",
            field: "quick_actions",
            message: `Quick action "${cid}" is not a declared command.`,
          });
        }
      });
    }
  }

  const actions = draft.actions;
  if (actions === undefined) return;
  if (!Array.isArray(actions)) {
    issues.push({
      severity: "error",
      section: "behavior",
      field: "actions",
      message: "Actions must be a list.",
    });
    return;
  }

  const seenIds = new Set<string>();
  actions.forEach((entry, i) => {
    const where = `Action ${i + 1}`;
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      issues.push({
        severity: "error",
        section: "behavior",
        field: "actions",
        message: `${where} must be a mapping with an id.`,
      });
      return;
    }
    const a = entry as unknown as Record<string, unknown>;
    const id = a.id;
    let label = where;
    if (typeof id !== "string" || !id) {
      issues.push({
        severity: "error",
        section: "behavior",
        field: "actions",
        message: `${where} needs an id.`,
      });
    } else {
      label = `Action "${id}"`;
      if (seenIds.has(id)) {
        issues.push({
          severity: "error",
          section: "behavior",
          field: "actions",
          message: `${label} duplicates another action's id — ids must be unique.`,
        });
      }
      seenIds.add(id);
    }

    const kind = a.kind ?? "command";
    if (!(ACTION_KINDS_YAML as readonly unknown[]).includes(kind)) {
      issues.push({
        severity: "error",
        section: "behavior",
        field: "actions",
        message: `${label} has unknown kind "${String(kind)}" — use "command" or "link" (setup actions need a Python driver).`,
      });
    }

    const availability = a.availability;
    if (
      availability !== undefined &&
      !(ACTION_AVAILABILITIES as readonly unknown[]).includes(availability)
    ) {
      issues.push({
        severity: "error",
        section: "behavior",
        field: "actions",
        message: `${label} availability must be online, offline, or always.`,
      });
    }

    const url = a.url;
    if (kind === "link") {
      if (url !== undefined && (typeof url !== "string" || !url)) {
        issues.push({
          severity: "error",
          section: "behavior",
          field: "actions",
          message: `${label} URL must be a non-empty string (leave it out to open https://{host}).`,
        });
      } else if (typeof url === "string") {
        checkUrlTemplate(`${label}'s URL`, "actions", url);
      }
    } else if (url !== undefined) {
      issues.push({
        severity: "error",
        section: "behavior",
        field: "actions",
        message: `${label} has a URL, but only a link action opens one — set the kind to "link" or remove the URL.`,
      });
    }

    validateVisibleWhen(label, a.visible_when, issues);

    // A kind:"command" action must resolve to a declared command — the
    // explicit command field, or the action id itself. (Skipped when no
    // commands are declared yet, matching the backend.)
    if (kind === "command" && typeof id === "string" && id) {
      const command = a.command;
      if (command !== undefined && typeof command !== "string") {
        issues.push({
          severity: "error",
          section: "behavior",
          field: "actions",
          message: `${label} command must be a command id string.`,
        });
      } else {
        const target = (command as string | undefined) || id;
        if (commandIds.size > 0 && !commandIds.has(target)) {
          issues.push({
            severity: "error",
            section: "behavior",
            field: "actions",
            message: `${label} promotes command "${target}", which isn't a declared command.`,
          });
        }
      }
    }
  });
}

/** Mirror avcdriver_semantic.py's _validate_visible_when: a single
 *  {key, operator, value} condition or an {any|all: [...]} group.
 *  Light-touch — unknown extra keys are tolerated. */
function validateVisibleWhen(
  label: string,
  vw: unknown,
  issues: ValidationIssue[],
): void {
  if (vw === undefined || vw === null) return;
  if (typeof vw !== "object" || Array.isArray(vw)) {
    issues.push({
      severity: "error",
      section: "behavior",
      field: "actions",
      message: `${label} visibility condition must be a mapping.`,
    });
    return;
  }
  const rec = vw as Record<string, unknown>;

  const checkCondition = (cwhere: string, cond: unknown) => {
    if (!cond || typeof cond !== "object" || Array.isArray(cond)) {
      issues.push({
        severity: "error",
        section: "behavior",
        field: "actions",
        message: `${cwhere} must be a mapping with a state key.`,
      });
      return;
    }
    const c = cond as Record<string, unknown>;
    if (typeof c.key !== "string" || !c.key) {
      issues.push({
        severity: "error",
        section: "behavior",
        field: "actions",
        message: `${cwhere} needs a state key to compare.`,
      });
    }
    const op = c.operator ?? "eq";
    if (typeof op !== "string" || !VISIBLE_WHEN_OPERATORS.has(op)) {
      issues.push({
        severity: "error",
        section: "behavior",
        field: "actions",
        message: `${cwhere} has unknown operator "${String(op)}".`,
      });
    }
  };

  if ("any" in rec || "all" in rec) {
    for (const groupKey of ["any", "all"] as const) {
      if (!(groupKey in rec)) continue;
      const group = rec[groupKey];
      if (!Array.isArray(group) || group.length === 0) {
        issues.push({
          severity: "error",
          section: "behavior",
          field: "actions",
          message: `${label} visibility "${groupKey}" group must list at least one condition.`,
        });
        continue;
      }
      group.forEach((cond, j) =>
        checkCondition(`${label} visibility condition ${j + 1}`, cond),
      );
    }
  } else {
    checkCondition(`${label} visibility condition`, rec);
  }
}

/** Push channel types the runtime knows about but hasn't implemented — kept
 *  distinct from plain typos so the message says "not yet" rather than
 *  "never". Mirror driver_loader.py. */
// Every declared shape is implemented — nothing is reserved-but-unbuilt
// today. Kept (empty) so a future shape can be named here with a
// "not yet" message instead of reading as a typo. Mirror driver_loader.py.
const RESERVED_PUSH_TYPES: ReadonlySet<string> = new Set<string>();

/** Mirror server/drivers/driver_loader.py's push: load-time checks. Values
 *  accept {config_field} templates, and a template may only name a field
 *  declared in config_schema or default_config — an undeclared field
 *  resolves to nothing at runtime and the channel never opens. */
function validatePush(
  draft: DriverDefinition,
  issues: ValidationIssue[],
): void {
  const push = draft.push;
  if (!push) return;

  const declaredFields = new Set([
    ...Object.keys(draft.config_schema ?? {}),
    ...Object.keys(draft.default_config ?? {}),
  ]);

  const type = push.type ?? "";
  if (RESERVED_PUSH_TYPES.has(type)) {
    issues.push({
      severity: "error",
      section: "connection",
      field: "push.type",
      message: `Push type "${type}" isn't supported yet — only "multicast", "sse", "tcp_listener", and "http_listener".`,
    });
  } else if (!(type in PUSH_KEYS_BY_TYPE)) {
    issues.push({
      severity: "error",
      section: "connection",
      field: "push.type",
      message: `Push type must be "multicast", "sse", "tcp_listener", or "http_listener".`,
    });
  }

  const knownKeys =
    PUSH_KEYS_BY_TYPE[type] ??
    new Set([
      "type",
      "group",
      "port",
      "path",
      "idle_timeout",
      "frame_parser",
      "register",
      "unregister",
    ]);
  const unknownKeys = Object.keys(push).filter(
    (k) => !knownKeys.has(k) && (push as Record<string, unknown>)[k] !== undefined,
  );
  if (unknownKeys.length > 0) {
    issues.push({
      severity: "error",
      section: "connection",
      field: "push",
      message: `Push has key(s) that don't apply to type "${type}": ${unknownKeys.join(", ")} — allowed: ${[...knownKeys].join(", ")}.`,
    });
  }

  // A {config_field} template must name declared config fields; braces with
  // no token would pass through to the wire verbatim.
  const checkTemplate = (where: string, value: string) => {
    const fields = [...value.matchAll(/\{(\w+)\}/g)].map((m) => m[1]);
    if (fields.length === 0) {
      issues.push({
        severity: "error",
        section: "connection",
        field: `push.${where}`,
        message: `Push ${where} "${value}" has braces but no {config_field} token.`,
      });
    }
    for (const f of fields) {
      if (!declaredFields.has(f)) {
        issues.push({
          severity: "error",
          section: "connection",
          field: `push.${where}`,
          message: `Push ${where} references config field "${f}", which isn't declared in the driver's config.`,
        });
      }
    }
  };

  if (type === "multicast") {
    const group = push.group;
    if (group === undefined || group === "") {
      issues.push({
        severity: "error",
        section: "connection",
        field: "push.group",
        message:
          "Push needs a multicast group — a literal address like 239.0.0.100, or a {config_field} template.",
      });
    } else if (group.includes("{")) {
      checkTemplate("group", group);
    } else if (!isMulticastGroup(group)) {
      issues.push({
        severity: "error",
        section: "connection",
        field: "push.group",
        message: `Push group "${group}" must be an IPv4 multicast address (224.0.0.0 – 239.255.255.255) or a {config_field} template.`,
      });
    }

    const port = push.port;
    if (port === undefined || port === "") {
      issues.push({
        severity: "error",
        section: "connection",
        field: "push.port",
        message:
          "Push needs a port — a number 1-65535, or a {config_field} template.",
      });
    } else if (typeof port === "string" && port.includes("{")) {
      checkTemplate("port", port);
    } else if (
      typeof port !== "number" ||
      !Number.isInteger(port) ||
      port < 1 ||
      port > 65535
    ) {
      issues.push({
        severity: "error",
        section: "connection",
        field: "push.port",
        message:
          "Push port must be a whole number between 1 and 65535, or a {config_field} template.",
      });
    }
  }

  if (type === "sse") {
    // SSE rides the driver's HTTP session — it is a streaming mode of the
    // control transport, not a separate listener.
    if (draft.transport && draft.transport !== "http") {
      issues.push({
        severity: "error",
        section: "connection",
        field: "push.type",
        message: `SSE push requires the HTTP transport (this driver uses "${draft.transport}").`,
      });
    }

    const rawPath = push.path;
    const paths =
      typeof rawPath === "string"
        ? rawPath === ""
          ? []
          : [rawPath]
        : Array.isArray(rawPath)
          ? rawPath
          : [];
    if (paths.length === 0) {
      issues.push({
        severity: "error",
        section: "connection",
        field: "push.path",
        message:
          "Push needs an event-stream path — a URL path on the device like /v2/configuration/system/status (one per line for multiple streams).",
      });
    }
    for (const p of paths) {
      if (typeof p !== "string" || p.trim() === "") {
        issues.push({
          severity: "error",
          section: "connection",
          field: "push.path",
          message: "Every event-stream path must be a non-empty string.",
        });
      } else if (p.includes("{")) {
        checkTemplate("path", p);
      } else if (!p.startsWith("/")) {
        issues.push({
          severity: "error",
          section: "connection",
          field: "push.path",
          message: `Event-stream path "${p}" must start with "/" (a URL path on the device) or be a {config_field} template.`,
        });
      }
    }

    const idle = push.idle_timeout;
    if (
      idle !== undefined &&
      (typeof idle !== "number" || !Number.isFinite(idle) || idle <= 0)
    ) {
      issues.push({
        severity: "error",
        section: "connection",
        field: "push.idle_timeout",
        message: "Idle timeout must be a positive number of seconds.",
      });
    }
  }

  if (type === "tcp_listener") {
    const port = push.port;
    if (port === undefined || port === "") {
      issues.push({
        severity: "error",
        section: "connection",
        field: "push.port",
        message:
          "Push needs a listener port — a number 0-65535 (0 = automatic), or a {config_field} template.",
      });
    } else if (typeof port === "string" && port.includes("{")) {
      checkTemplate("port", port);
    } else if (
      typeof port !== "number" ||
      !Number.isInteger(port) ||
      port < 0 ||
      port > 65535
    ) {
      issues.push({
        severity: "error",
        section: "connection",
        field: "push.port",
        message:
          "Listener port must be a whole number between 0 and 65535 (0 = automatic), or a {config_field} template.",
      });
    }

    const frame = push.frame_parser;
    if (frame !== undefined && frame !== null) {
      const ftype = frame.type ?? "";
      if (!PUSH_FRAME_PARSER_TYPES.has(ftype)) {
        issues.push({
          severity: "error",
          section: "connection",
          field: "push.frame_parser",
          message: `Notification framing type "${ftype}" must be struct_frame, length_prefix, or fixed_length.`,
        });
      } else if (ftype === "struct_frame") {
        for (const fkey of [
          "header_reserve",
          "mid_reserve",
          "trailer_reserve",
        ]) {
          const fval = (frame as Record<string, unknown>)[fkey] ?? 0;
          if (
            typeof fval !== "number" ||
            !Number.isInteger(fval) ||
            fval < 0
          ) {
            issues.push({
              severity: "error",
              section: "connection",
              field: `push.frame_parser.${fkey}`,
              message: `Frame ${fkey.replace("_", " ")} must be a non-negative whole number of bytes.`,
            });
          }
        }
        const fsize = (frame as Record<string, unknown>).length_size ?? 2;
        if (typeof fsize !== "number" || !STRUCT_LENGTH_SIZES.has(fsize)) {
          issues.push({
            severity: "error",
            section: "connection",
            field: "push.frame_parser.length_size",
            message: "Frame length size must be 1, 2, or 4 bytes.",
          });
        }
        const fadj = (frame as Record<string, unknown>).length_adjust ?? 0;
        if (typeof fadj !== "number" || !Number.isInteger(fadj)) {
          issues.push({
            severity: "error",
            section: "connection",
            field: "push.frame_parser.length_adjust",
            message: "Frame length adjust must be a whole number.",
          });
        }
        const fend = (frame as Record<string, unknown>).length_endian ?? "big";
        if (!(LENGTH_ENDIANS as readonly unknown[]).includes(fend)) {
          issues.push({
            severity: "error",
            section: "connection",
            field: "push.frame_parser.length_endian",
            message: 'Frame length byte order must be "big" or "little".',
          });
        }
      }
    }

    // register / unregister must name declared commands — a typo would
    // silently never arm the device.
    const commandNames = new Set(Object.keys(draft.commands ?? {}));
    for (const ckey of ["register", "unregister"] as const) {
      const cval = push[ckey];
      if (cval === undefined) continue;
      if (typeof cval !== "string" || cval.trim() === "") {
        issues.push({
          severity: "error",
          section: "connection",
          field: `push.${ckey}`,
          message: `Push ${ckey} must be a command name.`,
        });
      } else if (!commandNames.has(cval)) {
        issues.push({
          severity: "error",
          section: "connection",
          field: `push.${ckey}`,
          message: `Push ${ckey} command "${cval}" is not declared in this driver's commands.`,
        });
      }
    }
  }
}

/** Mirror server/drivers/driver_loader.py's liveness: load-time checks — a
 *  misdeclared watchdog would silently never arm (the exact never-goes-
 *  offline failure it exists to fix) or tear healthy devices down. */
function validateLiveness(
  draft: DriverDefinition,
  issues: ValidationIssue[],
): void {
  const liveness = draft.liveness;
  if (!liveness) return;

  if (draft.transport && !LIVENESS_TRANSPORTS.has(draft.transport)) {
    issues.push({
      severity: "error",
      section: "connection",
      field: "liveness",
      message: `Connection watchdog only works on TCP, serial, UDP, or OSC transports, not ${draft.transport}. Disable it or change the transport.`,
    });
  }

  if (!liveness.send) {
    issues.push({
      severity: "error",
      section: "connection",
      field: "liveness.send",
      message:
        "Connection watchdog needs a probe command to send — without one the watchdog never arms.",
    });
  }

  if (liveness.expect !== undefined) {
    if (!liveness.expect) {
      issues.push({
        severity: "error",
        section: "connection",
        field: "liveness.expect",
        message:
          "Connection watchdog expect pattern can't be empty — remove it to count any inbound frame as a reply.",
      });
    } else {
      try {
        new RegExp(liveness.expect);
      } catch {
        issues.push({
          severity: "error",
          section: "connection",
          field: "liveness.expect",
          message: `Connection watchdog expect pattern "${liveness.expect}" isn't a valid regular expression.`,
        });
      }
    }
  }

  if (
    liveness.interval !== undefined &&
    (typeof liveness.interval !== "number" || liveness.interval < 1)
  ) {
    issues.push({
      severity: "error",
      section: "connection",
      field: "liveness.interval",
      message: "Connection watchdog interval must be at least 1 second.",
    });
  }
  if (
    liveness.timeout !== undefined &&
    (typeof liveness.timeout !== "number" || liveness.timeout < 0.1)
  ) {
    issues.push({
      severity: "error",
      section: "connection",
      field: "liveness.timeout",
      message: "Connection watchdog reply timeout must be at least 0.1 seconds.",
    });
  }
  if (
    liveness.max_failures !== undefined &&
    (!Number.isInteger(liveness.max_failures) || liveness.max_failures < 1)
  ) {
    issues.push({
      severity: "error",
      section: "connection",
      field: "liveness.max_failures",
      message:
        "Connection watchdog max failures must be a whole number of at least 1.",
    });
  }

  // The OSC-only args list has no editor surface (it round-trips as loaded),
  // but an imported file can still carry a bad one.
  if (liveness.args !== undefined) {
    if (draft.transport !== "osc") {
      issues.push({
        severity: "error",
        section: "connection",
        field: "liveness.args",
        message:
          "Connection watchdog args are only valid on the OSC transport.",
      });
    } else if (!Array.isArray(liveness.args)) {
      issues.push({
        severity: "error",
        section: "connection",
        field: "liveness.args",
        message: "Connection watchdog args must be a list.",
      });
    }
  }
}

/** Mirror server/drivers/driver_loader.py's frame_parser load-time checks.
 *  The Frame Parser editor's own inputs constrain new drivers, but an
 *  imported or hand-edited .avcdriver can still carry a header_size the
 *  LengthPrefixFrameParser rejects (only 1/2/4) or a non-positive fixed
 *  length the FixedLengthFrameParser rejects — both raise at connect, so
 *  surface them as Connection errors before save. */
function validateFrameParser(
  draft: DriverDefinition,
  issues: ValidationIssue[],
): void {
  const fp = draft.frame_parser;
  if (!fp) return;
  const type = fp.type;
  if (type === "length_prefix") {
    const headerSize = (fp.header_size as number | undefined) ?? 2;
    if (!FRAME_HEADER_SIZES.has(headerSize)) {
      issues.push({
        severity: "error",
        section: "connection",
        field: "frame_parser.header_size",
        message: `Frame parser header size must be 1, 2, or 4 bytes (got ${String(headerSize)}). The device would fail to connect.`,
      });
    }
    const offset = fp.header_offset;
    if (offset !== undefined && !Number.isInteger(offset)) {
      issues.push({
        severity: "error",
        section: "connection",
        field: "frame_parser.header_offset",
        message: `Frame parser header offset must be a whole number (got ${String(offset)}).`,
      });
    }
    for (const key of ["length_offset", "header_extra"] as const) {
      const v = fp[key] as number | undefined;
      if (v !== undefined && (!Number.isInteger(v) || v < 0)) {
        issues.push({
          severity: "error",
          section: "connection",
          field: `frame_parser.${key}`,
          message: `Frame parser ${key} must be a non-negative whole number (got ${String(v)}).`,
        });
      }
    }
    const endian = fp.length_endian as string | undefined;
    if (
      endian !== undefined &&
      !(LENGTH_ENDIANS as readonly unknown[]).includes(endian)
    ) {
      issues.push({
        severity: "error",
        section: "connection",
        field: "frame_parser.length_endian",
        message: `Frame parser length byte order must be "big" or "little" (got ${String(endian)}).`,
      });
    }
  } else if (type === "fixed_length") {
    const length = (fp.length as number | undefined) ?? 1;
    if (!Number.isInteger(length) || length <= 0) {
      issues.push({
        severity: "error",
        section: "connection",
        field: "frame_parser.length",
        message: `Frame parser frame length must be a positive whole number (got ${String(length)}). The device would fail to connect.`,
      });
    }
  } else if (type) {
    issues.push({
      severity: "error",
      section: "connection",
      field: "frame_parser.type",
      message: `Frame parser type "${String(type)}" isn't supported — use length-prefix or fixed-length.`,
    });
  } else {
    issues.push({
      severity: "error",
      section: "connection",
      field: "frame_parser.type",
      message: "Frame parser is enabled but has no type set.",
    });
  }
}

/** Mirror server/drivers/driver_loader.py's send_frame load-time checks — the
 *  send twin of the frame_parser validation above. */
function validateSendFrame(
  draft: DriverDefinition,
  issues: ValidationIssue[],
): void {
  const sf = draft.send_frame;
  if (!sf) return;
  const type = sf.type ?? "length_prefix";
  if (type !== "length_prefix") {
    issues.push({
      severity: "error",
      section: "connection",
      field: "send_frame.type",
      message: `Send frame type "${String(type)}" isn't supported — use length-prefix.`,
    });
    return;
  }
  const lengthSize = (sf.length_size as number | undefined) ?? 4;
  if (!Number.isInteger(lengthSize) || lengthSize < 1) {
    issues.push({
      severity: "error",
      section: "connection",
      field: "send_frame.length_size",
      message: `Send frame length field size must be a positive whole number (got ${String(lengthSize)}).`,
    });
  }
  const endian = sf.length_endian as string | undefined;
  if (
    endian !== undefined &&
    !(LENGTH_ENDIANS as readonly unknown[]).includes(endian)
  ) {
    issues.push({
      severity: "error",
      section: "connection",
      field: "send_frame.length_endian",
      message: `Send frame length byte order must be "big" or "little" (got ${String(endian)}).`,
    });
  }
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
