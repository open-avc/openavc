// TypeScript types mirroring Python Pydantic models from server/core/project_loader.py

export interface ProjectMeta {
  id: string;
  name: string;
  description: string;
  created: string;
  modified: string;
}

export interface DeviceConfig {
  id: string;
  driver: string;
  name: string;
  config: Record<string, unknown>;
  enabled?: boolean;
  pending_settings?: Record<string, unknown>;
  // Project-side metadata for child entities owned by this device. Keyed
  // by child_type -> padded local_id -> ChildEntityConfig. Empty for
  // devices whose drivers don't declare child_entity_types.
  child_entities?: Record<string, Record<string, ChildEntityConfig>>;
}

export interface ChildEntityConfig {
  label: string;
  config: Record<string, unknown>;
}

export interface DeviceGroup {
  id: string;
  name: string;
  device_ids: string[];
}

export interface VariableValidation {
  min?: number | null;
  max?: number | null;
  allowed?: string[] | null;
}

export interface VariableConfig {
  id: string;
  type: string;
  default: unknown;
  label: string;
  description?: string;
  dashboard?: boolean;
  persist?: boolean;
  source_key?: string;
  source_map?: Record<string, unknown>;
  validation?: VariableValidation | null;
}

export interface StepCondition {
  key: string;
  operator: string; // eq, ne, gt, lt, gte, lte, truthy, falsy
  value?: unknown;
}

export interface MacroStep {
  action: string;
  device?: string;
  group?: string;  // group.command: target device group ID
  command?: string;
  params?: Record<string, unknown>;
  seconds?: number;
  key?: string;
  value?: unknown;
  macro?: string;
  event?: string;
  payload?: Record<string, unknown>;
  page?: string; // ui.navigate: target page id, or "$back" / "$dismiss"
  description?: string;

  // Conditional step fields (action == "conditional")
  condition?: StepCondition;
  then_steps?: MacroStep[];
  else_steps?: MacroStep[];

  // wait_until step fields (action == "wait_until")
  // timeout is seconds; null means "never time out"
  timeout?: number | null;
  on_timeout?: "fail" | "continue";

  // Step-level guard
  skip_if?: StepCondition;

  // Device offline guard
  skip_if_offline?: boolean;
}

export interface TriggerCondition {
  key: string;
  operator: string;
  value?: unknown;
}

export interface TriggerConfig {
  id: string;
  type: string; // "schedule" | "state_change" | "event" | "startup"
  enabled: boolean;

  // Schedule
  cron?: string;

  // State change
  state_key?: string;
  state_operator?: string;
  state_value?: unknown;

  // Event
  event_pattern?: string;

  // Execution control
  delay_seconds?: number;
  debounce_seconds?: number;
  cooldown_seconds?: number;
  overlap?: string; // "skip" | "queue" | "allow"

  // Guard conditions
  conditions?: TriggerCondition[];
}

export interface MacroConfig {
  id: string;
  name: string;
  steps: MacroStep[];
  triggers?: TriggerConfig[];
  stop_on_error?: boolean;
  cancel_group?: string;
}

export interface GridArea {
  col: number;
  row: number;
  col_span: number;
  row_span: number;
}

export interface UIElementOption {
  label: string;
  value: string;
}

export interface UIElement {
  id: string;
  type: string;
  label?: string;
  text?: string;
  min?: number;
  max?: number;
  step?: number;
  output_min?: number;
  output_max?: number;
  scale_to_full?: boolean;
  response?: string; // slider/fader taper: "linear" | "logarithmic"
  response_db_range?: number; // logarithmic taper: dB span of the throw
  target_page?: string;
  options?: UIElementOption[];
  placeholder?: string;
  src?: string;
  preset_number?: number;
  icon?: string;
  icon_position?: string;
  icon_size?: number;
  icon_color?: string;
  display_mode?: string;
  button_image?: string;
  image_fit?: string;
  object_fit?: string;
  image_blend_mode?: string;
  image_opacity?: number;
  frameless?: boolean;
  // Gauge
  unit?: string;
  arc_angle?: number;
  zones?: Array<{ from: number; to: number; color: string }>;
  // Slider / Level meter / Fader
  orientation?: string;
  thumb_size?: number;
  // Clock
  clock_mode?: string;
  format?: string;
  timezone?: string;
  target_time?: string;
  start_key?: string;
  duration_minutes?: number;
  // Keypad
  digits?: number;
  auto_send?: boolean;
  auto_send_delay_ms?: number;
  keypad_style?: string;
  show_display?: boolean;
  // Group
  label_position?: string;
  collapsible?: boolean;
  // List
  list_style?: string;
  item_height?: number;
  items?: Array<{ label: string; value: string }>;
  // Matrix
  matrix_config?: {
    input_count?: number;
    output_count?: number;
    input_labels?: string[];
    output_labels?: string[];
    input_key_pattern?: string;
    output_key_pattern?: string;
    route_key_pattern?: string;
    audio_route_key_pattern?: string;
    audio_follow_video?: boolean;
    show_lock?: boolean;
    show_mute?: boolean;
    presets?: { name: string; macro?: string }[];
  };
  matrix_style?: string;
  // Plugin element
  plugin_type?: string;
  plugin_id?: string;
  plugin_config?: Record<string, unknown>;
  grid_area: GridArea;
  style: Record<string, unknown>;
  bindings: Record<string, unknown>;
}

