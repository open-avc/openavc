// GENERATED FILE - DO NOT EDIT.
// Rendered from the driver-contract registry (server/drivers/spec.py).
// Regenerate with:  python -m server.drivers.contract_gen
// A test compares this file against a fresh render, so hand edits fail CI.

// --- contract constant tables ---

/** Transports a YAML driver may declare. */
export const YAML_TRANSPORTS = ["tcp", "serial", "udp", "http", "osc", "bridge"] as const;

/** Transports that need a Python driver (no YAML surface). */
export const PYTHON_ONLY_TRANSPORTS = ["ssh", "mqtt"] as const;

/** Values allowed in the transports: interchangeable list. */
export const INTERCHANGEABLE_TRANSPORTS = ["tcp", "serial", "udp", "http", "osc"] as const;

/** Port kinds a bridge driver may advertise. */
export const BRIDGE_PORT_KINDS = ["serial", "ir", "relay"] as const;

/**
 * The catalog's driver categories (the Builder's labeled list in
 * driverCategories.ts must cover exactly these).
 */
export const DRIVER_CATEGORY_IDS = ["projector", "display", "switcher", "audio", "camera", "video", "streaming", "lighting", "power", "utility"] as const;

/** compatible_models confidence levels. */
export const CONFIDENCE_LEVELS = ["full", "partial", "untested"] as const;

/** Value types for state variables, child state variables, and device settings. */
export const VALUE_TYPES = ["string", "integer", "number", "boolean", "enum", "float"] as const;

/** Set form of VALUE_TYPES for membership checks. */
export const STATE_VAR_TYPES: ReadonlySet<string> = new Set(["boolean", "enum", "float", "integer", "number", "string"]);

/** Types a command/action parameter may declare. */
export const PARAM_TYPES = ["string", "integer", "number", "boolean", "enum", "child_id"] as const;

/** Types a config_schema field may declare. */
export const CONFIG_FIELD_TYPES = ["string", "text", "integer", "number", "float", "boolean", "enum", "table"] as const;

/** cloud_priority forwarding tiers. */
export const CLOUD_PRIORITIES = ["low", "high"] as const;

/** Action kinds a YAML driver may declare (setup needs a Python driver). */
export const ACTION_KINDS_YAML = ["command", "link"] as const;

/** When an action button shows, relative to connection state. */
export const ACTION_AVAILABILITIES = ["online", "offline", "always"] as const;

/** Operators a visible_when condition accepts. */
export const VISIBLE_WHEN_OPERATORS: ReadonlySet<string> = new Set(["!=", "<", "<=", "==", ">", ">=", "eq", "equals", "falsy", "gt", "gte", "lt", "lte", "ne", "not_equals", "truthy"]);

/** OSC argument type tags the runtime can encode. */
export const OSC_ARG_TYPES = ["f", "i", "s", "h", "d", "T", "F", "N"] as const;

/** Transports the auth: login handshake supports. */
export const AUTH_TRANSPORTS: ReadonlySet<string> = new Set(["serial", "tcp"]);

/** Auth handshake types the runtime implements. */
export const AUTH_TYPES = ["telnet_login"] as const;

/** Transports the liveness: watchdog supports. */
export const LIVENESS_TRANSPORTS: ReadonlySet<string> = new Set(["osc", "serial", "tcp", "udp"]);

/** Frame parsers a push: tcp_listener subscription may declare. */
export const PUSH_FRAME_PARSER_TYPES: ReadonlySet<string> = new Set(["fixed_length", "length_prefix", "struct_frame"]);

/** Frame parser types valid on the top-level frame_parser block. */
export const FRAME_PARSER_TYPES = ["length_prefix", "fixed_length"] as const;

/** Legal length_prefix header sizes. */
export const FRAME_HEADER_SIZES: ReadonlySet<number> = new Set([1, 2, 4]);

/** Legal struct_frame length-field sizes. */
export const STRUCT_LENGTH_SIZES: ReadonlySet<number> = new Set([1, 2, 4]);

/** Byte orders a length field may declare. */
export const LENGTH_ENDIANS = ["big", "little"] as const;

/** Send-side frame types. */
export const SEND_FRAME_TYPES = ["length_prefix"] as const;

/** child id_format.type values. */
export const CHILD_ID_TYPES = ["integer", "string"] as const;

/** The mutually exclusive child-roster sources. */
export const INSTANCE_SOURCES = ["count", "count_from", "ids_from", "ids"] as const;

/**
 * Ports a discovery port_open hint may not use (they match every web/SSH
 * host).
 */
export const DISALLOWED_OPEN_PORTS: ReadonlySet<number> = new Set([22, 80, 443, 8000, 8080, 8443, 8888]);

/** The keys each push type accepts (unknown keys are rejected at load). */
export const PUSH_KEYS_BY_TYPE: Readonly<Record<string, ReadonlySet<string>>> = {
  multicast: new Set(["group", "port", "type"]),
  sse: new Set(["idle_timeout", "path", "type"]),
  tcp_listener: new Set(["frame_parser", "port", "register", "type", "unregister"]),
  http_listener: new Set(["type"]),
};

// --- driver definition types ---

/**
 * One enum option: a bare wire value, or a {value, label} pair where the label
 * is shown in pickers (and read in macros) while the value goes on the wire. A
 * plain scalar means value == label.
 */
export type EnumOption = string | { value: string; label?: string };

/**
 * The long-form child_set id: which capture group (regex rules) or address
 * segment (OSC rules) holds the wire id, with an optional wire-id -> local-id
 * translation map.
 */
export type DriverChildSetIdSpec = { group?: number | string; segment?: number; map?: Record<string, string | number> };

