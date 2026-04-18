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
  button_image_active?: string;
  image_fit?: string;
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
    audio_follow_video?: boolean;
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

export interface ScheduleConfig {
  id: string;
  type: string;
  expression: string;
  event: string;
  enabled: boolean;
  description: string;
}

export interface ISCConfig {
  enabled: boolean;
  shared_state: string[];
  auth_key: string;
  peers: string[];
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
  schedules: ScheduleConfig[];
  isc: ISCConfig;
}

// --- Driver Definition types ---

export interface DriverCommandDef {
  label: string;
  string: string;
  send?: string;
  method?: string;
  path?: string;
  body?: string;
  params: Record<string, { type: string; required?: boolean; values?: string[]; help?: string }>;
  help?: string;
}

export interface DriverResponseMapping {
  group: number;
  state: string;
  type?: string;
  map?: Record<string, string>;
}

export interface DriverResponseDef {
  // YAML .avcdriver format uses match/set, Driver Builder UI uses pattern/mappings.
  // Both are accepted; the runtime handles either.
  pattern?: string;
  match?: string;
  mappings?: DriverResponseMapping[];
  set?: Record<string, string>;
}

export interface DriverDiscoveryHints {
  ports?: number[];
  mac_prefixes?: string[];
  protocols?: string[];
  mdns_services?: string[];
  hostname_patterns?: string[];
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
  write?: {
    send?: string;
    method?: string;
    path?: string;
    body?: string;
  };
}

export interface DriverSimulatorDef {
  initial_state?: Record<string, unknown>;
  delays?: Record<string, number>;
  controls?: Array<Record<string, unknown>>;
  command_handlers?: Array<Record<string, unknown>>;
  error_modes?: Record<string, { behavior: string; description?: string; state?: Record<string, unknown> }>;
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
  state_variables: Record<string, { type: string; label: string; values?: string[]; help?: string }>;
  commands: Record<string, DriverCommandDef>;
  responses: DriverResponseDef[];
  polling: { interval?: number; queries?: string[] };
  frame_parser?: { type: string; [key: string]: unknown } | null;
  discovery?: DriverDiscoveryHints;
  device_settings?: Record<string, DriverDeviceSettingDef>;
  simulator?: DriverSimulatorDef;
  help?: { overview?: string; setup?: string };
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
  values?: string[];
  min?: number;
  max?: number;
  regex?: string;
}

export interface DeviceSettingValue extends DeviceSettingDef {
  current_value: unknown;
}

export interface DriverInfo {
  id: string;
  name: string;
  manufacturer: string;
  category: string;
  description?: string;
  version?: string;
  author?: string;
  commands: Record<string, unknown>;
  config_schema: Record<string, unknown>;
  default_config?: Record<string, unknown>;
  state_variables?: Record<string, unknown>;
  device_settings?: Record<string, DeviceSettingDef>;
  help?: { overview?: string; setup?: string };
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
  driver_info: Record<string, unknown>;
  config?: Record<string, unknown>;
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
}

export interface InstalledDriver {
  id: string;
  name: string;
  format: string;
  filename: string;
  source: string;  // 'builtin' | 'community' | 'user'
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