export interface GridConfig {
  columns: number;
  rows: number;
}

export interface OverlayConfig {
  width?: number;
  height?: number;
  position?: string;
  backdrop?: string;
  dismiss_on_backdrop?: boolean;
  animation?: string;
  side?: string;
}

export interface PageBackground {
  color?: string;
  image?: string;
  image_opacity?: number;
  image_size?: string;
  image_position?: string;
  gradient?: {
    type: string;
    angle: number;
    from: string;
    to: string;
  };
}

export interface UIPage {
  id: string;
  name: string;
  page_type?: string;
  overlay?: OverlayConfig;
  background?: PageBackground;
  grid: GridConfig;
  grid_gap?: number;
  elements: UIElement[];
}

export interface UISettings {
  theme: string;
  theme_id: string;
  theme_overrides: Record<string, unknown>;
  accent_color: string;
  font_family: string;
  lock_code: string;
  idle_timeout_seconds: number;
  idle_page: string;
  orientation: string;
  page_transition: string;
  page_transition_duration: number;
  element_entry: string;
  element_stagger_ms: number;
  element_stagger_style?: string;
}

export interface MasterElement extends UIElement {
  pages: string | string[];
}

export interface PageGroup {
  name: string;
  pages: string[];
}

export interface UIConfig {
  settings: UISettings;
  pages: UIPage[];
  master_elements?: MasterElement[];
  page_groups?: PageGroup[];
}

export interface ScriptConfig {
  id: string;
  file: string;
  enabled: boolean;
  description: string;
}

export interface PythonDriverInfo {
  id: string;
  filename: string;
  name: string;
  manufacturer: string;
  category: string;
  loaded: boolean;
  load_error: string | null;
  devices_using: string[];
}

export interface ISCConfig {
  enabled: boolean;
  shared_state: string[];
  auth_key: string;
  peers: string[];
  // Glob allowlist (matched against "<device_id>.<command>") for device
  // commands a remote peer may run on this instance. Empty = deny all.
  allowed_remote_commands: string[];
}

export interface DriverDependency {
  driver_id: string;
  driver_name: string;
  version: string;
  source: string; // "builtin" | "community" | "user" | "unknown"
}

export interface PluginConfig {
  enabled: boolean;
  config: Record<string, unknown>;
}

export interface PluginDependency {
  plugin_id: string;
  plugin_name: string;
  version: string;
  source: string;
  platforms: string[];
}

export interface PluginInfo {
  plugin_id: string;
  name: string;
  version: string;
  author: string;
  description: string;
  /**
   * Markdown-formatted "How to Use" content shown on the plugin detail page.
   * Renders below the one-line description. Plugins set this in PLUGIN_INFO
   * to give users in-IDE instructions instead of pointing at the README.
   */
  usage?: string;
  category: string;
  license?: string;
  status: string; // "running" | "stopped" | "error" | "missing" | "incompatible"
  platforms: string[];
  capabilities: string[];
  installed: boolean;
  compatible: boolean;
  error?: string;
  missing_reason?: string;
  config_schema?: Record<string, SchemaField>;
  has_config_schema?: boolean;
  has_surface_layout?: boolean;
  has_extensions?: boolean;
  dependencies?: string[];
}