/**
 * An extract: rule — a static literal, or a regex capture against the probe
 * response.
 */
export type DriverDiscoveryExtractRule = string | { regex: string; group?: number };

/**
 * Show the action only when a state condition holds. A single {key, operator,
 * value} condition, or an {any:[...]} (OR) / {all:[...]} (AND) group. key may
 * use $id for the device's own id.
 */
export type DriverVisibleWhen = DriverVisibleWhenCondition | { any: DriverVisibleWhenCondition[] } | { all: DriverVisibleWhenCondition[] };

/** One typed OSC argument (type tag + value; see OSC_ARG_TYPES). */
export interface DriverOscArg {
  /**
   * OSC type tag: f=float32, i=int32, s=string, h=int64, d=float64, T=true,
   * F=false, N=nil.
   */
  type: string;
  value: string;
}

/** Cascade: source this param's options from a sibling param's chosen value. */
export interface ParamOptionsFrom {
  /** The sibling param whose value selects the option set. */
  param: string;
  /**
   * child_schema: offer the controls of the child picked in the sibling
   * child_id param.
   */
  source: "child_schema";
}

/**
 * Make this param's input type follow the control chosen in a sibling cascade.
 * The named param is itself an options_from child_schema cascade; this param
 * then renders as that control's type (number+range, Yes/No, etc.).
 */
export interface ParamTypeFrom {
  /**
   * The sibling options_from(child_schema) param whose chosen control supplies
   * this param's type/min/max.
   */
  param: string;
}

export interface DriverParamDef {
  type: string;
  required?: boolean;
  label?: string;
  help?: string;
  /**
   * Allowed values for an enum param. Each entry is a bare wire value or a
   * {value, label} pair (label shown in the picker, value sent on the wire).
   * The runtime accepts either the label or the value from any caller and
   * normalizes to the value.
   */
  values?: EnumOption[];
  /** For child_id type: the child_entity_types name this parameter targets. */
  child_type?: string;
  /**
   * Make this param a picker sourced from a device-relative state key. The IDE
   * reads device.<id>.<options_state> (a JSON-encoded list of strings or
   * {value,label} objects) and offers it as a dropdown. The driver publishes
   * the enumerable set as a state variable.
   */
  options_state?: string;
  /**
   * Like options_state but an absolute state key, read verbatim (same
   * primitive plugins use). Use options_state for per-device lists.
   */
  options_source?: string;
  /** Cascade: source this param's options from a sibling param's chosen value. */
  options_from?: { param: string; source: "child_schema" };
  /**
   * Make this param's input type follow the control chosen in a sibling
   * cascade. The named param is itself an options_from child_schema cascade;
   * this param then renders as that control's type (number+range, Yes/No,
   * etc.).
   */
  type_from?: { param: string };
  /**
   * Minimum for an integer/number param. Enforced by the runtime at command
   * time; the IDE also flags violations while authoring.
   */
  min?: number;
  /**
   * Maximum for an integer/number param. Enforced by the runtime at command
   * time; the IDE also flags violations while authoring.
   */
  max?: number;
  /**
   * For a number param: round the value to this many decimal places on the
   * wire (0 = whole number). An integer param always coerces to a whole
   * number, so decimals is not needed there. For fixed-width or hex output,
   * use a format spec on the placeholder instead (e.g. {level:03d},
   * {addr:02X}).
   */
  decimals?: number;
  /**
   * Regex a free-text value must fully match — a shape check for values that
   * can't be enumerated (IP, hostname, fixed-length ID). The runtime validates
   * it at command time; the IDE shows an inline error while authoring. Must
   * compile and avoid catastrophic backtracking.
   */
  pattern?: string;
  /**
   * For string params. Default true: leading/trailing whitespace is trimmed
   * before the value goes on the wire. Set false to pass the value through
   * verbatim — for raw payloads where edge whitespace is meaningful (typed
   * text, verbatim titles, relay bodies whose trailing terminator is part of
   * the protocol). Requires platform 0.22.0.
   */
  trim?: boolean;
  default?: unknown;
  /**
   * Wire-value translation applied after validation, before substitution: the
   * validated value (string-keyed) is replaced by the mapped wire value.
   * Values not in the map pass through unchanged. Most useful on child_id
   * params whose local ids differ from the protocol's channel numbers.
   */
  map?: Record<string, string | number>;
  /** Accepted alias for help — read either, write help. */
  description?: string;
}

/**
 * A command must declare one of: send (TCP/serial/UDP), path/method (HTTP), or
 * address (OSC).
 */
export interface DriverCommandDef {
  label: string;
  help?: string;
  /**
   * Raw bytes to send (TCP/serial/UDP). {param} and {config} placeholders
   * substituted at runtime.
   */
  send: string;
  /**
   * Send this command's send string exactly as written, skipping the driver's
   * command_prefix / command_suffix framing. Use it for the odd command that
   * doesn't share the common frame. Requires platform 0.23.0.
   */
  raw?: boolean;
  /** HTTP method (GET/POST/PUT/DELETE). Default GET. */
  method?: string;
  /** HTTP URL path. */
  path?: string;
  /** HTTP request body. Parsed as JSON when valid, otherwise sent raw. */
  body?: string;
  /** HTTP query parameters with {param} substitution. */
  query_params?: Record<string, string>;
  /** Per-request HTTP headers with {param} substitution. */
  headers?: Record<string, string>;
  /** OSC address pattern. */
  address?: string;
  /** OSC typed arguments. */
  args?: DriverOscArg[];
  /** Parameter definitions, keyed by {placeholder} name. */
  params: Record<string, DriverParamDef>;
  /**
   * Declared state effect: the state variables this command sets on the
   * device, e.g. {power: true} or {master_volume: "{level}"}. A "{param}"
   * value takes that command parameter's value; anything else is a literal.
   * The auto-generated simulator applies these instead of guessing from the
   * command name; keys must name declared state variables. On a command with
   * exactly one child_id parameter, a key may instead name a state variable of
   * that parameter's child type — the effect then applies to the addressed
   * child. Requires platform 0.24.0.
   */
  sets?: Record<string, string | number | boolean>;
  /**
   * Declares this command as a status query: the device answers it by
   * reporting the named state variable. The auto-generated simulator replies
   * with that variable's current value instead of inferring one from the
   * command name. Must name a declared state variable; on a command with
   * exactly one child_id parameter it may instead name a state variable of
   * that parameter's child type. Requires platform 0.24.0.
   */
  query_for?: string;
}

