/**
 * Device-aware Value picker for the UI Builder "Shows" bucket: a Device ->
 * Property cascade using the driver's friendly state-variable labels — the
 * Shows counterpart of the Does side's Device -> Command cascade.
 *
 * Authoring aid only: picking a property writes the same
 * `device.<id>.<key>` state key the raw picker would. Properties the
 * element can't obviously display and live-only keys (metadata, runtime
 * state not in the driver schema) are grouped under "Status & metadata" —
 * de-emphasized, never hidden. A device with no driver schema (disabled,
 * orphaned) falls back to its live state keys.
 */
import { useState, useEffect, useMemo } from "react";
import { Info } from "lucide-react";
import type {
  ProjectConfig,
  DeviceInfo,
  UIElement,
  ChildEntitiesListResponse,
  ChildEntityEntry,
} from "../../../api/types";
import { CHILD_RESERVED_PROPS } from "../../../api/types";
import { useConnectionStore } from "../../../store/connectionStore";
import * as api from "../../../api/restClient";
import {
  SearchableDropdown,
  dropdownRowStyle,
  dropdownGroupHeaderStyle,
  dropdownTypeBadgeStyle,
  dropdownEmptyHintStyle,
} from "../../shared/SearchableDropdown";

/** Shape of one entry in DRIVER_INFO.state_variables (per-device, from
 *  getDevice — instance-building drivers only populate it there). Child
 *  entity state vars share the shape (plus the same optional fields), so
 *  the range prompt and grouping logic treat both alike. */
export interface DeviceStateVarDef {
  type?: string;
  label?: string;
  values?: string[];
  help?: string;
  min?: number;
  max?: number;
  step?: number;
  /** Declared unit for numeric values (e.g. "dB") — preferred over the
   *  "(dB)" label parse when filling a matched control's Unit. */
  unit?: string;
  /** Driver marks vars meant to drive a control; the picker lists flagged
   *  vars first. Ordering only — unflagged vars are never hidden. */
  control?: boolean;
}

const NUMERIC_TYPES = new Set(["int", "integer", "float", "number"]);
const STRING_TYPES = new Set(["string", "str", "text"]);

/** Read-outs and device info that are rarely what a control shows. Demoted
 *  to the "Status & metadata" group — still listed, never removed. */
const METADATA_PATTERN =
  /(^|_)(name|label)$|^offline_|^last_|^(connected|online|host|port|model|version|firmware|serial|serial_number|mac|mac_address|ip|ip_address|uptime)$/;

function isEnumDef(def: DeviceStateVarDef): boolean {
  return (def.type || "").toLowerCase() === "enum" || (def.values?.length ?? 0) > 0;
}

/** Does this state var's declared type fit what the element displays? */
function varMatchesElement(elementType: string, def: DeviceStateVarDef): boolean {
  const t = (def.type || "").toLowerCase();
  switch (elementType) {
    case "slider":
    case "fader":
    case "gauge":
    case "level_meter":
      return NUMERIC_TYPES.has(t);
    case "select":
    case "list":
      return isEnumDef(def);
    case "text_input":
      return STRING_TYPES.has(t) || isEnumDef(def);
    default:
      return true;
  }
}

/** No schema (disabled/orphaned device): judge a live value's JS type. */
function liveValueMatchesElement(elementType: string, value: unknown): boolean {
  switch (elementType) {
    case "slider":
    case "fader":
    case "gauge":
    case "level_meter":
      return typeof value === "number";
    case "text_input":
      return typeof value === "string";
    default:
      return true;
  }
}

function matchGroupLabel(elementType: string): string {
  switch (elementType) {
    case "slider":
    case "fader":
    case "gauge":
    case "level_meter":
      return "Levels & values";
    case "select":
    case "list":
      return "Selections";
    case "text_input":
      return "Text values";
    default:
      return "Properties";
  }
}