export interface SchemaField {
  type: string;
  label: string;
  description?: string;
  default?: unknown;
  required?: boolean;
  placeholder?: string;
  pattern?: string;
  max_length?: number;
  min?: number;
  max?: number;
  step?: number;
  options?: { value: string; label: string }[];
  fields?: Record<string, SchemaField>;
  item_schema?: Record<string, SchemaField>;
  collapsed?: boolean;
  visible_when?: Record<string, unknown>;
  min_items?: number;
  max_items?: number;
  device_field?: string;
}

export interface ProjectConfig {
  openavc_version: string;
  project: ProjectMeta;
  devices: DeviceConfig[];
  device_groups: DeviceGroup[];
  connections: Record<string, Record<string, unknown>>;
  driver_dependencies: DriverDependency[];
  plugin_dependencies: PluginDependency[];
  plugins: Record<string, PluginConfig>;
  variables: VariableConfig[];
  macros: MacroConfig[];
  ui: UIConfig;
  scripts: ScriptConfig[];
  isc: ISCConfig;
}

// --- Driver Definition types ---

export interface DriverCommandDef {
  label: string;
  // TCP / serial / UDP — the wire string sent to the device.
  // The runtime also accepts the legacy `string` key as an alias and
  // emits a deprecation warning when it sees it. Read with
  // `cmd.send ?? cmd.string ?? ""`; always write to `send`.
  send: string;
  string?: string;
  // HTTP — REST request shape. method defaults to GET.
  // headers and query_params support {param} substitution from the
  // command's params map and the device config.
  method?: string;
  path?: string;
  body?: string;
  headers?: Record<string, string>;
  query_params?: Record<string, string>;
  // OSC.
  address?: string;
  args?: { type: string; value: string }[];
  params: Record<string, DriverParamDef>;
  help?: string;
}

export interface DriverParamDef {
  type: string;
  required?: boolean;
  label?: string;
  help?: string;
  // The runtime accepts `description` as an alias for `help` — read either,
  // write `help` (canonical) when introducing new params.
  description?: string;
  // Numeric bounds — only meaningful when type is integer or number.
  // Enforced at command time by the runtime (the IDE also flags violations as
  // an authoring aid).
  min?: number;
  max?: number;
  // Regex (full-match) a free-text value must satisfy — a shape check for
  // values that can't be enumerated (IP, hostname, fixed-length ID). The
  // runtime validates it at command time; the IDE shows an inline error while
  // authoring. (§69 Phase 3.)
  pattern?: string;
  // Default value — type-coerced by the runtime against `type`.
  default?: unknown;
  // Allowed values for type='enum' — required for that type.
  values?: string[];
  // Only meaningful for type='child_id' — names one of the driver's
  // declared child_entity_types. The runtime command picker renders a
  // dropdown of currently-registered children of that type; the value
  // passed at command time is the integer local id (the runtime handles
  // padding when assembling the wire string).
  child_type?: string;
  // Option providers (param pickers, §69 Phase 2) — make a free-text param a
  // dropdown sourced from values the platform already knows. Authoring-time
  // aids only; the runtime still validates the submitted value.
  //   options_state  — a device-relative state key. The widget reads
  //                    `device.<id>.<options_state>` (a JSON-encoded list) and
  //                    offers it. e.g. `options_state: "snapshot_banks"`.
  //   options_source — an absolute state key, read verbatim (the same
  //                    primitive plugins already use for select params).
  //   options_from   — cascade: source options from a sibling param's chosen
  //                    value. `{ param, source: "child_schema" }` reads the
  //                    control names of the child picked in a sibling
  //                    `child_id` param.
  options_state?: string;
  options_source?: string;
  options_from?: ParamOptionsFrom;
  // Derive this param's input TYPE from a sibling param's chosen value (a
  // "field follows the selection" cascade). `{ param }` names a sibling that
  // itself cascades off a child_id (`options_from: { source: "child_schema" }`);
  // this field then takes the type/min/max of the control chosen there — e.g. a
  // `value` field becomes a number spinner (with the gain's range) when `gain`
  // is picked, or Yes/No when `mute` is picked. Falls back to the declared
  // `type` until the sibling is chosen. Authoring aid; runtime still validates.
  type_from?: ParamTypeFrom;
}

export interface ParamOptionsFrom {
  // The sibling param whose chosen value selects the option set.
  param: string;
  // Where the options come from. Only "child_schema" is supported today (the
  // chosen child's per-instance control schema).
  source: string;
}

export interface ParamTypeFrom {
  // The sibling param (a child_schema `options_from` cascade) whose chosen
  // control supplies this field's type/min/max.
  param: string;
}