export interface DriverResponseMapping {
  /** Regex capture group index (1-based; 0 is the whole match). */
  group: number;
  /** State variable to update. */
  state: string;
  /** Coercion applied to the captured value. */
  type?: string;
  /** Lookup table translating raw captured values to friendly values. */
  map?: Record<string, string>;
  value?: unknown;
  /** OSC argument index (for OSC responses). */
  arg?: number;
  /**
   * Optional. The matched value is treated as a JSON string: it is parsed and
   * this dot-separated path is walked (object keys and integer list indices,
   * e.g. "data" or "data.name" or "data.0") to the value used before
   * mapping/coercion. A path landing on an array or object yields its length
   * (so a boolean type becomes "is non-empty?" and an integer type becomes the
   * count). Omit for today's positional/raw behavior. Common for OSC devices
   * whose replies carry the value inside a JSON string (e.g. QLab's /reply ...
   * {"data": ...}).
   */
  json_path?: string;
}

/**
 * Routes a matched response into one child's state. Regex rules: id is a
 * capture ref ($1), a literal, or {group, map}; state values are capture refs
 * or literals. OSC rules (platform 0.23.0+): id is {segment: N} (0-based index
 * into the /-split address) or a literal; state values are {arg: N}
 * positional-argument specs or literals. Values coerce by the child property's
 * declared type.
 */
export interface DriverChildSetEntry {
  /** A declared child_entity_types name. */
  type: string;
  /**
   * A capture ref ($1, regex rules), {segment: N} (OSC rules), a literal child
   * id, or the map long form to translate a wire id (0-based channels, ST
   * codes) to the local child id.
   */
  id: string | number | DriverChildSetIdSpec;
  /**
   * Child property -> capture ref or literal (regex rules); {arg: N[, map,
   * type]}, {value: ...}, or literal (OSC rules).
   */
  state: Record<string, unknown>;
}

/**
 * A response must declare match (regex), address (OSC), or json: true (JSON-
 * body).
 */
export interface DriverResponseDef {
  /**
   * When true, the whole reply body is parsed as a JSON object and every
   * set/mappings key is read from it. Unlike regex responses, all json rules
   * are applied to a body (not just the first match), so one JSON reply can
   * populate many state variables. In this mode a set value is the JSON field
   * to read (a string key, dot path allowed) or a {key, type, map} object, not
   * a capture ref.
   */
  json?: boolean;
  /** Regex matched against incoming text. Capture groups extract values. */
  match?: string;
  /** OSC address pattern (must start with /). Supports fnmatch wildcards. */
  address?: string;
  /**
   * Shorthand mapping state variables to values. For regex responses, values
   * are capture groups ("$1") or static values. For a json: true response,
   * values are JSON field names (dot path allowed) or {key, type, map} specs.
   */
  set?: Record<string, unknown>;
  /** Verbose mapping form, supporting type coercion and value maps. */
  mappings?: DriverResponseMapping[];
  /**
   * Route a matched response into child-entity state. Works on regex responses
   * (captures) and OSC address rules (address segments + positional args;
   * platform 0.23.0+) — not json: true. May coexist with set/mappings on the
   * same entry.
   */
  child_set?: DriverChildSetEntry[];
  /**
   * Optional. After this rule matches and applies, further matches of the same
   * rule are dropped for this many seconds (drop-style; each skipped frame is
   * superseded by the next). For continuous push telemetry like audio level
   * meters — do not throttle ordinary replies or state-change notices. Works
   * on regex, json, and OSC rules. Requires platform 0.23.0.
   */
  throttle?: number;
  /**
   * json: true rules only. Apply this rule only to bodies carrying the named
   * JSON key (or every key in the list). Scopes a rule when different
   * endpoints on one device reuse a field name with different meanings.
   * Requires platform 0.23.0.
   */
  require?: string | string[];
}

/**
 * Per-child query template: expands to one query per registered child of the
 * named type, substituting {child_id} with the unpadded local ID.
 */
export interface DriverEachChildQuery {
  /** A declared child_entity_types name (must have an instances: roster). */
  each_child: string;
  /**
   * Query template; must contain {child_id} (a format spec like {child_id:02d}
   * zero-pads — requires platform 0.23.0).
   */
  send: string;
  /**
   * Config field gating this entry: it runs only while that field is truthy.
   * Must name a field declared in config_schema / default_config. Requires
   * platform 0.23.0.
   */
  when?: string;
  /**
   * State variable each reply reports, from the child type's state_variables.
   * Lets the auto-generated simulator answer the query from that child's own
   * state instead of leaving it unmodeled. Requires platform 0.24.0.
   */
  query_for?: string;
}

/**
 * A plain query in mapping form so it can carry extra semantics a bare string
 * cannot: when: gates it on a config field (arm a chatty subscription behind
 * an integrator checkbox), and query_for: names the state variable the reply
 * reports so the auto-generated simulator answers it without name-guessing. At
 * least one of the two must be present — a mapping with only send: is just a
 * string query written the long way.
 */
