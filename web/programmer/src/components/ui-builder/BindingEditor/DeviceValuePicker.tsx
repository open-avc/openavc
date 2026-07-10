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
import { useState, useRef, useEffect, useMemo } from "react";
import { ChevronDown, Info } from "lucide-react";
import type { ProjectConfig, DeviceInfo, UIElement } from "../../../api/types";
import { useConnectionStore } from "../../../store/connectionStore";
import * as api from "../../../api/restClient";

/** Shape of one entry in DRIVER_INFO.state_variables (per-device, from
 *  getDevice — instance-building drivers only populate it there). */
export interface DeviceStateVarDef {
  type?: string;
  label?: string;
  values?: string[];
  help?: string;
  min?: number;
  max?: number;
  step?: number;
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
  group: "match" | "other" | "more";
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
    api.getDevice(selectedDevice).then(setDeviceInfo).catch(() => setDeviceInfo(null));
  }, [selectedDevice]);

  const schema = useMemo(() => {
    const info = deviceInfo?.driver_info as
      | { state_variables?: Record<string, DeviceStateVarDef> }
      | undefined;
    return info?.state_variables ?? {};
  }, [deviceInfo]);

  // Schema vars in driver declaration order, then live-only keys (from the
  // device state snapshot) so runtime-populated and metadata keys stay
  // reachable without leaving the cascade.
  const entries = useMemo((): PropEntry[] => {
    const result: PropEntry[] = [];
    const hasSchema = Object.keys(schema).length > 0;
    for (const [suffix, def] of Object.entries(schema)) {
      result.push({
        suffix,
        label: def.label || suffix,
        def,
        group: METADATA_PATTERN.test(suffix)
          ? "more"
          : varMatchesElement(element.type, def)
            ? "match"
            : "other",
      });
    }
    const liveOnly = Object.keys(deviceInfo?.state ?? {})
      .filter((s) => !(s in schema))
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
    return result;
  }, [schema, deviceInfo, element.type]);

  const selectedEntry = selectedDevice === boundDeviceId
    ? entries.find((e) => e.suffix === boundSuffix)
    : undefined;

  const boundDef =
    keyValue && selectedDevice === boundDeviceId ? schema[boundSuffix] : undefined;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      <div>
        <label style={labelStyle}>Device</label>
        <select
          value={selectedDevice}
          onChange={(e) => setDeviceOverride(e.target.value)}
          style={{ width: "100%", padding: "4px 6px", fontSize: "var(--font-size-sm)" }}
        >
          <option value="">Select device...</option>
          {project.devices.map((d) => {
            const connected = useConnectionStore.getState().liveState[`device.${d.id}.connected`];
            return (
              <option key={d.id} value={d.id}>
                {connected ? "● " : "○ "}{d.name} — {d.driver}
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
            elementType={element.type}
            selectedSuffix={selectedEntry?.suffix ?? (selectedDevice === boundDeviceId ? boundSuffix : "")}
            selectedLabel={selectedEntry?.label}
            deviceId={selectedDevice}
            onPick={(suffix) => onKeyChange(`device.${selectedDevice}.${suffix}`)}
          />
          {boundDef?.help && (
            <div style={{ ...helpBoxStyle, marginTop: 4 }}>
              <Info size={13} style={{ flexShrink: 0, marginTop: 1, color: "var(--accent)" }} />
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
  elementType,
  selectedSuffix,
  selectedLabel,
  deviceId,
  onPick,
}: {
  entries: PropEntry[];
  elementType: string;
  selectedSuffix: string;
  selectedLabel?: string;
  deviceId: string;
  onPick: (suffix: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [dropdownPos, setDropdownPos] = useState({ top: 0, left: 0, width: 0, flipUp: false });

  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        setSearch("");
      }
    };
    const handleScroll = (e: Event) => {
      if (containerRef.current && containerRef.current.contains(e.target as Node)) return;
      setOpen(false);
      setSearch("");
    };
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("scroll", handleScroll, true);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("scroll", handleScroll, true);
    };
  }, [open]);

  useEffect(() => {
    if (open && searchRef.current) searchRef.current.focus();
  }, [open]);

  const filtered = useMemo(() => {
    if (!search) return entries;
    const q = search.toLowerCase();
    return entries.filter(
      (e) => e.suffix.toLowerCase().includes(q) || e.label.toLowerCase().includes(q),
    );
  }, [entries, search]);

  const groups: { id: PropEntry["group"]; label: string; desc: string; items: PropEntry[] }[] = [
    { id: "match", label: matchGroupLabel(elementType), desc: "", items: [] },
    { id: "other", label: "Other properties", desc: "", items: [] },
    { id: "more", label: "Status & metadata", desc: "Read-outs and device info", items: [] },
  ];
  for (const e of filtered) groups.find((g) => g.id === e.group)!.items.push(e);

  const displayText = selectedSuffix
    ? selectedLabel || selectedSuffix
    : "Select property...";

  return (
    <div ref={containerRef} style={{ position: "relative" }}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => {
          if (!open && triggerRef.current) {
            const rect = triggerRef.current.getBoundingClientRect();
            const spaceBelow = window.innerHeight - rect.bottom;
            const flipUp = spaceBelow < 250 && rect.top > spaceBelow;
            // Clamp into the viewport — the trigger sits in the narrow
            // right-docked properties pane while the dropdown is 320px wide.
            const width = Math.max(rect.width, 320);
            const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
            setDropdownPos({ top: rect.bottom + 2, left, width, flipUp });
          }
          setOpen(!open);
        }}
        style={{
          ...triggerStyle,
          color: selectedSuffix ? "var(--text-primary)" : "var(--text-muted)",
        }}
      >
        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", textAlign: "left" }}>
          {displayText}
        </span>
        <ChevronDown size={14} style={{ flexShrink: 0, opacity: 0.5 }} />
      </button>

      {open && (() => {
        const rect = triggerRef.current?.getBoundingClientRect();
        const triggerBottom = rect?.bottom ?? dropdownPos.top;
        const triggerTop = rect?.top ?? dropdownPos.top;
        const top = dropdownPos.flipUp ? undefined : triggerBottom + 2;
        const bottom = dropdownPos.flipUp ? window.innerHeight - triggerTop + 2 : undefined;
        const maxH = dropdownPos.flipUp ? triggerTop - 16 : window.innerHeight - triggerBottom - 16;
        const liveState = useConnectionStore.getState().liveState;
        return (
          <div
            style={{
              position: "fixed",
              top,
              bottom,
              left: dropdownPos.left,
              width: dropdownPos.width,
              maxHeight: Math.max(200, maxH),
              display: "flex",
              flexDirection: "column",
              background: "var(--bg-elevated)",
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              boxShadow: "var(--shadow-lg)",
              zIndex: 9999,
            }}
          >
            <div style={{ padding: "6px 8px", borderBottom: "1px solid var(--border-color)" }}>
              <input
                ref={searchRef}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search properties..."
                style={searchInputStyle}
              />
            </div>
            <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
              {entries.length === 0 && (
                <div style={emptyHintStyle}>
                  No state reported by this device yet. Use &ldquo;Pick any state
                  key&rdquo; below, or start the system.
                </div>
              )}
              {entries.length > 0 && filtered.length === 0 && (
                <div style={emptyHintStyle}>No properties matching &ldquo;{search}&rdquo;</div>
              )}
              {groups.filter((g) => g.items.length > 0).map((g) => (
                <div key={g.id}>
                  <div style={groupHeaderStyle}>
                    <span style={{ fontWeight: 600 }}>{g.label}</span>
                    {g.desc && (
                      <span style={{ fontWeight: 400, fontStyle: "italic", marginLeft: 6 }}>{g.desc}</span>
                    )}
                  </div>
                  {g.items.map((entry) => {
                    const live = liveState[`device.${deviceId}.${entry.suffix}`];
                    const dimmed = entry.group === "more";
                    return (
                      <div
                        key={entry.suffix}
                        onClick={() => {
                          onPick(entry.suffix);
                          setOpen(false);
                          setSearch("");
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
                          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                            <span style={{ fontSize: 12, color: "var(--text-primary)" }}>
                              {entry.label}
                            </span>
                            {entry.def?.type && <span style={typeBadgeStyle}>{entry.def.type}</span>}
                          </div>
                          {entry.label !== entry.suffix && (
                            <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                              {entry.suffix}
                            </div>
                          )}
                        </div>
                        {live !== undefined && (
                          <span
                            style={{
                              fontSize: 11,
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
            </div>
          </div>
        );
      })()}
    </div>
  );
}

// --- "Match this control to the driver's range?" prompt ---

const RANGE_ELEMENTS = new Set(["slider", "fader", "gauge", "level_meter"]);
const STEP_ELEMENTS = new Set(["slider", "fader"]);
const UNIT_ELEMENTS = new Set(["slider", "fader", "gauge"]);

/** Pull a unit out of a label like "Input 1 Gain (dB)" — the state-var
 *  schema has no unit field, so a short parenthesized trailer is the best
 *  signal a driver gives today. */
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
  const unit = UNIT_ELEMENTS.has(element.type) ? parseUnitFromLabel(varDef.label) : undefined;
  const step = STEP_ELEMENTS.has(element.type) ? varDef.step : undefined;
  const differs =
    element.min !== varDef.min ||
    element.max !== varDef.max ||
    (step !== undefined && element.step !== step) ||
    (unit !== undefined && element.unit !== unit);
  return { min: varDef.min, max: varDef.max, step, unit, differs };
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
  if (dismissed || !target || !target.differs) return null;

  return (
    <div style={helpBoxStyle}>
      <Info size={13} style={{ flexShrink: 0, marginTop: 1, color: "var(--accent)" }} />
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span>
          This value has a defined range of {target.min} to {target.max}
          {target.unit ? ` ${target.unit}` : ""}. Match this {element.type.replace(/_/g, " ")} to it?
        </span>
        <div style={{ display: "flex", gap: 6 }}>
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
    api.getDevice(deviceId)
      .then((info) => {
        if (stale) return;
        const vars = (info.driver_info as { state_variables?: Record<string, DeviceStateVarDef> } | undefined)
          ?.state_variables;
        setVarDef(vars?.[suffix] ?? null);
      })
      .catch(() => {
        if (!stale) setVarDef(null);
      });
    return () => {
      stale = true;
    };
  }, [deviceId, suffix]);

  const target = varDef ? driverRangeTarget(element, varDef) : null;
  if (!target || !target.differs) return null;

  return (
    <button
      type="button"
      onClick={() => applyDriverRange(target, onElementPatch)}
      title="Set Min/Max (and Step/Unit when the driver declares them) from the bound device property"
      style={matchRowBtnStyle}
    >
      Match driver range ({target.min} to {target.max}
      {target.unit ? ` ${target.unit}` : ""})
    </button>
  );
}

// --- Styles ---

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  color: "var(--text-muted)",
  marginBottom: 2,
};

const triggerStyle: React.CSSProperties = {
  width: "100%",
  padding: "4px 8px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
  gap: 4,
};

const searchInputStyle: React.CSSProperties = {
  width: "100%",
  padding: "4px 6px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
};

const groupHeaderStyle: React.CSSProperties = {
  padding: "6px 8px 2px",
  fontSize: 11,
  color: "var(--text-muted)",
  display: "flex",
  alignItems: "baseline",
  flexWrap: "wrap",
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  padding: "4px 8px 4px 16px",
  cursor: "pointer",
  fontSize: "var(--font-size-sm)",
  transition: "background 0.1s",
};

const typeBadgeStyle: React.CSSProperties = {
  fontSize: 10,
  padding: "0 4px",
  borderRadius: 3,
  background: "var(--bg-hover)",
  color: "var(--text-muted)",
};

const emptyHintStyle: React.CSSProperties = {
  padding: "12px 8px",
  fontSize: 12,
  color: "var(--text-muted)",
  fontStyle: "italic",
  textAlign: "center",
};

const helpBoxStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: 6,
  padding: "6px 8px",
  borderRadius: 4,
  background: "rgba(138,180,147,0.08)",
  border: "1px solid rgba(138,180,147,0.15)",
  fontSize: 12,
  color: "var(--text-secondary)",
  lineHeight: 1.4,
};

const applyBtnStyle: React.CSSProperties = {
  padding: "3px 10px",
  borderRadius: "var(--border-radius)",
  background: "var(--accent-bg)",
  color: "#fff",
  fontSize: 11,
  border: "none",
  cursor: "pointer",
};

const dismissBtnStyle: React.CSSProperties = {
  padding: "3px 10px",
  borderRadius: "var(--border-radius)",
  background: "transparent",
  color: "var(--text-muted)",
  fontSize: 11,
  border: "1px solid var(--border-color)",
  cursor: "pointer",
};

const matchRowBtnStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  width: "100%",
  padding: "4px 8px",
  borderRadius: "var(--border-radius)",
  border: "1px dashed var(--accent)",
  background: "rgba(138,180,147,0.08)",
  color: "var(--accent)",
  fontSize: 11,
  cursor: "pointer",
};