export interface DriverResponseMapping {
  group: number;
  arg?: number;
  state: string;
  type?: string;
  // Static literal — used when group=0. The runtime sets state to this
  // value verbatim rather than reading a capture group. Comes from the
  // `set: { state: <literal> }` shorthand in YAML drivers.
  value?: unknown;
  map?: Record<string, string>;
}

export interface DriverResponseDef {
  // YAML .avcdriver format uses match/set, Driver Builder UI uses pattern/mappings.
  // Both are accepted; the runtime handles either. The builder preserves
  // whichever form was loaded so byte-equal round-trips stay byte-equal.
  pattern?: string;
  match?: string;
  address?: string;
  mappings?: DriverResponseMapping[];
  // Shorthand for capture-or-static mappings:
  //   set: { volume: "$1" }       — capture group reference
  //   set: { mute: "true" }       — static string
  //   set: { signal: true }       — static boolean
  set?: Record<string, unknown>;
}

// Driver ``discovery:`` block. Schema reference:
// ``OpenAVC-Discovery-Spec.md`` §2 (workspace root) and the runtime parser
// in ``openavc/server/discovery/hints.py``.
//
// Two kinds of declarations:
//   * Fingerprints identify the driver alone (mDNS, SSDP, AMX-DDP, TCP
//     probe, UDP probe, Python escape-hatch). One match is enough.
//   * Hints narrow candidates (OUI, hostname, open port, manufacturer
//     alias, SNMP enterprise number). Multiple hints combine to surface
//     the device as a ``possible`` candidate.
//
// Each fingerprint may carry ``cross_vendor: true`` to mark a wire
// signal shared by multiple manufacturers (PJLink projectors, Crestron
// CIP family, ONVIF cameras). The matcher demotes such drivers to
// ``alternative`` whenever a peer driver claims the same device via a
// vendor-specific hint.

export interface DriverDiscoveryMdnsFingerprint {
  service: string;
  txt?: Record<string, string>;
  cross_vendor?: boolean;
}

export interface DriverDiscoverySsdpFingerprint {
  device_type: string;
  cross_vendor?: boolean;
}

export interface DriverDiscoveryAmxDdpFingerprint {
  make: string;
  model_pattern?: string;   // glob, default "*"
  cross_vendor?: boolean;
}

export type DriverDiscoveryExtractRule =
  | string                                  // static literal
  | { regex: string; group?: number };     // dynamic capture

export interface DriverDiscoveryProbe {
  port: number;
  // Exactly one of send_ascii / send_hex; both omitted means a
  // connect-only TCP probe (UDP probes must include one).
  send_ascii?: string;
  send_hex?: string;
  // Exactly one of expect / expect_regex / expect_hex — the runtime rejects
  // a probe that declares more than one. UDP probes require a matcher; TCP
  // probes that send bytes also need one.
  expect?: string;
  expect_regex?: string;
  expect_hex?: string;
  cross_vendor?: boolean;
  timeout_ms?: number;
  // Sugar for an ``extract.manufacturer`` literal. Feeds the
  // manufacturer-alias hint path so peer vendor drivers can claim the
  // device.
  extract_manufacturer?: string;
  extract?: Record<string, DriverDiscoveryExtractRule>;
}

export interface DriverDiscoveryPython {
  file: string;
  cross_vendor?: boolean;
}

export interface DriverDiscoveryConfig {
  // Fingerprints — any one alone identifies this driver.
  mdns?: Array<string | DriverDiscoveryMdnsFingerprint>;
  ssdp?: Array<string | DriverDiscoverySsdpFingerprint>;
  amx_ddp?: DriverDiscoveryAmxDdpFingerprint[];
  tcp_probe?: DriverDiscoveryProbe;
  udp_probe?: DriverDiscoveryProbe;
  python?: string | DriverDiscoveryPython;

  // Hints — combine to narrow candidates.
  oui?: string[];
  hostname?: string[];
  port_open?: number[];           // 22, 80, 443 are rejected at parse time
  manufacturer_alias?: string[];  // case-insensitive
  snmp_pen?: number;              // IANA Private Enterprise Number
}

export interface DriverDeviceSettingDef {
  label: string;
  type: string;
  help?: string;
  state_key?: string;
  default?: unknown;
  setup?: boolean;
  unique?: boolean;
  values?: string[];
  min?: number;
  max?: number;
  regex?: string;
  write?: {
    send?: string;
    method?: string;
    path?: string;
    body?: string;
    headers?: Record<string, string>;
    address?: string;
    args?: { type: string; value: string }[];
  };
}