export interface DriverQueryEntry {
  /**
   * Query sent as authored — a raw protocol string on tcp/serial, a command
   * name or path on http/udp.
   */
  send: string;
  /**
   * Config field gating this entry. Must name a field declared in
   * config_schema / default_config. Requires platform 0.23.0.
   */
  when?: string;
  /**
   * State variable the device reports in answer to this query. Must name a
   * declared state variable. Requires platform 0.24.0.
   */
  query_for?: string;
}

/**
 * An OSC on_connect item that carries typed arguments — a bring-up message
 * that isn't a bare subscription address. when: gates it on a config field
 * like the other entry shapes.
 */
export interface DriverOscConnectItem {
  address: string;
  args?: DriverOscArg[];
  /**
   * Config field gating this entry: it runs only while that field is truthy.
   * Must name a field declared in config_schema / default_config. Requires
   * platform 0.23.0.
   */
  when?: string;
}

export interface DriverVisibleWhenCondition {
  /** State key compared against. May contain $id (replaced with the device id). */
  key: string;
  /** Comparison operator. Default eq. */
  operator?: "eq" | "ne" | "gt" | "lt" | "gte" | "lte" | "truthy" | "falsy" | "equals" | "not_equals" | "==" | "!=" | ">" | "<" | ">=" | "<=";
  /** Value to compare against (not needed for truthy/falsy). */
  value?: unknown;
}

/**
 * A promoted action. kind:"command" must resolve to a declared command (the
 * command field, or the id).
 */
export interface DriverActionDef {
  /** Unique action id within the driver. */
  id: string;
  /**
   * 'command' promotes a declared command (runs online via send_command).
   * 'link' opens a URL (e.g. the device's web interface) in a new tab, client-
   * side. Offline-capable 'setup' provisioning wizards require a Python driver
   * with a run_setup_action() handler.
   */
  kind?: "command" | "link";
  /** Button label. Defaults to the promoted command's label, else the id. */
  label?: string;
  /** lucide icon name (kebab-case), e.g. power, shield, search, radar. */
  icon?: string;
  /**
   * Confirm before running. true for a generic prompt, or a custom message
   * string.
   */
  confirm?: boolean | string;
  /**
   * When the button shows. online (default) hides while offline; offline shows
   * only while offline; always ignores connection state.
   */
  availability?: "online" | "offline" | "always";
  /** kind:"command" only — the command id to send. Defaults to the action id. */
  command?: string;
  /**
   * kind:"link" only — the URL to open. Supports {host}/{port}/{config_key}
   * substitution from the device config. Defaults to https://{host}.
   */
  url?: string;
  /**
   * Input dialog fields. For kind:"command", defaults to the promoted
   * command's params.
   */
  params?: Record<string, DriverParamDef>;
  visible_when?: DriverVisibleWhen;
}

export interface DriverDiscoveryMdnsFingerprint {
  service: string;
  txt?: Record<string, string>;
  cross_vendor?: boolean;
}

export interface DriverDiscoverySsdpFingerprint {
  device_type: string;
  cross_vendor?: boolean;
  model?: string;
  manufacturer?: string;
  friendly_name?: string;
}

export interface DriverDiscoveryAmxDdpFingerprint {
  make: string;
  model_pattern?: string;
  cross_vendor?: boolean;
}

/**
 * A tcp_probe / udp_probe declaration. tls and cert_subject apply to TCP
 * probes only; UDP probes must declare a send payload and exactly one matcher.
 */
export interface DriverDiscoveryProbe {
  port: number;
  send_hex?: string;
  send_ascii?: string;
  expect?: string;
  expect_regex?: string;
  expect_hex?: string;
  cross_vendor?: boolean;
  /**
   * Wrap the connection in TLS (no cert verification) before send/read, for an
   * HTTPS-only device. Default false. tcp_probe only.
   */
  tls?: boolean;
  /**
   * Regex matched against the peer TLS certificate's subject (RFC4514 string +
   * SAN DNS names) to identify a device by its self-signed cert's own name,
   * e.g. 'CN=DM-NVX-'. Requires tls: true. A probe with only cert_subject (no
   * send/expect) matches on the cert alone; `extract` rules also run against
   * the subject. Platform >= 0.24.0.
   */
  cert_subject?: string;
  timeout_ms?: number;
  extract?: Record<string, DriverDiscoveryExtractRule>;
  extract_manufacturer?: string;
}

export interface DriverDiscoveryPython {
  file: string;
  cross_vendor?: boolean;
}

/**
 * Discovery fingerprints and hints. Unknown keys are rejected by the platform
 * and the catalog validator.
 */
export interface DriverDiscoveryConfig {
  /**
   * Minimum platform version whose discovery parser understands this block.
   * Normally stamped by scripts/build_index.py at catalog emission (e.g. SSDP
   * description filters need 0.23.0); platforms older than this skip the block
   * cleanly. Rarely hand-authored.
   */
  requires?: string;
  mdns?: Array<string | DriverDiscoveryMdnsFingerprint>;
  ssdp?: Array<string | DriverDiscoverySsdpFingerprint>;
  amx_ddp?: DriverDiscoveryAmxDdpFingerprint[];
  tcp_probe?: DriverDiscoveryProbe;
  udp_probe?: DriverDiscoveryProbe;
  python?: string | DriverDiscoveryPython;
  /** MAC OUI prefixes (vendor) used as a soft hint. */
  oui?: string[];
  /** Hostname regex patterns used as a soft hint. */
  hostname?: string[];
  /** Open-port hints. Generic admin/web/SSH ports are disallowed. */
  port_open?: number[];
  /** Vendor-string aliases matched against discovered banners/TXT records. */
  manufacturer_alias?: string[];
  /** SNMP private enterprise number. */
  snmp_pen?: number;
}

