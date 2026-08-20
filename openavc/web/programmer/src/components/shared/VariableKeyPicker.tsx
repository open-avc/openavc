/**
 * Searchable state key picker with grouped dropdown, live values, and inline variable creation.
 * Used in macro editors, UI Builder binding editors, and anywhere a state key is selected.
 */
import { useState, useEffect, useMemo } from "react";
import { Plus } from "lucide-react";
import type { VariableConfig } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { useConnectionStore } from "../../store/connectionStore";
import { CopyButton } from "./CopyButton";
import { groupLabel } from "./variableKeyPickerHelpers";
import { showError } from "../../store/toastStore";
import { getDevice, listChildEntities } from "../../api/restClient";
import {
  SearchableDropdown,
  dropdownRowStyle,
  dropdownGroupHeaderStyle,
  dropdownTypeBadgeStyle,
  dropdownEmptyHintStyle,
} from "./SearchableDropdown";

/** Session cache of device state-variable labels (deviceId -> suffix ->
 *  friendly label), filled lazily the first time a picker opens. Display-only:
 *  a missing or stale entry just means the row shows the raw suffix. Uses the
 *  per-device endpoints because instance-building drivers only populate
 *  state_variables on the live instance, not at class level. Child-entity
 *  keys (input.01.fader_db) resolve to "<child> · <var>" labels from the
 *  children payload's schemas. */
const deviceVarLabelCache = new Map<string, Record<string, string>>();
const labelFetchInflight = new Map<string, Promise<void>>();

function fetchDeviceVarLabels(deviceId: string): Promise<void> {
  if (deviceVarLabelCache.has(deviceId)) return Promise.resolve();
  let p = labelFetchInflight.get(deviceId);
  if (!p) {
    p = Promise.all([
      getDevice(deviceId),
      listChildEntities(deviceId).catch(() => null),
    ])
      .then(([info, kids]) => {
        const vars =
          (info.driver_info as { state_variables?: Record<string, { label?: string }> } | undefined)
            ?.state_variables ?? {};
        const labels: Record<string, string> = {};
        for (const [k, v] of Object.entries(vars)) {
          if (v?.label) labels[k] = v.label;
        }
        if (kids) {
          for (const [ctype, tdef] of Object.entries(kids.child_entity_types ?? {})) {
            for (const child of kids.children?.[ctype] ?? []) {
              const schema = child.schema ?? tdef.state_variables ?? {};
              const childLabel =
                child.label || `${tdef.label || ctype} ${child.local_id}`;
              for (const [prop, def] of Object.entries(schema)) {
                labels[`${ctype}.${child.local_id_padded}.${prop}`] =
                  `${childLabel} · ${def?.label || prop}`;
              }
            }
          }
        }
        deviceVarLabelCache.set(deviceId, labels);
      })
      .catch(() => {
        deviceVarLabelCache.set(deviceId, {});
      })
      .finally(() => {
        labelFetchInflight.delete(deviceId);
      });
    labelFetchInflight.set(deviceId, p);
  }
  return p;
}

interface VariableKeyPickerProps {
  value: string;
  onChange: (key: string) => void;
  /** Show device state keys in addition to project variables */
  showDeviceState?: boolean;
  /** Also offer $trigger.<field> refs (the event payload / state-change
   *  snapshot of the trigger that fired the macro). Only meaningful in macro
   *  step/condition editors, where the macro may be run by a trigger. */
  showTriggerContext?: boolean;
  /** UI-event tokens this binding can deliver ($value/$input/$output/$mute),
   *  scoped to the binding slot. When non-empty, they appear as a top "This
   *  control" group. Only meaningful on UI Builder bindings, where a UI press
   *  carries an event value. The stored value is the bare token (e.g. "value"),
   *  resolved against the event context at runtime. */
  eventContext?: { key: string; label: string }[];
  /** Placeholder text */
  placeholder?: string;
  /** Style override for the outer container */
  style?: React.CSSProperties;
}

/** Stable empty default for eventContext, so callers that omit it keep a
 *  constant reference (a fresh `[]` per render would defeat the allEntries
 *  memo for every existing caller). */