export interface DriverSimulatorDef {
  push_state?: boolean;
  initial_state?: Record<string, unknown>;
  delays?: Record<string, number>;
  controls?: Array<Record<string, unknown>>;
  command_handlers?: Array<Record<string, unknown>>;
  error_modes?: Record<string, { behavior: string; description?: string; state?: Record<string, unknown> }>;
}

/**
 * Driver-authored child entity type. Mirrors the runtime
 * ``DRIVER_INFO["child_entity_types"]`` block consumed by
 * ``server/drivers/base.py`` (register_child / set_child_state) and
 * ``server/cloud/state_relay.py`` (per-property ``cloud_priority`` tiering).
 *
 * The editor surface adds the same metadata as device state_variables
 * (label, help, min/max/step, values) so authors don't see a new mental
 * model; only ``cloud_priority`` is child-specific. The runtime is
 * permissive about extra keys — only ``type``, ``min``, ``values``, and
 * ``cloud_priority`` are read.
 */
export interface DriverChildStateVarDef {
  type: string;
  label?: string;
  help?: string;
  values?: string[];
  min?: number;
  max?: number;
  step?: number;
  /** "low" relays at the verbose-state cadence (30s default); "high"
   *  rides the snappy top-tier cadence (2s default). Unspecified =
   *  default child cadence (5s). */
  cloud_priority?: "low" | "high";
}

export interface DriverChildIdFormat {
  /** v1 only supports "integer". The platform validates at register_child
   *  time; the editor surfaces a fixed dropdown. */
  type: "integer";
  min?: number;
  max?: number;
  pad_width?: number;
}

export interface DriverChildEntityType {
  label?: string;
  label_plural?: string;
  id_format: DriverChildIdFormat;
  state_variables: Record<string, DriverChildStateVarDef>;
  summary_fields?: string[];
  /** Which declared state variable carries the controller-owned name
   *  (distinct from the user-set ``label`` injected by the platform). */
  label_field?: string;
}

export interface DriverDefinition {
  id: string;
  name: string;
  manufacturer: string;
  category: string;
  version: string;
  author: string;
  description: string;
  transport: string;
  delimiter: string;
  default_config: Record<string, unknown>;
  config_schema: Record<string, unknown>;
  state_variables: Record<string, { type: string; label: string; values?: string[]; help?: string; min?: number; max?: number; step?: number }>;
  /** Sub-units the driver manages (encoders, decoders, zones, presets, ...).
   *  Optional — drivers that don't declare children stay one flat device.
   *  See ``server/drivers/base.py`` ``BaseDriver`` child API. */
  child_entity_types?: Record<string, DriverChildEntityType>;
  commands: Record<string, DriverCommandDef>;
  responses: DriverResponseDef[];
  polling: { interval?: number; queries?: string[] };
  frame_parser?: { type: string; [key: string]: unknown } | null;
  discovery?: DriverDiscoveryConfig;
  device_settings?: Record<string, DriverDeviceSettingDef>;
  simulator?: DriverSimulatorDef;
  help?: { overview?: string; setup?: string };
  // Catalog / publishing metadata. The runtime reads `protocols` and
  // `min_platform_version`; the others (`tags`, `simulated`, `verified`,
  // `source_url`, `ports`, `compatible_models`) are surfaced by the
  // community catalog and the Browse Drivers UI.
  min_platform_version?: string;
  protocols?: string[];
  tags?: string[];
  simulated?: boolean;
  verified?: boolean;
  source_url?: string;
  ports?: number[];
  // Sequence of wire strings sent immediately after connect (and after any
  // auth handshake completes). Used for verbose-mode toggles, GET ALL, push
  // subscriptions. The runtime substitutes config-key placeholders.
  on_connect?: string[];
  // Login handshake the runtime performs after raw connect. Today only
  // type='telnet_login' is implemented (prompt-driven Telnet/SSH banner
  // login). Username/password come from device config keys named here.
  auth?: DriverAuthDef;
  // Where the file lives on disk — set by the list endpoint, not authored.
  // "builtin": ships with the platform (read-only, can't delete or edit
  // in place; use Customize a Copy). "user": lives in driver_repo, freely
  // editable. Absent on a brand-new draft that hasn't been saved yet.
  source?: "builtin" | "user";
}