export interface DriverDeviceSettingWrite {
  /** TCP/serial write string. {value} and config placeholders substituted. */
  send?: string;
  /** HTTP method. Default POST. */
  method?: string;
  path?: string;
  body?: string;
  headers?: Record<string, string>;
  /** OSC address. */
  address?: string;
  args?: DriverOscArg[];
}

/**
 * type, label, help, and default are expected by the IDE; the runtime enforces
 * only the write definition.
 */
export interface DriverDeviceSettingDef {
  type: string;
  label: string;
  help?: string;
  /** State variable providing the current value. Defaults to the setting key. */
  state_key?: string;
  default?: unknown;
  /**
   * Prompt for this setting when adding the device to a project. Default
   * false.
   */
  setup?: boolean;
  /** Generate a non-clashing default (appends device ID). Default false. */
  unique?: boolean;
  /**
   * Allowed values for an enum setting. Each entry is a bare wire value or a
   * {value, label} pair (label shown in the editor, value written to the
   * device). A write that resolves to nothing in the set is rejected.
   */
  values?: EnumOption[];
  min?: number;
  max?: number;
  regex?: string;
  write?: DriverDeviceSettingWrite;
}

export interface DriverSimulatorDef {
  /**
   * Push state changes to connected drivers, matching real device feedback
   * behavior.
   */
  push_state?: boolean;
  /** Override initial state values. */
  initial_state?: Record<string, unknown>;
  /** Response delays in seconds, e.g. {command_response: 0.05}. */
  delays?: Record<string, number>;
  /** Named error behaviors selectable in the simulator UI. */
  error_modes?: Record<string, { behavior?: string; description?: string; set_state?: Record<string, unknown> }>;
  /** Declarative control widgets for the simulator UI. */
  controls?: Array<Record<string, unknown>>;
  state_machines?: Record<string, Record<string, unknown>>;
  command_handlers?: Array<Record<string, unknown>>;
  /** Unsolicited messages emitted on state change, keyed by state variable. */
  notifications?: Record<string, unknown>;
}

export interface DriverStateVarDef {
  type: string;
  /** Human-readable label. Required for top-level state variables. */
  label: string;
  help?: string;
  /** Allowed values for enum type. */
  values?: string[];
  min?: number;
  max?: number;
  /**
   * Value resolution for numeric types (e.g. 0.5 for a half-dB fader). Fills a
   * matched control's Step in the UI Builder.
   */
  step?: number;
  /**
   * Unit for numeric values (e.g. dB, Hz, %). Fills a matched control's Unit
   * in the UI Builder; without it the UI falls back to parsing a trailing
   * "(dB)" from the label.
   */
  unit?: string;
  /**
   * Marks a variable an integrator would bind a panel control to (a fader
   * level, a mute). The UI Builder's value picker lists flagged variables
   * first. Ordering only — unflagged variables stay pickable.
   */
  control?: boolean;
  default?: unknown;
  cloud_priority?: "low" | "high";
}

/**
 * Child state variable. Same shape as device state variables, but label is not
 * required (the platform injects online and label automatically).
 */
export interface DriverChildStateVarDef {
  type: string;
  label?: string;
  help?: string;
  values?: string[];
  min?: number;
  max?: number;
  /**
   * Value resolution for numeric types (e.g. 0.5 for a half-dB fader). Fills a
   * matched control's Step in the UI Builder.
   */
  step?: number;
  /**
   * Unit for numeric values (e.g. dB, Hz, %). Fills a matched control's Unit
   * in the UI Builder's range-match prompt.
   */
  unit?: string;
  /**
   * Marks a settable control (not a read-only mirror or metadata). The UI
   * Builder's value picker and the options_from: child_schema command cascade
   * list flagged fields first.
   */
  control?: boolean;
  default?: unknown;
  cloud_priority?: "low" | "high";
}

export interface DriverChildIdFormat {
  /**
   * Local child id type. min/max/pad_width apply to integer ids; string ids
   * pair with an instances ids_from roster (letter-addressed matrix outputs)
   * or a literal ids list (main buses st/m; platform 0.23.0+).
   */
  type: "integer" | "string";
  min?: number;
  max?: number;
  pad_width?: number;
  /**
   * String ids only: maximum id length the runtime accepts at register_child
   * time. Default 128.
   */
  max_length?: number;
}

/**
 * Declarative roster — makes the child type real at runtime for YAML drivers.
 * Exactly one of count (fixed IDs 1..N), count_from (an integer config field),
 * ids_from (a comma-separated config field; sparse IDs), or ids (a literal
 * fixed list; requires platform 0.23.0). An optional count_from_state names a
 * device-reported state var (e.g. num_outputs) the roster follows once
 * connected, with the chosen config source as the offline fallback. Children
 * register on connect, reconcile as a want-set (also whenever count_from_state
 * changes), and back the per-device Refresh from Device button.
 */
export interface DriverChildInstances {
  /** Fixed roster: registers integer IDs 1..count. */
  count?: number;
  /**
   * Name of an integer config field (config_schema / default_config) holding
   * the count — lets one driver cover different frame sizes.
   */
  count_from?: string;
  /**
   * Name of a device-reported integer state variable (e.g. num_outputs) the
   * roster follows once the device reports it, auto-sizing the roster to the
   * hardware. Optional companion to count_from, which stays the offline
   * fallback used before the device answers (a non-positive/absent value falls
   * back to count_from). Requires platform 0.23.0.
   */
  count_from_state?: string;
  /**
   * Name of a comma-separated config field listing the IDs (e.g. "1,2,4") —
   * for sparse or installer-chosen rosters.
   */
  ids_from?: string;
  /**
   * Literal fixed roster (e.g. [st, m]) — for protocol-fixed string or sparse-
   * integer IDs that no config field should have to carry. Requires platform
   * 0.23.0.
   */
  ids?: (string | number)[];
  /**
   * Initial label template; {id} substitutes the local ID. A user-set project
   * label always wins.
   */
  label?: string;
}