const NO_EVENT_CONTEXT: { key: string; label: string }[] = [];

/** $trigger.<field> refs a macro can read when it is fired by a trigger. The
 *  available fields depend on the trigger type (event vs state-change); the
 *  picker shows the full set and the group note explains the constraint. */
const TRIGGER_CONTEXT_KEYS: { key: string; label: string }[] = [
  { key: "trigger.event", label: "event: the event name (event triggers)" },
  { key: "trigger.data", label: "data: received/parsed payload (event triggers)" },
  { key: "trigger.raw", label: "raw: raw bytes payload (event triggers)" },
  { key: "trigger.new_value", label: "new_value: the new value (state-change triggers)" },
  { key: "trigger.old_value", label: "old_value: the previous value (state-change triggers)" },
  { key: "trigger.key", label: "key: the state key that changed (state-change triggers)" },
];

interface KeyEntry {
  key: string;
  label: string;
  type?: string;
  group: string;
  groupDesc: string;
  deviceName?: string;
  description?: string;
}

/** Match on the raw key, the friendly label, or the owning device's name. */
function filterEntries(entries: KeyEntry[], search: string): KeyEntry[] {
  if (!search) return entries;
  const q = search.toLowerCase();
  return entries.filter(
    (e) =>
      e.key.toLowerCase().includes(q) ||
      e.label.toLowerCase().includes(q) ||
      (e.deviceName && e.deviceName.toLowerCase().includes(q)),
  );
}

function groupEntries(entries: KeyEntry[]) {
  const map = new Map<string, { label: string; desc: string; entries: KeyEntry[] }>();
  for (const e of entries) {
    if (!map.has(e.group)) {
      map.set(e.group, {
        label: groupLabel(e.group, e.deviceName),
        desc: e.groupDesc,
        entries: [],
      });
    }
    map.get(e.group)!.entries.push(e);
  }
  return map;
}