export interface DriverAuthDef {
  type?: string;
  username_prompt?: string;
  password_prompt?: string;
  success_pattern?: string;
  failure_pattern?: string;
  username_field?: string;
  password_field?: string;
  timeout_seconds?: number;
  line_ending?: string;
  skip_if_empty?: boolean;
}

// --- API response types ---

export interface DeviceSettingDef {
  type: string;
  label: string;
  help: string;
  state_key?: string;
  default?: unknown;
  setup?: boolean;
  unique?: boolean;
  secret?: boolean;
  values?: string[];
  min?: number;
  max?: number;
  regex?: string;
}

export interface DeviceSettingValue extends DeviceSettingDef {
  current_value: unknown;
}

/** A typed port a bridge device advertises (from a driver's
 *  DRIVER_INFO["bridge"]["ports"]). Other devices connect *through* these.
 *  Serial ports carry a `passthrough_port` (the TCP port on the bridge host
 *  that transparently pipes that serial line, e.g. 4999); IR / relay ports
 *  route commands through the bridge at send time and omit it. */
export interface BridgePort {
  id: string;
  kind: string; // "serial" | "ir" | "relay"
  passthrough_port?: number;
  label?: string;
}

export interface DriverInfo {
  id: string;
  name: string;
  manufacturer: string;
  category: string;
  description?: string;
  version?: string;
  author?: string;
  /** Primary wire transport ("tcp" | "serial" | "udp" | "http" | "osc"). */
  transport?: string;
  /** Multi-transport drivers (e.g. ["tcp", "serial"]) — the connection picker
   *  offers "Direct serial" / "Through a bridge" for serial-capable drivers. */
  transports?: string[];
  /** Present + non-empty `ports` => this driver is a bridge other devices can
   *  connect through. */
  bridge?: { ports?: BridgePort[] };
  commands: Record<string, unknown>;
  config_schema: Record<string, unknown>;
  default_config?: Record<string, unknown>;
  state_variables?: Record<string, unknown>;
  device_settings?: Record<string, DeviceSettingDef>;
  help?: { overview?: string; setup?: string };
}

// --- Device Actions (Quick Action strip) ---

/** One leaf condition in an action's visible_when. Same shape the panel and
 *  Stream Deck (§38) use. `key` may contain `$id`, replaced with the device id. */
export interface ActionCondition {
  key: string;
  operator?: string; // eq, ne, gt, lt, gte, lte, truthy, falsy
  value?: unknown;
}

/** A single condition, or an any/all group of them. */
export type ActionVisibleWhen =
  | ActionCondition
  | { all: ActionCondition[] }
  | { any: ActionCondition[] };

/** A param field for an action's input dialog (subset of the command/config
 *  schema field shape the Send Command form already renders). */
export interface ActionParam {
  type?: string; // string, integer, number, boolean, enum, password, child_id
  label?: string;
  help?: string;
  required?: boolean;
  values?: string[];
  min?: number;
  max?: number;
  pattern?: string;
  default?: unknown;
  secret?: boolean;
  child_type?: string;
  // Option providers — see DriverParamDef for the full contract.
  options_state?: string;
  options_source?: string;
  options_from?: ParamOptionsFrom;
  type_from?: ParamTypeFrom;
}

/** A driver-declared action, resolved by the backend (quick_actions sugar
 *  folded into the unified list). */
export interface DeviceAction {
  id: string;
  kind: "command" | "setup";
  label: string;
  icon?: string | null; // lucide icon name
  confirm?: boolean | string | null; // string = custom confirmation message
  visible_when?: ActionVisibleWhen | null;
  availability: "online" | "offline" | "always";
  params: Record<string, ActionParam>;
  command?: string; // kind === "command": the command id invoked
}

export interface DeviceInfo {
  id: string;
  name: string;
  driver: string;
  connected: boolean;
  orphaned?: boolean;
  orphan_reason?: string;
  enabled?: boolean;
  state: Record<string, unknown>;
  commands: Record<string, unknown>;
  actions?: DeviceAction[];
  driver_info: Record<string, unknown>;
  config?: Record<string, unknown>;
}

// --- Child Entities ---
//
// Mirrors the runtime shape returned by /api/devices/{id}/children. The
// per-type schema is the effective schema (platform `online` + `label`
// injected) so the IDE can render columns + the expanded state view
// without hardcoding which keys exist.