export interface DriverChildEntityType {
  label?: string;
  label_plural?: string;
  id_format: DriverChildIdFormat;
  state_variables: Record<string, DriverChildStateVarDef>;
  summary_fields?: string[];
  label_field?: string;
  instances?: DriverChildInstances;
}

/**
 * Help text shown in the Add Device dialog and available to the AI assistant.
 * overview + setup are required; connection is optional.
 */
export interface DriverHelpDef {
  overview?: string;
  setup?: string;
  /**
   * Optional short troubleshooting hint shown on the device's offline banner
   * when it can't connect (e.g. a remote-access setting that must be enabled
   * on the device first).
   */
  connection?: string;
}

export interface DriverCompatibleModelsEntry {
  manufacturer: string;
  models: string[];
  confidence: "full" | "partial" | "untested";
  notes?: string | null;
}

/**
 * Telnet-style login handshake. Only valid on tcp/serial transports (it reads
 * a raw byte stream); declaring it on udp/http/osc is rejected at load time.
 * username_prompt and password_prompt are required. All four regexes are
 * checked for catastrophic backtracking since they run on raw pre-auth device
 * bytes.
 */
export interface DriverAuthDef {
  /** Handshake type. Only telnet_login is implemented and accepted. */
  type?: string;
  /** Regex matched against the device's username prompt. Required. */
  username_prompt?: string;
  /** Regex matched against the device's password prompt. Required. */
  password_prompt?: string;
  /** Optional regex indicating successful login. */
  success_pattern?: string;
  /** Optional regex indicating rejected login. */
  failure_pattern?: string;
  /** Config field holding the username. Default "username". */
  username_field?: string;
  /** Config field holding the password. Default "password". */
  password_field?: string;
  /** Skip the handshake when the username config is empty. Default true. */
  skip_if_empty?: boolean;
  /** Per-stage handshake timeout. Default 10. */
  timeout_seconds?: number;
  /** Line ending appended to credentials. Default "\r\n". */
  line_ending?: string;
}

/**
 * Device-initiated push notifications arriving on a channel the platform opens
 * (not the established control connection). type: multicast joins the device's
 * notification group; incoming datagrams feed the driver's responses rules
 * (split on the driver delimiter first) and are accepted only from the
 * device's own address. type: sse holds GET path(s) open on the driver's own
 * HTTP session with Accept: text/event-stream; each event's data block feeds
 * the responses rules whole (pair with json: true rules for JSON payloads).
 * type: tcp_listener opens a local TCP port the device dials back to after a
 * registration command carrying {listener_port} tells it where; frames are
 * parsed by the declared frame_parser, split on the driver delimiter, and
 * accepted only from the device's own address. In every shape the subscription
 * starts before on_connect (and any register command) runs, and stops on
 * disconnect; a dropped SSE stream reconnects with exponential backoff. type:
 * http_listener accepts the device's own HTTP POSTs (webhooks) on a callback
 * path the platform assigns per device — send the URL to the device from an
 * on_connect registration command, where the token {push_callback_url}
 * substitutes it into command bodies, paths, and headers; request bodies feed
 * the responses rules whole and are accepted only from the device's own
 * address. Requires platform 0.23.0.
 */
export interface DriverPushDef {
  /**
   * Push channel kind. multicast = UDP group listen; sse = Server-Sent Events
   * stream on the HTTP transport; tcp_listener = local port the device dials
   * back to; http_listener = the device POSTs to a callback URL OpenAVC
   * assigns (no other keys; use {push_callback_url} in the registration
   * command).
   */
  type?: string;
  /**
   * multicast only: IPv4 multicast group (224.0.0.0 - 239.255.255.255) as a
   * literal, or a {config_field} template naming a declared
   * config_schema/default_config field (use a template when the device's
   * notification target is user-configurable).
   */
  group?: string;
  /**
   * multicast: UDP port (1-65535) as an integer literal, or a {config_field}
   * template string. tcp_listener: local inbound TCP port (0-65535, 0 = OS-
   * assigned) or a {config_field} template.
   */
  port?: number | string;
  /**
   * sse only: event-stream URL path on the device (literal starting with /, or
   * a {config_field} template) - or a list of paths for devices that stream
   * each resource separately.
   */
  path?: string | string[];
  /**
   * sse only, optional: seconds of stream silence (keepalives included) before
   * the connection is presumed dead and reopened. Set above the device's
   * keepalive interval; omit to wait indefinitely.
   */
  idle_timeout?: number;
  /**
   * tcp_listener only, optional: framing for the pushed frames (struct_frame /
   * length_prefix / fixed_length) - the dial-back channel is its own byte
   * stream, independent of the control transport's framing. Omit to dispatch
   * raw reads.
   */
  frame_parser?: { type: string; [key: string]: unknown } | null;
  /**
   * tcp_listener only, optional: name of the command that registers the dial-
   * back target with the device (reference {listener_port} in its path/send
   * string). Runs after the listener opens, and again on every reconnect.
   */
  register?: string;
  /**
   * tcp_listener only, optional: name of the command that cancels the
   * registration. Runs best-effort on graceful disconnect, freeing the
   * device's receiver slot.
   */
  unregister?: string;
}