interface PropEntry {
  suffix: string;
  label: string;
  def: DeviceStateVarDef | null;
  /** "match" / "other" / "more", or "child:<type>" for child-entity vars. */
  group: string;
  /** Render de-emphasized (platform/metadata rows inside a child group). */
  dim?: boolean;
}

/** Platform-injected child state keys — real and pickable, but never what a
 *  control binds to, so they sort last (dimmed) inside their child group.
 *  Generated from the driver contract: hand-listing them is how a new one
 *  ends up offered as an ordinary control in a command cascade. */
const PLATFORM_CHILD_PROPS = CHILD_RESERVED_PROPS;

/** Effective var defs for one child: a dynamic child's own schema when
 *  present, else the type-level schema. */
function childSchemaFor(
  resp: ChildEntitiesListResponse,
  ctype: string,
  entry: ChildEntityEntry,
): Record<string, DeviceStateVarDef> {
  return (entry.schema ??
    resp.child_entity_types[ctype]?.state_variables ??
    {}) as Record<string, DeviceStateVarDef>;
}

/** Resolve a bound suffix like "input.01.fader_db" against the device's
 *  children payload (child type -> registered child -> var def). */
export function childVarDefForSuffix(
  resp: ChildEntitiesListResponse | null,
  suffix: string,
): DeviceStateVarDef | null {
  if (!resp) return null;
  const parts = suffix.split(".");
  if (parts.length < 3) return null;
  const [ctype, padded] = parts;
  const prop = parts.slice(2).join(".");
  const entry = (resp.children?.[ctype] ?? []).find(
    (c) => c.local_id_padded === padded,
  );
  if (!entry) return null;
  return childSchemaFor(resp, ctype, entry)[prop] ?? null;
}

/** Var def for a bound device suffix — device-level schema first, then the
 *  child-entity schemas. Used by MatchDriverRangeRow, which doesn't hold the
 *  picker's already-fetched data. */
export async function fetchBoundVarDef(
  deviceId: string,
  suffix: string,
): Promise<DeviceStateVarDef | null> {
  const info = await api.getDevice(deviceId);
  const vars = (
    info.driver_info as
      | { state_variables?: Record<string, DeviceStateVarDef> }
      | undefined
  )?.state_variables;
  const direct = vars?.[suffix];
  if (direct) return direct;
  if (suffix.split(".").length < 3) return null;
  const kids = await api.listChildEntities(deviceId).catch(() => null);
  return childVarDefForSuffix(kids, suffix);
}

interface DeviceValuePickerProps {
  /** Current binding key ("" or a full state key like "device.mixer.input_1_fader_db"). */
  keyValue: string;
  project: ProjectConfig;
  element: UIElement;
  onKeyChange: (key: string) => void;
  onElementPatch: (patch: Partial<UIElement>) => void;
}

