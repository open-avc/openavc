// TypeScript types mirroring Python Pydantic models from server/core/project_loader.py

import type {
  EnumOption,
  ParamOptionsFrom,
  ParamTypeFrom,
} from "./types.gen";

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
  // Macro-level throttle, enforced at the engine chokepoint so it applies
  // however the macro is fired (script, REST, AI, UI, trigger, another macro).
  overlap?: 'skip' | 'queue' | 'allow';
  cooldown_seconds?: number;
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
  send_on_release?: boolean; // slider/fader: send only when the drag ends
  send_throttle_ms?: number; // slider/fader: min ms between live sends
  display_decimals?: number; // slider/fader: decimal places in the value readout
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
  /** Top-level short route for the plugin's guest router (e.g. "present" -> /present). */
  guest_alias?: string;
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
// Generated from the platform's driver-contract registry — see
// types.gen.ts (rendered by: python -m server.drivers.contract_gen).

export * from "./types.gen";

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
  // Enum options — bare wire values or {value, label} pairs (see EnumOption).
  values?: EnumOption[];
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
  // Enum options — bare wire values or {value, label} pairs (see EnumOption).
  values?: EnumOption[];
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
  kind: "command" | "setup" | "link";
  label: string;
  icon?: string | null; // lucide icon name
  confirm?: boolean | string | null; // string = custom confirmation message
  visible_when?: ActionVisibleWhen | null;
  availability: "online" | "offline" | "always";
  params: Record<string, ActionParam>;
  command?: string; // kind === "command": the command id invoked
  url?: string; // kind === "link": URL to open (host-substituted by the backend)
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
  // Unit for numeric values (e.g. "dB") — fills a bound control's Unit in
  // the UI Builder's range-match prompt.
  unit?: string;
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

interface ParsedSemver {
  main: number[];
  pre: string[];
}

/** Parse a semver string into numeric main parts and pre-release identifiers.
 *  Build metadata (`+...`) is stripped (it has no precedence). Returns null if
 *  the main version has a non-numeric part, so callers can treat it as
 *  incomparable rather than silently coercing NaN to 0. */
function parseSemver(v: string): ParsedSemver | null {
  const noBuild = v.split("+", 1)[0];
  const dash = noBuild.indexOf("-");
  const core = dash === -1 ? noBuild : noBuild.slice(0, dash);
  const pre = dash === -1 ? [] : noBuild.slice(dash + 1).split(".");
  const main = core.split(".").map((s) => parseInt(s, 10));
  if (main.some((n) => Number.isNaN(n))) return null;
  return { main, pre };
}

/** Semver precedence compare: -1 / 0 / 1 for a < b / a == b / a > b.
 *  Unparseable versions compare equal (0) so a garbled string never fabricates
 *  an update. Follows semver: main parts numeric; a pre-release ranks LOWER
 *  than its release; build metadata is ignored. */
function compareSemver(a: string, b: string): number {
  const pa = parseSemver(a);
  const pb = parseSemver(b);
  if (!pa || !pb) return 0;

  const len = Math.max(pa.main.length, pb.main.length);
  for (let i = 0; i < len; i++) {
    const x = pa.main[i] ?? 0;
    const y = pb.main[i] ?? 0;
    if (x !== y) return x > y ? 1 : -1;
  }

  // Equal main parts: a version WITHOUT a pre-release outranks one WITH.
  if (!pa.pre.length && !pb.pre.length) return 0;
  if (!pa.pre.length) return 1;
  if (!pb.pre.length) return -1;

  // Both pre-release: compare identifiers left to right (numeric < alphanumeric;
  // more identifiers outrank a prefix-equal shorter set).
  const plen = Math.max(pa.pre.length, pb.pre.length);
  for (let i = 0; i < plen; i++) {
    const ai = pa.pre[i];
    const bi = pb.pre[i];
    if (ai === undefined) return -1;
    if (bi === undefined) return 1;
    const aNum = /^\d+$/.test(ai);
    const bNum = /^\d+$/.test(bi);
    if (aNum && bNum) {
      const d = parseInt(ai, 10) - parseInt(bi, 10);
      if (d !== 0) return d > 0 ? 1 : -1;
    } else if (aNum !== bNum) {
      return aNum ? -1 : 1;
    } else if (ai !== bi) {
      return ai > bi ? 1 : -1;
    }
  }
  return 0;
}

/** Compare semver strings. Returns true if `available` is newer than `installed`.
 *  Handles pre-release and build suffixes (e.g. `1.0.1-beta`) — the old
 *  `.split('.').map(Number)` turned any suffixed segment into NaN and coerced
 *  it to 0, silently hiding updates to (or from) a suffixed version.
 *
 *  An installed driver/plugin with no recorded version (an older install or a
 *  hand-placed file) is treated as older than any catalogued release, so the
 *  community version is offered as an update — otherwise it silently sticks on
 *  stale device logic with no way to refresh short of uninstall/reinstall. */
export function hasUpdate(installed: string, available: string): boolean {
  if (!available) return false;
  if (!installed) return true;
  return compareSemver(available, installed) > 0;
}