export function VariableKeyPicker({
  value,
  onChange,
  showDeviceState = true,
  showTriggerContext = false,
  eventContext = NO_EVENT_CONTEXT,
  placeholder = "Select state key...",
  style,
}: VariableKeyPickerProps) {
  const projectVariables = useProjectStore((s) => s.project?.variables);
  const projectDevices = useProjectStore((s) => s.project?.devices);
  const projectPages = useProjectStore((s) => s.project?.ui?.pages);
  const storeUpdate = useProjectStore((s) => s.update);
  const liveState = useConnectionStore((s) => s.liveState);

  const variables = projectVariables ?? [];
  const devices = projectDevices ?? [];
  const pages = projectPages ?? [];

  const [showCreate, setShowCreate] = useState(false);
  const [newId, setNewId] = useState("");
  const [newType, setNewType] = useState("string");
  const [newLabel, setNewLabel] = useState("");
  const [newDefault, setNewDefault] = useState("");
  // Mirrors the dropdown's own open flag. The shell owns opening and closing;
  // this picker only needs to know, so the lazy label fetch below can wait for
  // the first time someone actually looks at the list.
  const [open, setOpen] = useState(false);

  // Resolve friendly device state-variable labels (lazily, first open only per
  // device — see deviceVarLabelCache). The version bump re-runs the entries
  // memo once labels arrive; rows fall back to the raw suffix meanwhile.
  const [labelsVersion, setLabelsVersion] = useState(0);
  useEffect(() => {
    if (!open || !showDeviceState || devices.length === 0) return;
    let stale = false;
    Promise.all(devices.map((d) => fetchDeviceVarLabels(d.id))).then(() => {
      if (!stale) setLabelsVersion((v) => v + 1);
    });
    return () => {
      stale = true;
    };
  }, [open, showDeviceState, projectDevices]);

  // Build grouped entries
  const allEntries = useMemo((): KeyEntry[] => {
    const entries: KeyEntry[] = [];

    // "This control" — the UI-event tokens this binding slot can deliver
    // ($value/$input/$output/$mute). Rendered first so the common case (the
    // touched value) is at the top. No live value: the value arrives with the
    // press at runtime.
    for (const t of eventContext) {
      entries.push({
        key: t.key,
        label: t.label,
        group: "control",
        groupDesc: "The value the user just touched on this control.",
      });
    }

    // Project Variables
    for (const v of variables) {
      entries.push({
        key: `var.${v.id}`,
        label: v.label || v.id,
        type: v.type,
        group: "variables",
        groupDesc: "Values you define for your program logic",
        description: v.description,
      });
    }

    // Live state keys — group by prefix
    if (showDeviceState) {
      // Build device name lookup from project
      const deviceNames: Record<string, string> = {};
      for (const d of devices) {
        deviceNames[d.id] = d.name;
      }

      // Build page/element lookup for UI keys
      const uiElements = new Set<string>();
      const pageNames: Record<string, string> = {};
      for (const page of pages) {
        for (const el of page.elements ?? []) {
          uiElements.add(el.id);
          pageNames[el.id] = page.name;
        }
      }

      for (const k of Object.keys(liveState)) {
        if (k.startsWith("device.")) {
          const parts = k.split(".");
          const deviceId = parts[1] ?? "";
          const suffix = parts.slice(2).join(".");
          entries.push({
            key: k,
            // Friendly driver label when resolved; the full raw key still
            // renders under it, so nothing is hidden.
            label: deviceVarLabelCache.get(deviceId)?.[suffix] || suffix,
            group: `device:${deviceId}`,
            groupDesc: "Live hardware state reported by this device",
            deviceName: deviceNames[deviceId] || deviceId,
          });
        } else if (k.startsWith("system.")) {
          entries.push({
            key: k,
            label: k.slice(7),
            group: "system",
            groupDesc: "System-level values (uptime, status)",
          });
        } else if (k.startsWith("plugin.")) {
          const parts = k.split(".");
          const pluginId = parts[1] ?? "";
          entries.push({
            key: k,
            label: parts.slice(2).join("."),
            group: `plugin:${pluginId}`,
            groupDesc: "State from a running plugin",
            deviceName: pluginId,
          });
        } else if (k.startsWith("ui.")) {
          const parts = k.split(".");
          const elId = parts[1] ?? "";
          entries.push({
            key: k,
            label: parts.slice(1).join("."),
            group: uiElements.has(elId) ? `ui:${elId}` : "ui",
            groupDesc: "Override element appearance from macros or scripts",
            deviceName: pageNames[elId] || "",
          });
        }
      }
    }

    // Trigger context refs ($trigger.<field>) — only where a value may be run
    // by a trigger (macro step / condition editors), gated by the prop.
    if (showTriggerContext) {
      for (const t of TRIGGER_CONTEXT_KEYS) {
        entries.push({
          key: t.key,
          label: t.label,
          group: "trigger",
          groupDesc:
            "The event or state change that fired this macro. Only resolves when the macro is run by a trigger.",
        });
      }
    }

    return entries;
    // labelsVersion re-runs this once lazily-fetched device labels land.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectVariables, projectDevices, projectPages, liveState, showDeviceState, showTriggerContext, eventContext, labelsVersion]);

  // Display text for collapsed state
  const selectedEntry = allEntries.find((e) => e.key === value);
  const displayText = selectedEntry
    ? selectedEntry.key
    : value || placeholder;
  const liveValue = value ? liveState[value] : undefined;

  const handleSelect = (key: string, close: () => void) => {
    onChange(key);
    close();
  };

  const handleCreateVariable = (close: () => void) => {
    const id = newId.trim().replace(/[^a-zA-Z0-9_]/g, "_");
    if (!id) return;
    if (variables.some((v) => v.id === id)) {
      showError(`Variable "${id}" already exists.`);
      return;
    }
    let defVal: unknown = newDefault;
    if (newType === "boolean") defVal = newDefault === "true";
    else if (newType === "number") defVal = Number(newDefault) || 0;

    const newVar: VariableConfig = {
      id,
      type: newType,
      default: defVal,
      label: newLabel.trim() || id,
    };
    storeUpdate({ variables: [...variables, newVar] });
    onChange(`var.${id}`);
    setNewId("");
    setNewType("string");
    setNewLabel("");
    setNewDefault("");
    close();
    useProjectStore.getState().debouncedSave();
  };

  const hasLiveData = Object.keys(liveState).length > 0;

  return (
    <SearchableDropdown
      display={
        <>
          {displayText}
          {liveValue !== undefined && (
            <span style={{ color: "var(--text-muted)", marginLeft: "var(--space-sm)" }}>
              = {String(liveValue)}
            </span>
          )}
        </>
      }
      empty={!value}
      searchPlaceholder="Search state keys..."
      onOpenChange={setOpen}
      onClose={() => setShowCreate(false)}
      style={style}
      footer={({ close }) =>
        showCreate ? (
          <div style={createFormStyle}>
            <div style={{ fontSize: "var(--font-size-sm)", fontWeight: "var(--font-weight-semibold)", color: "var(--accent)" }}>
              Create New Variable
            </div>
            <div style={{ display: "flex", gap: "var(--space-sm)" }}>
              <div style={{ flex: 1 }}>
                <label style={miniLabel}>ID</label>
                <input
                  style={fieldStyle}
                  value={newId}
                  onChange={(e) => setNewId(e.target.value)}
                  placeholder="e.g. room_active"
                  autoFocus
                  onKeyDown={(e) => e.key === "Enter" && handleCreateVariable(close)}
                />
              </div>
              <div style={{ width: 90 }}>
                <label style={miniLabel}>Type</label>
                <select style={fieldStyle} value={newType} onChange={(e) => setNewType(e.target.value)}>
                  <option value="string">String</option>
                  <option value="boolean">Boolean</option>
                  <option value="number">Number</option>
                </select>
              </div>
            </div>
            <div style={{ display: "flex", gap: "var(--space-sm)" }}>
              <div style={{ flex: 1 }}>
                <label style={miniLabel}>Label</label>
                <input
                  style={fieldStyle}
                  value={newLabel}
                  onChange={(e) => setNewLabel(e.target.value)}
                  placeholder="Display name"
                  onKeyDown={(e) => e.key === "Enter" && handleCreateVariable(close)}
                />
              </div>
              <div style={{ width: 90 }}>
                <label style={miniLabel}>Default</label>
                {newType === "boolean" ? (
                  <select style={fieldStyle} value={newDefault} onChange={(e) => setNewDefault(e.target.value)}>
                    <option value="false">false</option>
                    <option value="true">true</option>
                  </select>
                ) : (
                  <input
                    style={fieldStyle}
                    value={newDefault}
                    onChange={(e) => setNewDefault(e.target.value)}
                    placeholder={newType === "number" ? "0" : ""}
                    onKeyDown={(e) => e.key === "Enter" && handleCreateVariable(close)}
                  />
                )}
              </div>
            </div>
            <div style={{ display: "flex", gap: "var(--space-xs)" }}>
              <button type="button" onClick={() => handleCreateVariable(close)} style={btnPrimary}>Create & Select</button>
              <button type="button" onClick={() => setShowCreate(false)} style={btnSecondary}>Cancel</button>
            </div>
          </div>
        ) : null
      }
    >
      {({ search, close }) => {
        const filteredEntries = filterEntries(allEntries, search);
        const groups = groupEntries(filteredEntries);
        return (
          <>
            {!hasLiveData && showDeviceState && (
              <div style={dropdownEmptyHintStyle}>
                Start the system to see live device state values.
              </div>
            )}

            {filteredEntries.length === 0 && search && (
              <div style={dropdownEmptyHintStyle}>
                No keys matching &ldquo;{search}&rdquo;
              </div>
            )}

            {Array.from(groups.entries()).map(([groupId, group]) => (
              <div key={groupId}>
                <div style={dropdownGroupHeaderStyle}>
                  <span style={{ fontWeight: "var(--font-weight-semibold)" }}>{group.label}</span>
                  <span style={{ fontWeight: "var(--font-weight-normal)", fontStyle: "italic", marginLeft: "var(--space-sm)" }}>
                    {group.desc}
                  </span>
                </div>
                {group.entries.map((entry) => {
                  const entryLive = liveState[entry.key];
                  const liveType: string = entryLive === null ? "null"
                    : entryLive === undefined ? ""
                    : typeof entryLive;
                  const sourceColor = entry.group === "variables" ? "#8b5cf6"
                    : entry.group.startsWith("device:") ? "#10b981"
                    : entry.group === "system" ? "#6b7280"
                    : entry.group.startsWith("plugin:") ? "#f59e0b"
                    : "#3b82f6";
                  return (
                  <div
                    key={entry.key}
                    onClick={() => handleSelect(entry.key, close)}
                    style={{
                      ...dropdownRowStyle,
                      background: entry.key === value ? "var(--bg-hover)" : undefined,
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                    onMouseLeave={(e) =>
                      (e.currentTarget.style.background =
                        entry.key === value ? "var(--bg-hover)" : "transparent")
                    }
                  >
                    {/* Source indicator dot */}
                    <span style={{
                      width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
                      background: sourceColor, marginRight: "var(--space-xs)",
                    }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                        <span
                          style={{
                            fontFamily: "var(--font-mono)",
                            fontSize: "var(--font-size-sm)",
                            color: "var(--text-primary)",
                          }}
                        >
                          {entry.label}
                        </span>
                        {entry.type && (
                          <span style={dropdownTypeBadgeStyle}>{entry.type}</span>
                        )}
                        {!entry.type && liveType && liveType !== "" && (
                          <span style={dropdownTypeBadgeStyle}>{liveType}</span>
                        )}
                      </div>
                      {entry.group !== "variables" && (
                        <div
                          style={{
                            fontSize: "var(--font-size-2xs)",
                            color: "var(--text-muted)",
                            fontFamily: "var(--font-mono)",
                          }}
                        >
                          {entry.key}
                        </div>
                      )}
                      {entry.description && (
                        <div
                          style={{
                            fontSize: "var(--font-size-2xs)",
                            color: "var(--text-muted)",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                          title={entry.description}
                        >
                          {entry.description}
                        </div>
                      )}
                    </div>
                    {entryLive !== undefined && (
                      <span style={{
                        fontSize: "var(--font-size-xs)", color: "var(--text-muted)", flexShrink: 0, marginRight: "var(--space-xs)",
                        maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                        fontFamily: "var(--font-mono)",
                      }}
                        title={String(entryLive)}
                      >
                        {String(entryLive)}
                      </span>
                    )}
                    <CopyButton value={entry.key} title="Copy state key" />
                  </div>
                  );
                })}
              </div>
            ))}

            {/* New Variable option */}
            {!showCreate && (
              <div
                onClick={() => setShowCreate(true)}
                style={{ ...dropdownRowStyle, color: "var(--accent)", gap: "var(--space-xs)", borderTop: "1px solid var(--border-color)" }}
                onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              >
                <Plus size={14} />
                <span>New Variable...</span>
              </div>
            )}
          </>
        );
      }}
    </SearchableDropdown>
  );
}

/* ── Styles ── */

const createFormStyle: React.CSSProperties = {
  padding: "var(--space-sm) var(--space-md)",
  borderTop: "1px solid var(--border-color)",
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
};

const miniLabel: React.CSSProperties = {
  display: "block",
  fontSize: "var(--font-size-xs)",
  color: "var(--text-muted)",
  marginBottom: "var(--space-2xs)",
};

const fieldStyle: React.CSSProperties = {
  width: "100%",
  padding: "var(--space-xs) var(--space-sm)",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
};

const btnPrimary: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-lg)",
  borderRadius: "var(--border-radius)",
  background: "var(--accent-bg)",
  color: "var(--text-on-accent-bg)",
  fontSize: "var(--font-size-sm)",
  border: "none",
  cursor: "pointer",
};

const btnSecondary: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-lg)",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  color: "var(--text-secondary)",
  fontSize: "var(--font-size-sm)",
  border: "none",
  cursor: "pointer",
};