/**
 * Dead-link watchdog: send a probe every `interval` seconds and await a reply;
 * after `max_failures` consecutive silent probes the platform drops the
 * connection with a typed no_response fault and reconnects. Only valid on
 * tcp/serial/udp/osc transports (rejected at load time on http, where polling
 * already awaits every response, and on bridge, which owns no transport). Any
 * inbound data during the wait window counts as alive unless `expect` narrows
 * it. Needed for connectionless transports (UDP/OSC, where fire-and-forget
 * polls never notice silence) and push-mostly TCP (no FIN when the device
 * vanishes).
 */
export interface DriverLivenessDef {
  /**
   * Probe payload. Same conventions as polling queries: a raw protocol string
   * with escape processing and {config} substitution (terminator included) for
   * tcp/serial/udp, or an OSC address on osc.
   */
  send?: string;
  /**
   * Optional regex; only inbound data matching it satisfies the probe. Without
   * it, any inbound data counts. Checked for catastrophic backtracking.
   */
  expect?: string;
  /** Seconds between probes. Default 30. */
  interval?: number;
  /** Seconds to await a qualifying reply. Default 5. */
  timeout?: number;
  /** Consecutive misses before the connection is dropped. Default 2. */
  max_failures?: number;
  /**
   * OSC only: arguments sent with the probe address (same shape as command
   * args).
   */
  args?: unknown[];
}

export interface DriverBridgePortDef {
  /** Port id referenced by a downstream device's bridge_port (e.g. "serial:1"). */
  id: string;
  /**
   * Port kind. A serial port vends a transparent TCP pass-through; ir/relay
   * ports route commands through the bridge at send time.
   */
  kind: "serial" | "ir" | "relay";
  /**
   * For serial ports: the TCP port on the bridge host that transparently pipes
   * this serial line (e.g. 4999).
   */
  passthrough_port?: number;
  /** Human-readable port label shown in the connection picker. */
  label?: string;
}

/**
 * Optional. Declares this driver as a bridge: a device that exposes typed
 * ports other devices connect through (e.g. a serial-to-Ethernet or IR
 * bridge). The port declaration is valid in YAML, but the runtime behind a
 * port needs a Python driver: pushing serial line settings to the hardware
 * (prepare_bridge_port), and emitting/learning IR (bridge_emit /
 * bridge_learn_*) for ir ports.
 */
export interface DriverBridgeDef {
  /** The typed ports this bridge advertises. */
  ports: DriverBridgePortDef[];
}

/**
 * A full driver definition — the .avcdriver document (or a Python driver's
 * DRIVER_INFO) as the Builder edits it.
 */