export function DeviceValuePicker({
  keyValue,
  project,
  element,
  onKeyChange,
  onElementPatch,
}: DeviceValuePickerProps) {
  const keyParts = keyValue.startsWith("device.") ? keyValue.split(".") : [];
  const boundDeviceId = keyParts[1] ?? "";
  const boundSuffix = keyParts.slice(2).join(".");

  // The device dropdown follows the bound key until the user picks another
  // device; the binding itself only changes when a property is picked.
  const [deviceOverride, setDeviceOverride] = useState<string | null>(null);
  const selectedDevice = deviceOverride ?? boundDeviceId;

  const [deviceInfo, setDeviceInfo] = useState<DeviceInfo | null>(null);
  useEffect(() => {
    if (!selectedDevice) {
      setDeviceInfo(null);
      return;
    }
    // Guarded like the child-entity fetch below it and like ActionPicker's:
    // switch devices while an earlier getDevice() is in flight and the late
    // answer would repaint this picker with the previous device's properties,
    // ranges and units, under the new device's name.
    let stale = false;
    api.getDevice(selectedDevice)
      .then((info) => { if (!stale) setDeviceInfo(info); })
      .catch(() => { if (!stale) setDeviceInfo(null); });
    return () => { stale = true; };
  }, [selectedDevice]);

  // Child entities (registered children + per-type schemas) — drivers whose
  // controls live on children (mixer channels, zones) surface them as
  // friendly grouped properties instead of raw live keys.
  const [childData, setChildData] = useState<ChildEntitiesListResponse | null>(null);
  useEffect(() => {
    if (!selectedDevice) {
      setChildData(null);
      return;
    }
    let stale = false;
    api.listChildEntities(selectedDevice)
      .then((r) => {
        if (!stale) setChildData(r);
      })
      .catch(() => {
        if (!stale) setChildData(null);
      });
    return () => {
      stale = true;
    };
  }, [selectedDevice]);

  const schema = useMemo(() => {
    const info = deviceInfo?.driver_info as
      | { state_variables?: Record<string, DeviceStateVarDef> }
      | undefined;
    return info?.state_variables ?? {};
  }, [deviceInfo]);

  // Schema vars in driver declaration order (control-flagged first inside
  // the top group when the driver flags any), then child-entity vars in one
  // group per child type, then live-only keys (from the device state
  // snapshot) so runtime-populated and metadata keys stay reachable without
  // leaving the cascade.
  const { entries, childGroups } = useMemo(() => {
    const result: PropEntry[] = [];
    const hasSchema = Object.keys(schema).length > 0;

    // Device-level vars. A driver that flags control vars is authoritative
    // for ordering inside the match group; unflagged drivers keep the
    // type+name heuristic order. Nothing is ever hidden either way.
    const anyFlagged = Object.values(schema).some((d) => d.control === true);
    const matchFlagged: PropEntry[] = [];
    const matchRest: PropEntry[] = [];
    const nonMatch: PropEntry[] = [];
    for (const [suffix, def] of Object.entries(schema)) {
      const group = METADATA_PATTERN.test(suffix)
        ? "more"
        : varMatchesElement(element.type, def)
          ? "match"
          : "other";
      const entry: PropEntry = { suffix, label: def.label || suffix, def, group };
      if (group === "match" && anyFlagged && def.control === true) {
        matchFlagged.push(entry);
      } else if (group === "match") {
        matchRest.push(entry);
      } else {
        nonMatch.push(entry);
      }
    }
    result.push(...matchFlagged, ...matchRest, ...nonMatch);

    // Child-entity vars: one group per child type ("Inputs", "Mixes", ...),
    // children in registration order, each child's control vars first and
    // its platform/metadata rows (online, label, name) last + dimmed.
    const groups: { id: string; label: string }[] = [];
    const childSuffixes = new Set<string>();
    if (childData) {
      for (const [ctype, tdef] of Object.entries(childData.child_entity_types ?? {})) {
        const kids = childData.children?.[ctype] ?? [];
        if (kids.length === 0) continue;
        const gid = `child:${ctype}`;
        groups.push({ id: gid, label: tdef.label_plural || tdef.label || ctype });
        for (const kid of kids) {
          const defs = childSchemaFor(childData, ctype, kid);
          const kidFlagged = Object.values(defs).some((d) => d?.control === true);
          const childLabel = kid.label || `${tdef.label || ctype} ${kid.local_id}`;
          const preferred: PropEntry[] = [];
          const plain: PropEntry[] = [];
          const demotedRows: PropEntry[] = [];
          for (const [prop, def] of Object.entries(defs)) {
            const suffix = `${ctype}.${kid.local_id_padded}.${prop}`;
            childSuffixes.add(suffix);
            const demoted =
              PLATFORM_CHILD_PROPS.has(prop) || METADATA_PATTERN.test(prop);
            const entry: PropEntry = {
              suffix,
              label: `${childLabel} · ${def?.label || prop}`,
              def: def ?? null,
              group: gid,
              dim: demoted,
            };
            if (demoted) demotedRows.push(entry);
            else if (kidFlagged ? def?.control === true : true) preferred.push(entry);
            else plain.push(entry);
          }
          result.push(...preferred, ...plain, ...demotedRows);
        }
      }
    }

    const liveOnly = Object.keys(deviceInfo?.state ?? {})
      .filter((s) => !(s in schema) && !childSuffixes.has(s))
      .sort();
    for (const suffix of liveOnly) {
      const value = deviceInfo?.state[suffix];
      result.push({
        suffix,
        label: suffix,
        def: null,
        // With a schema, non-schema keys are runtime extras -> metadata group.
        // Without one (disabled/orphaned device) the live keys ARE the list,
        // so judge them by their live value's type instead of burying them.
        group: hasSchema
          ? "more"
          : METADATA_PATTERN.test(suffix)
            ? "more"
            : liveValueMatchesElement(element.type, value)
              ? "match"
              : "other",
      });
    }
    return { entries: result, childGroups: groups };
  }, [schema, deviceInfo, childData, element.type]);

  const selectedEntry = selectedDevice === boundDeviceId
    ? entries.find((e) => e.suffix === boundSuffix)
    : undefined;

  const boundDef =
    keyValue && selectedDevice === boundDeviceId
      ? schema[boundSuffix] ??
        childVarDefForSuffix(childData, boundSuffix) ??
        undefined
      : undefined;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      <div>
        <label style={labelStyle}>Device</label>
        <select
          value={selectedDevice}
          onChange={(e) => setDeviceOverride(e.target.value)}
          style={{ width: "100%", padding: "var(--space-xs) var(--space-sm)", fontSize: "var(--font-size-sm)" }}
        >
          <option value="">Select device...</option>
          {project.devices.map((d) => {
            const connected = useConnectionStore.getState().liveState[`device.${d.id}.connected`];
            return (
              <option key={d.id} value={d.id}>
                {connected ? "● " : "○ "}{d.name} ({d.driver})
              </option>
            );
          })}
        </select>
      </div>

      {selectedDevice && (
        <div>
          <label style={labelStyle}>Property</label>
          <PropertyDropdown
            entries={entries}
            childGroups={childGroups}
            elementType={element.type}
            selectedSuffix={selectedEntry?.suffix ?? (selectedDevice === boundDeviceId ? boundSuffix : "")}
            selectedLabel={selectedEntry?.label}
            deviceId={selectedDevice}
            onPick={(suffix) => onKeyChange(`device.${selectedDevice}.${suffix}`)}
          />
          {boundDef?.help && (
            <div style={{ ...helpBoxStyle, marginTop: "var(--space-xs)" }}>
              <Info size={13} style={{ flexShrink: 0, marginTop: "var(--space-2xs)", color: "var(--accent)" }} />
              {boundDef.help}
            </div>
          )}
        </div>
      )}

      {boundDef && (
        <RangeMatchPrompt
          key={keyValue}
          element={element}
          varDef={boundDef}
          onElementPatch={onElementPatch}
        />
      )}
    </div>
  );
}