export interface ChildEntityStateVarDef {
  type: string;
  label?: string;
  values?: string[];
  min?: number;
  max?: number;
  step?: number;
  cloud_priority?: string;
  // Marks this child state var as a settable control (not a read-only
  // mirror or metadata). A command param that cascades off this child type
  // (`options_from: { source: "child_schema" }`) offers only `control: true`
  // vars when any are flagged, so the picker shows real controls rather than
  // every state key. Optional — when no var on a child is flagged, the
  // cascade offers all non-platform keys.
  control?: boolean;
}

export interface ChildEntityIdFormat {
  // "integer" (numbered children, zero-padded) or "string" (children keyed
  // by a device-native name, e.g. a Q-SYS Code Name).
  type: string;
  min?: number;
  max?: number;
  pad_width?: number;
  max_length?: number;
}

export interface ChildEntityTypeSchema {
  label?: string;
  label_plural?: string;
  id_format: ChildEntityIdFormat;
  state_variables: Record<string, ChildEntityStateVarDef>;
  summary_fields?: string[];
  label_field?: string;
  // When true, each child of this type carries its own discovered control
  // set (see ChildEntityEntry.schema); the type-level state_variables above
  // hold only the platform-managed online/label.
  dynamic?: boolean;
}

export interface ChildEntityEntry {
  // Integer for numbered children, string for name-keyed children.
  local_id: number | string;
  local_id_padded: string;
  label: string;
  config: Record<string, unknown>;
  registered: boolean;
  state: Record<string, unknown>;
  // Present only for dynamic child types: this child's own state-variable
  // schema (its discovered controls + platform online/label).
  schema?: Record<string, ChildEntityStateVarDef>;
}

export interface ChildEntitiesListResponse {
  device_id: string;
  child_entity_types: Record<string, ChildEntityTypeSchema>;
  children: Record<string, ChildEntityEntry[]>;
}

export interface ChildEntitiesByTypeResponse {
  device_id: string;
  child_type: string;
  schema: ChildEntityTypeSchema;
  children: ChildEntityEntry[];
}

export interface ChildEntityDetailResponse extends ChildEntityEntry {
  device_id: string;
  child_type: string;
}

export interface ChildEntityRefreshResponse {
  status: string;
  device_id: string;
  result: unknown;
}

export interface StateHistoryEntry {
  key: string;
  old_value: unknown;
  new_value: unknown;
  source: string;
  timestamp: number;
}

export interface ScriptReference {
  script_id: string;
  script_name: string;
  key: string;
  usage_type: string; // "read" | "write" | "subscribe"
  line: number;
}

export interface LogEntryResponse {
  timestamp: number;
  level: string;
  source: string;
  category: string;
  message: string;
}

// --- Project Library types ---

export interface LibraryProject {
  id: string;
  name: string;
  description: string;
  device_count: number;
  page_count: number;
  macro_count: number;
  script_count: number;
  required_drivers: string[];
  created: string;
  modified: string;
}

export interface LibraryProjectDetail {
  id: string;
  project: ProjectConfig;
  scripts: Record<string, string>;
}

// --- Community Driver types ---

export interface CommunityDriver {
  id: string;
  name: string;
  file: string;
  format: 'avcdriver' | 'python';
  category: string;
  manufacturer: string;
  version: string;
  author: string;
  transport: string;
  verified: boolean;
  simulated?: boolean;
  description: string;
  protocols?: string[];
  ports?: number[];
  min_platform_version?: string;
  // Schema v1 fields produced by openavc-drivers/scripts/build_index.py
  source_url?: string;
  tags?: string[];
  help?: { overview: string; setup: string };
  deprecated?: boolean;
  replacement_id?: string;
  compatible_models?: Array<{
    manufacturer: string;
    models: string[];
    confidence: 'full' | 'partial' | 'untested';
    notes?: string;
  }>;
}

export interface InstalledDriver {
  id: string;
  name: string;
  format: string;
  filename: string;
  version: string;
}

/** Compare semver strings. Returns true if `available` is newer than `installed`. */
export function hasUpdate(installed: string, available: string): boolean {
  if (!installed || !available) return false;
  const a = installed.split(".").map(Number);
  const b = available.split(".").map(Number);
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const ai = a[i] || 0;
    const bi = b[i] || 0;
    if (bi > ai) return true;
    if (bi < ai) return false;
  }
  return false;
}