export interface DriverDefinition {
  /** Unique driver identifier. Lowercase alphanumeric with underscores. */
  id: string;
  /** Human-readable display name. */
  name: string;
  /**
   * Manufacturer name. Must exist in manufacturers.json for catalog
   * submission.
   */
  manufacturer: string;
  /** Driver category. One of the ten catalog categories. */
  category: string;
  /** Semantic version, e.g. 1.0.0. */
  version: string;
  /** Driver author. */
  author: string;
  /**
   * Transport the driver uses to reach the device. Use "bridge" for a device
   * that has no address of its own and emits through a live bridge instance
   * (an IR device on an emitter port); it opens no socket and routes commands
   * via the bridge.
   */
  transport: string;
  /**
   * Optional. Transports this driver can use interchangeably (e.g. ["tcp",
   * "serial"] for a text protocol whose command/response strings are byte-
   * identical over the network or a serial line). The per-device connection
   * picks the actual transport; listing "serial" makes the device offerable
   * over a direct serial port or through a bridge. Opt-in: only declare it
   * when the strings really are identical across the listed media.
   */
  transports?: string[];
  /**
   * Optional. Declares this driver as a bridge: a device that exposes typed
   * ports other devices connect through (e.g. a serial-to-Ethernet or IR
   * bridge). The port declaration is valid in YAML, but the runtime behind a
   * port needs a Python driver: pushing serial line settings to the hardware
   * (prepare_bridge_port), and emitting/learning IR (bridge_emit /
   * bridge_learn_*) for ir ports.
   */
  bridge?: DriverBridgeDef;
  /** Brief description of what the driver controls. */
  description: string;
  /**
   * Protocol reference or product documentation URL. Must start with http://
   * or https://.
   */
  source_url?: string;
  /**
   * Message delimiter. Default "\r". Use "\r\n" for CRLF. Escape sequences \r
   * and \n are interpreted.
   */
  delimiter: string;
  /**
   * Opt-in constant string prepended to every command's send string (a fixed
   * packet header). Set it once instead of repeating it on each command. Byte-
   * stream transports only (tcp/serial/udp); never applied to OSC or HTTP.
   * Supports the same escape sequences and {config} substitution as send. A
   * command can opt out with raw: true. Requires platform 0.23.0.
   */
  command_prefix?: string;
  /**
   * Opt-in constant string appended to every command's send string (its
   * terminator). Set it once so you don't type \r on each command. Byte-stream
   * transports only (tcp/serial/udp). Supports the same escape sequences and
   * {config} substitution as send. A command can opt out with raw: true.
   * Requires platform 0.23.0.
   */
  command_suffix?: string;
  /**
   * Built-in generic devices only. When true, the device page shows a no-code
   * Commands & Responses editor that stores commands/responses/state_variables
   * in the device config and merges them into this driver at runtime.
   * Community drivers ship their commands and responses in the driver file and
   * should not set this.
   */
  inline_protocol?: boolean;
  /**
   * Marks this as an IR code-set device (a device controlled by an infrared
   * remote through an IR bridge). When true, the device page shows the IR
   * Codes editor (learn / paste Pronto / type sendir / database search / test
   * emit) and each code in the ir_codes map becomes a device command that
   * emits through the bound bridge's IR port. A build-your-own IR device
   * authors codes per-device; a community IR driver ships its code-set in
   * default_config.ir_codes. Codes are stored as vendor-neutral Pronto hex
   * plus a per-command repeat. Use transport "bridge" with this.
   */
  ir_codes?: boolean;
  /** TCP/UDP ports the device listens on (catalog metadata only). */
  ports?: number[];
  /**
   * Protocol names this driver speaks, e.g. ["pjlink"]. Helps discovery match
   * devices to drivers.
   */
  protocols?: string[];
  /**
   * True if the driver ships with simulator support (auto-gen or an explicit
   * simulator: section).
   */
  simulated?: boolean;
  /** True once the driver has been validated against real hardware. */
  verified?: boolean;
  /**
   * Controls the 'Open Web UI' button. Leave unset for auto-detect: the
   * platform finds a reachable web URL (HTTP devices from config, others from
   * a port probe / discovery scan) and adds the button on its own. true forces
   * it on (opens https://{host}); a string forces it on with that URL template
   * (e.g. "http://{host}:8080") with {host}/{port}/{config_key} substitution;
   * false forces it off. Requires platform >= 0.24.0.
   */
  web_ui?: boolean | string;
  /**
   * Minimum OpenAVC version required. Blocks install on older platforms
   * missing a needed feature. Semantic version.
   */
  min_platform_version?: string | null;
  /** Search/browse tags. Lowercase, hyphen-separated, alphanumeric. */
  tags?: string[];
  help?: DriverHelpDef;
  /** True if this driver is superseded. Requires replacement_id. */
  deprecated?: boolean;
  /**
   * ID of the driver that replaces this one. Only valid when deprecated is
   * true.
   */
  replacement_id?: string;
  /** Specific device models this driver supports, with confidence levels. */
  compatible_models?: DriverCompatibleModelsEntry[];
  /**
   * Default values for config fields (e.g. host, port, poll_interval,
   * inter_command_delay).
   */
  default_config: Record<string, unknown>;
  /**
   * Computed config values, each a template substituted from other config
   * fields, e.g. {"ws": "/workspace/{workspace_id}"}. If any {field} the
   * template references is empty or missing, the derived value is "" — so an
   * optional prefixed address segment simply disappears. Computed once when
   * the device connects and visible to every command address, on_connect
   * entry, response, and poll query (just like a real config field). Lets one
   * friendly field (e.g. a workspace id) drive both a bare and a prefixed
   * address form without conditional logic in every command.
   */
  config_derived?: Record<string, string>;
  /**
   * Per-device connection settings shown in the Add Device dialog. Keyed by
   * config field name.
   */
  config_schema: Record<string, unknown>;
  /**
   * Configurable values that live on the device hardware (polled + writable).
   * Keyed by setting name.
   */
  device_settings?: Record<string, DriverDeviceSettingDef>;
  /**
   * Read-only state properties this driver exposes. Keyed by state variable
   * name.
   */
  state_variables: Record<string, DriverStateVarDef>;
  /**
   * Sub-units this device manages (encoders, decoders, zones, presets). Keyed
   * by child type name.
   */
  child_entity_types?: Record<string, DriverChildEntityType>;
  /** Commands this driver can send. Keyed by command name. */
  commands: Record<string, DriverCommandDef>;
  /**
   * Command ids promoted to one-click Quick Action buttons at the top of the
   * device view. Sugar for actions of kind "command". Each id must name a
   * declared command.
   */
  quick_actions?: string[];
  /**
   * Driver-declared actions promoted to buttons in the device view.
   * kind:"command" promotes a declared command (runs online); kind:"setup" is
   * an offline-capable provisioning wizard handled by the driver's
   * run_setup_action().
   */
  actions?: DriverActionDef[];
  /**
   * Patterns for parsing device replies. Regex/OSC rules are checked in order
   * and the first match wins; a json: true rule parses the whole JSON body and
   * applies all of its field mappings, so a multi-field JSON reply fully
   * populates.
   */
  responses: DriverResponseDef[];
  /**
   * Commands sent immediately after connect, before polling. Strings for
   * TCP/serial/UDP; {address, args} mappings for OSC (args carry typed OSC
   * values for a value-setting bring-up message); {each_child, send} templates
   * expand to one query per registered child. Any mapping entry may add when:
   * <config_field> to run only while that field is on, and a {send} entry may
   * add query_for: <state_var> to name the state variable its reply reports.
   */
  on_connect?: (string | DriverEachChildQuery | DriverQueryEntry | DriverOscConnectItem | Record<string, unknown>)[];
  /**
   * Periodic status query configuration. NOTE: a polling.interval key is inert
   * and rejected by the catalog validator; set the cadence via
   * default_config.poll_interval instead.
   */
  polling: { queries?: (string | DriverEachChildQuery | DriverQueryEntry)[] };
  auth?: DriverAuthDef;
  liveness?: DriverLivenessDef;
  push?: DriverPushDef;
  /**
   * Framing for the control transport's inbound byte stream. Top level accepts
   * length_prefix / fixed_length only; struct_frame is push-only
   * (push.frame_parser, for tcp_listener dial-back frames).
   */
  frame_parser?: { type: string; [key: string]: unknown } | null;
  send_frame?: { type: string; [key: string]: unknown } | null;
  simulator?: DriverSimulatorDef;
  discovery?: DriverDiscoveryConfig;
  /**
   * Where the file lives on disk — set by the list endpoint, not authored.
   * builtin ships with the platform (read-only; use Customize a Copy); user
   * lives in driver_repo. Absent on an unsaved draft.
   */
  source?: "builtin" | "user";
}