// --- Property dropdown (searchable, grouped by fit for the element) ---

function PropertyDropdown({
  entries,
  childGroups,
  elementType,
  selectedSuffix,
  selectedLabel,
  deviceId,
  onPick,
}: {
  entries: PropEntry[];
  childGroups: { id: string; label: string }[];
  elementType: string;
  selectedSuffix: string;
  selectedLabel?: string;
  deviceId: string;
  onPick: (suffix: string) => void;
}) {
  const displayText = selectedSuffix
    ? selectedLabel || selectedSuffix
    : "Select property...";

  return (
    <SearchableDropdown
      display={displayText}
      empty={!selectedSuffix}
      searchPlaceholder="Search properties..."
    >
      {({ search, close }) => {
        const q = search.toLowerCase();
        const filtered = search
          ? entries.filter(
              (e) => e.suffix.toLowerCase().includes(q) || e.label.toLowerCase().includes(q),
            )
          : entries;

        const groups: { id: string; label: string; desc: string; items: PropEntry[] }[] = [
          { id: "match", label: matchGroupLabel(elementType), desc: "", items: [] },
          ...childGroups.map((g) => ({ id: g.id, label: g.label, desc: "", items: [] as PropEntry[] })),
          { id: "other", label: "Other properties", desc: "", items: [] },
          { id: "more", label: "Status & metadata", desc: "Read-outs and device info", items: [] },
        ];
        for (const e of filtered) groups.find((g) => g.id === e.group)?.items.push(e);

        const liveState = useConnectionStore.getState().liveState;

        return (
          <>
            {entries.length === 0 && (
              <div style={dropdownEmptyHintStyle}>
                No state reported by this device yet. Use &ldquo;Pick any state
                key&rdquo; below, or start the system.
              </div>
            )}
            {entries.length > 0 && filtered.length === 0 && (
              <div style={dropdownEmptyHintStyle}>No properties matching &ldquo;{search}&rdquo;</div>
            )}
            {groups.filter((g) => g.items.length > 0).map((g) => (
              <div key={g.id}>
                <div style={dropdownGroupHeaderStyle}>
                  <span style={{ fontWeight: "var(--font-weight-semibold)" }}>{g.label}</span>
                  {g.desc && (
                    <span style={{ fontWeight: "var(--font-weight-normal)", fontStyle: "italic", marginLeft: "var(--space-sm)" }}>{g.desc}</span>
                  )}
                </div>
                {g.items.map((entry) => {
                  const live = liveState[`device.${deviceId}.${entry.suffix}`];
                  const dimmed = entry.group === "more" || entry.dim === true;
                  return (
                    <div
                      key={entry.suffix}
                      onClick={() => {
                        onPick(entry.suffix);
                        close();
                      }}
                      style={{
                        ...rowStyle,
                        opacity: dimmed ? 0.75 : 1,
                        background: entry.suffix === selectedSuffix ? "var(--bg-hover)" : undefined,
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                      onMouseLeave={(e) =>
                        (e.currentTarget.style.background =
                          entry.suffix === selectedSuffix ? "var(--bg-hover)" : "transparent")
                      }
                    >
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                          <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-primary)" }}>
                            {entry.label}
                          </span>
                          {entry.def?.type && <span style={dropdownTypeBadgeStyle}>{entry.def.type}</span>}
                        </div>
                        {entry.label !== entry.suffix && (
                          <div style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                            {entry.suffix}
                          </div>
                        )}
                      </div>
                      {live !== undefined && (
                        <span
                          style={{
                            fontSize: "var(--font-size-xs)",
                            color: "var(--text-muted)",
                            flexShrink: 0,
                            maxWidth: 110,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                            fontFamily: "var(--font-mono)",
                          }}
                          title={String(live)}
                        >
                          {String(live)}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            ))}
          </>
        );
      }}
    </SearchableDropdown>
  );
}

// --- "Match this control to the driver's range?" prompt ---

const RANGE_ELEMENTS = new Set(["slider", "fader", "gauge", "level_meter"]);
const STEP_ELEMENTS = new Set(["slider", "fader"]);
const UNIT_ELEMENTS = new Set(["slider", "fader", "gauge"]);

/** Pull a unit out of a label like "Input 1 Gain (dB)" — the fallback for
 *  drivers that don't declare a `unit` field on the state variable. */
function parseUnitFromLabel(label: string | undefined): string | undefined {
  const m = /\(([^()]+)\)\s*$/.exec(label ?? "");
  const candidate = m?.[1]?.trim() ?? "";
  return /^[A-Za-z%°]{1,5}$/.test(candidate) ? candidate : undefined;
}

/** What matching the control to the driver would set, and whether anything
 *  actually differs. Null when the element has no range fields or the var
 *  declares no numeric range. */
export function driverRangeTarget(
  element: UIElement,
  varDef: DeviceStateVarDef,
): { min: number; max: number; step?: number; unit?: string; differs: boolean } | null {
  if (!RANGE_ELEMENTS.has(element.type)) return null;
  if (typeof varDef.min !== "number" || typeof varDef.max !== "number") return null;
  const unit = UNIT_ELEMENTS.has(element.type)
    ? varDef.unit || parseUnitFromLabel(varDef.label)
    : undefined;
  const step = STEP_ELEMENTS.has(element.type) ? varDef.step : undefined;
  const differs =
    element.min !== varDef.min ||
    element.max !== varDef.max ||
    (step !== undefined && element.step !== step) ||
    (unit !== undefined && element.unit !== unit);
  return { min: varDef.min, max: varDef.max, step, unit, differs };
}

/** Element types that SEND what they read.
 *
 *  A gauge or a level meter scaled past what the device reports is a needle
 *  that never reaches the end of its sweep -- untidy, and a legitimate choice
 *  for a scale shared across channels. A fader scaled past it hands the device
 *  a value it refuses. Mirrors COMMANDING_TYPES in openavc/ui/page_review.py,
 *  which warns the AI about the same thing. */
const COMMANDING_ELEMENTS = new Set(["slider", "fader"]);

/**
 * The ends of a control's travel that command a value the device will refuse.
 *
 * Empty when the control sits inside what the driver declares. A NARROWER range
 * is deliberately not flagged -- a volume-limited install is real authoring, and
 * the platform has output_min / output_max / scale_to_full for adjacent
 * purposes. Only wider is a defect, and only on a control that commands.
 */
export function driverRangeOverreach(
  element: UIElement,
  varDef: DeviceStateVarDef,
): string[] {
  if (!COMMANDING_ELEMENTS.has(element.type)) return [];
  const over: string[] = [];
  if (typeof varDef.min === "number" && typeof element.min === "number" && element.min < varDef.min) {
    over.push(`its Min of ${element.min} is below the ${varDef.min} the driver declares`);
  }
  if (typeof varDef.max === "number" && typeof element.max === "number" && element.max > varDef.max) {
    over.push(`its Max of ${element.max} is above the ${varDef.max} the driver declares`);
  }
  return over;
}

function applyDriverRange(
  target: { min: number; max: number; step?: number; unit?: string },
  onElementPatch: (patch: Partial<UIElement>) => void,
) {
  const patch: Partial<UIElement> = { min: target.min, max: target.max };
  if (target.step !== undefined) patch.step = target.step;
  if (target.unit !== undefined) patch.unit = target.unit;
  onElementPatch(patch);
}

function RangeMatchPrompt({
  element,
  varDef,
  onElementPatch,
}: {
  element: UIElement;
  varDef: DeviceStateVarDef;
  onElementPatch: (patch: Partial<UIElement>) => void;
}) {
  const [dismissed, setDismissed] = useState(false);

  const target = driverRangeTarget(element, varDef);
  const overreach = driverRangeOverreach(element, varDef);
  if (dismissed || !target || !target.differs) return null;

  return (
    <div style={helpBoxStyle}>
      <Info size={13} style={{ flexShrink: 0, marginTop: "var(--space-2xs)", color: "var(--accent)" }} />
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
        <span>
          This value has a defined range of {target.min} to {target.max}
          {target.unit ? ` ${target.unit}` : ""}. Match this {element.type.replace(/_/g, " ")} to it?
        </span>
        {overreach.length > 0 && (
          <span style={overreachStyle}>
            Right now {overreach.join(" and ")}, so that end of its travel commands a
            value the device refuses.
          </span>
        )}
        <div style={{ display: "flex", gap: "var(--space-sm)" }}>
          <button type="button" onClick={() => applyDriverRange(target, onElementPatch)} style={applyBtnStyle}>
            Match range
          </button>
          <button type="button" onClick={() => setDismissed(true)} style={dismissBtnStyle}>Dismiss</button>
        </div>
      </div>
    </div>
  );
}

/** Compact "Match driver range" affordance for the Basic section — visible
 *  while the element's Value is bound to a device property with a declared
 *  range and the element's numbers differ from it. The Bindings-card prompt
 *  covers the moment of binding; this covers later edits to Min/Max without
 *  a trip back into Bindings. */
export function MatchDriverRangeRow({
  element,
  onElementPatch,
}: {
  element: UIElement;
  onElementPatch: (patch: Partial<UIElement>) => void;
}) {
  const bindings = element.bindings as { show?: { value?: { key?: string } } } | undefined;
  const key = String(bindings?.show?.value?.key || "");
  const parts = key.startsWith("device.") ? key.split(".") : [];
  const deviceId = parts[1] ?? "";
  const suffix = parts.slice(2).join(".");
  const [varDef, setVarDef] = useState<DeviceStateVarDef | null>(null);

  useEffect(() => {
    let stale = false;
    if (!deviceId || !suffix) {
      setVarDef(null);
      return;
    }
    fetchBoundVarDef(deviceId, suffix)
      .then((def) => {
        if (!stale) setVarDef(def);
      })
      .catch(() => {
        if (!stale) setVarDef(null);
      });
    return () => {
      stale = true;
    };
  }, [deviceId, suffix]);

  const target = varDef ? driverRangeTarget(element, varDef) : null;
  const overreach = varDef ? driverRangeOverreach(element, varDef) : [];
  if (!target || !target.differs) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
      {overreach.length > 0 && (
        <div style={overreachStyle}>
          This control commands the device, and {overreach.join(" and ")}. That end of
          its travel sends a value the device refuses.
        </div>
      )}
      <button
        type="button"
        onClick={() => applyDriverRange(target, onElementPatch)}
        title="Set Min/Max (and Step/Unit when the driver declares them) from the bound device property"
        style={matchRowBtnStyle}
      >
        Match driver range ({target.min} to {target.max}
        {target.unit ? ` ${target.unit}` : ""})
      </button>
    </div>
  );
}

/** A range that reaches past the device, which the match button then fixes. */
const overreachStyle: React.CSSProperties = {
  fontSize: "var(--font-size-xs)",
  padding: "var(--space-xs) var(--space-sm)",
  borderRadius: "var(--border-radius)",
  background: "var(--color-warning-bg)",
  border: "1px solid rgba(255,152,0,0.4)",
  color: "var(--text-primary)",
};

// --- Styles ---

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: "var(--font-size-xs)",
  color: "var(--text-muted)",
  marginBottom: "var(--space-2xs)",
};

/** The shared dropdown row, plus the one thing this list wants that the
 *  state-key list does not: a gap between the label block and the live value. */
const rowStyle: React.CSSProperties = { ...dropdownRowStyle, gap: "var(--space-sm)" };

const helpBoxStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: "var(--space-sm)",
  padding: "var(--space-sm)",
  borderRadius: "var(--border-radius)",
  background: "var(--accent-dim)",
  border: "1px solid rgba(138,180,147,0.15)",
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  lineHeight: "var(--line-tight)",
};

const applyBtnStyle: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-md)",
  borderRadius: "var(--border-radius)",
  background: "var(--accent-bg)",
  color: "var(--text-on-accent)",
  fontSize: "var(--font-size-xs)",
  border: "none",
  cursor: "pointer",
};

const dismissBtnStyle: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-md)",
  borderRadius: "var(--border-radius)",
  background: "transparent",
  color: "var(--text-muted)",
  fontSize: "var(--font-size-xs)",
  border: "1px solid var(--border-color)",
  cursor: "pointer",
};

const matchRowBtnStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  width: "100%",
  padding: "var(--space-xs) var(--space-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px dashed var(--accent)",
  background: "var(--accent-dim)",
  color: "var(--accent)",
  fontSize: "var(--font-size-xs)",
  cursor: "pointer",
};
