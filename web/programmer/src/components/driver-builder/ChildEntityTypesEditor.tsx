import { useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import type {
  DriverChildEntityType,
  DriverChildStateVarDef,
  DriverDefinition,
} from "../../api/types";
import {
  CHILD_TYPE_ID_RE,
  applyChildVarTypeChange,
  checkRename,
  nextChildFieldId,
  nextChildTypeId,
  sanitizeFieldId,
  sanitizeTypeId,
} from "./childEntityTypesHelpers";
import { IdRenameInput, type RenameResult } from "./IdRenameInput";

interface ChildEntityTypesEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  marginBottom: "var(--space-xs)",
};

const helpStyle: React.CSSProperties = {
  fontSize: "11px",
  color: "var(--text-muted)",
  marginTop: "var(--space-xs)",
};

export function ChildEntityTypesEditor({
  draft,
  onUpdate,
}: ChildEntityTypesEditorProps) {
  const types = draft.child_entity_types ?? {};
  const typeNames = Object.keys(types);
  const [expanded, setExpanded] = useState<string | null>(typeNames[0] ?? null);

  const writeTypes = (next: Record<string, DriverChildEntityType>) => {
    onUpdate({
      child_entity_types: Object.keys(next).length ? next : undefined,
    });
  };

  const addType = () => {
    const name = nextChildTypeId(typeNames);
    const initial: DriverChildEntityType = {
      label: "New Child Type",
      label_plural: "",
      id_format: { type: "integer", min: 1, max: undefined, pad_width: 0 },
      state_variables: {},
      summary_fields: [],
      label_field: "",
    };
    writeTypes({ ...types, [name]: initial });
    setExpanded(name);
  };

  const removeType = (name: string) => {
    const next = { ...types };
    delete next[name];
    writeTypes(next);
    if (expanded === name) setExpanded(null);
  };

  const renameType = (oldName: string, newName: string): RenameResult => {
    const cleaned = sanitizeTypeId(newName);
    const check = checkRename(cleaned, oldName, typeNames);
    if (!check.ok || cleaned === oldName) return check;
    const next: typeof types = {};
    for (const [k, v] of Object.entries(types)) {
      next[k === oldName ? cleaned : k] = v;
    }
    writeTypes(next);
    if (expanded === oldName) setExpanded(cleaned);
    return { ok: true };
  };

  const updateType = (
    name: string,
    partial: Partial<DriverChildEntityType>,
  ) => {
    const merged = { ...types[name], ...partial } as DriverChildEntityType;
    writeTypes({ ...types, [name]: merged });
  };

  return (
    <div data-testid="child-entity-types-editor">
      <p
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          marginBottom: "var(--space-md)",
        }}
      >
        Sub-units this driver manages — encoders, decoders, zones, presets,
        anything the device addresses by ID. Each child type gets its own row
        in the Child Entities tab on a device and its own per-instance state
        keys ({"device.<id>.<type>.<local_id>.<prop>"}). Leave this empty for
        ordinary single-unit devices.
      </p>

      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          border: "1px solid var(--border-color)",
          borderRadius: "var(--radius-sm)",
          padding: "var(--space-sm) var(--space-md)",
          marginBottom: "var(--space-md)",
        }}
      >
        To create children at runtime, give each type an <b>Instances</b> rule
        below — a fixed count, or a config field the installer fills in. The
        driver registers them on connect; response rules route per-child
        values with <code>child_set</code>, and polling can send one query per
        child with <code>each child</code>. A type without an Instances rule
        stays declaration-only (children are then created only by a Python
        driver&apos;s <code>register_child</code>).
      </div>

      {typeNames.length === 0 && (
        <p
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-muted)",
            marginBottom: "var(--space-md)",
            fontStyle: "italic",
          }}
        >
          No child types declared.
        </p>
      )}

      {typeNames.map((name) => {
        const t = types[name];
        const isOpen = expanded === name;
        const varCount = Object.keys(t.state_variables ?? {}).length;
        return (
          <div
            key={name}
            data-testid={`child-type-card-${name}`}
            style={{
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              marginBottom: "var(--space-sm)",
              background: "var(--bg-surface)",
            }}
          >
            <button
              data-testid={`child-type-header-${name}`}
              onClick={() => setExpanded(isOpen ? null : name)}
              style={{
                display: "flex",
                alignItems: "center",
                width: "100%",
                padding: "var(--space-sm) var(--space-md)",
                gap: "var(--space-sm)",
                textAlign: "left",
              }}
            >
              {isOpen ? (
                <ChevronDown size={14} />
              ) : (
                <ChevronRight size={14} />
              )}
              <span
                style={{
                  flex: 1,
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                {name}
              </span>
              <span
                style={{
                  color: "var(--text-muted)",
                  fontSize: "11px",
                }}
              >
                {t.label || "—"} · {varCount}{" "}
                {varCount === 1 ? "field" : "fields"}
              </span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  removeType(name);
                }}
                title="Remove this child type"
                style={{ padding: "2px", color: "var(--text-muted)" }}
              >
                <Trash2 size={14} />
              </button>
            </button>

            {isOpen && (
              <div
                style={{
                  padding: "var(--space-md)",
                  borderTop: "1px solid var(--border-color)",
                  display: "flex",
                  flexDirection: "column",
                  gap: "var(--space-md)",
                }}
              >
                <IdentitySection
                  name={name}
                  type={t}
                  onRename={(next) => renameType(name, next)}
                  onUpdate={(partial) => updateType(name, partial)}
                />
                <IdFormatSection
                  type={t}
                  onUpdate={(partial) => updateType(name, partial)}
                />
                <InstancesSection
                  name={name}
                  type={t}
                  configFields={Array.from(
                    new Set([
                      ...Object.keys(draft.config_schema ?? {}),
                      ...Object.keys(draft.default_config ?? {}),
                    ]),
                  )}
                  onUpdate={(partial) => updateType(name, partial)}
                />
                <StateVarsSection
                  type={t}
                  onUpdate={(partial) => updateType(name, partial)}
                />
                <PresentationSection
                  type={t}
                  onUpdate={(partial) => updateType(name, partial)}
                />
              </div>
            )}
          </div>
        );
      })}

      <button
        data-testid="add-child-type"
        onClick={addType}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          padding: "var(--space-sm) var(--space-md)",
          borderRadius: "var(--border-radius)",
          background: "var(--bg-hover)",
          fontSize: "var(--font-size-sm)",
          marginTop: "var(--space-sm)",
        }}
      >
        <Plus size={14} /> Add Child Type
      </button>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Identity (type id, label, label_plural)
// ──────────────────────────────────────────────────────────────────────────
function IdentitySection({
  name,
  type,
  onRename,
  onUpdate,
}: {
  name: string;
  type: DriverChildEntityType;
  onRename: (next: string) => RenameResult;
  onUpdate: (partial: Partial<DriverChildEntityType>) => void;
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr 1fr",
        gap: "var(--space-md)",
      }}
    >
      <div>
        <label style={labelStyle}>Type ID</label>
        <IdRenameInput
          data-testid={`child-type-id-${name}`}
          value={name}
          sanitize={sanitizeTypeId}
          onCommit={onRename}
          style={{ fontFamily: "var(--font-mono)" }}
        />
        <div style={helpStyle}>
          Lowercase, underscores. Becomes the third segment in state keys
          (e.g. <code>device.matrix1.encoder.005.signal</code>).
        </div>
      </div>
      <div>
        <label style={labelStyle}>Label (singular)</label>
        <input
          data-testid={`child-type-label-${name}`}
          value={type.label ?? ""}
          onChange={(e) => onUpdate({ label: e.target.value })}
          placeholder="Encoder"
          style={{ width: "100%" }}
        />
        <div style={helpStyle}>Shown in the IDE.</div>
      </div>
      <div>
        <label style={labelStyle}>Label (plural)</label>
        <input
          value={type.label_plural ?? ""}
          onChange={(e) => onUpdate({ label_plural: e.target.value })}
          placeholder="Encoders"
          style={{ width: "100%" }}
        />
        <div style={helpStyle}>Tab title for the list view.</div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// ID format (integer min/max/pad_width)
// ──────────────────────────────────────────────────────────────────────────
function IdFormatSection({
  type,
  onUpdate,
}: {
  type: DriverChildEntityType;
  onUpdate: (partial: Partial<DriverChildEntityType>) => void;
}) {
  const idf = type.id_format ?? { type: "integer" as const };

  const writeIdFormat = (partial: Partial<typeof idf>) => {
    const merged = { ...idf, ...partial } as typeof idf;
    onUpdate({ id_format: merged });
  };

  const parseInteger = (raw: string): number | undefined => {
    if (raw === "") return undefined;
    const n = parseInt(raw, 10);
    return Number.isFinite(n) ? n : undefined;
  };

  return (
    <div
      style={{
        border: "1px solid var(--border-color)",
        borderRadius: "var(--border-radius)",
        padding: "var(--space-sm) var(--space-md)",
      }}
    >
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          fontWeight: 600,
          marginBottom: "var(--space-xs)",
        }}
      >
        ID format
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr 1fr 1fr",
          gap: "var(--space-sm)",
          alignItems: "end",
        }}
      >
        <div>
          <span style={{ ...labelStyle, fontSize: "11px" }}>Type</span>
          <select
            value="integer"
            disabled
            style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
          >
            <option value="integer">Integer</option>
          </select>
        </div>
        <div>
          <span style={{ ...labelStyle, fontSize: "11px" }}>Min</span>
          <input
            type="number"
            value={idf.min ?? ""}
            onChange={(e) =>
              writeIdFormat({ min: parseInteger(e.target.value) })
            }
            placeholder="1"
            style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
          />
        </div>
        <div>
          <span style={{ ...labelStyle, fontSize: "11px" }}>Max</span>
          <input
            type="number"
            value={idf.max ?? ""}
            onChange={(e) =>
              writeIdFormat({ max: parseInteger(e.target.value) })
            }
            placeholder="unbounded"
            style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
          />
        </div>
        <div>
          <span style={{ ...labelStyle, fontSize: "11px" }}>Pad width</span>
          <input
            type="number"
            value={idf.pad_width ?? ""}
            onChange={(e) =>
              writeIdFormat({ pad_width: parseInteger(e.target.value) })
            }
            placeholder="0"
            style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
          />
        </div>
      </div>
      <div style={helpStyle}>
        IDs are integers in <code>[min, max]</code>. Pad width zero-pads the
        local id when rendered in state keys — e.g. pad_width 3 renders
        encoder 5 as <code>005</code>. v1 only supports integer IDs.
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Instances (declarative roster — count / count_from / ids_from + label)
// ──────────────────────────────────────────────────────────────────────────
function InstancesSection({
  name,
  type,
  configFields,
  onUpdate,
}: {
  name: string;
  type: DriverChildEntityType;
  configFields: string[];
  onUpdate: (partial: Partial<DriverChildEntityType>) => void;
}) {
  const inst = type.instances;
  const source: "none" | "count" | "count_from" | "ids_from" =
    inst == null
      ? "none"
      : inst.count !== undefined
        ? "count"
        : inst.count_from !== undefined
          ? "count_from"
          : inst.ids_from !== undefined
            ? "ids_from"
            : "none";

  const keepLabel = inst?.label ? { label: inst.label } : {};

  const setSource = (next: string) => {
    if (next === "none") {
      onUpdate({ instances: undefined });
    } else if (next === "count") {
      onUpdate({ instances: { count: 2, ...keepLabel } });
    } else if (next === "count_from") {
      onUpdate({ instances: { count_from: configFields[0] ?? "", ...keepLabel } });
    } else {
      onUpdate({ instances: { ids_from: configFields[0] ?? "", ...keepLabel } });
    }
  };

  const fieldSelect = (
    key: "count_from" | "ids_from",
    value: string,
  ) => (
    <select
      data-testid={`child-instances-${key}-${name}`}
      value={value}
      onChange={(e) =>
        onUpdate({ instances: { [key]: e.target.value, ...keepLabel } })
      }
      style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
    >
      {!configFields.includes(value) && <option value={value}>{value || "—"}</option>}
      {configFields.map((f) => (
        <option key={f} value={f}>
          {f}
        </option>
      ))}
    </select>
  );

  return (
    <div
      style={{
        border: "1px solid var(--border-color)",
        borderRadius: "var(--border-radius)",
        padding: "var(--space-sm) var(--space-md)",
      }}
      data-testid={`child-instances-${name}`}
    >
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          fontWeight: 600,
          marginBottom: "var(--space-xs)",
        }}
      >
        Instances
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr 1fr",
          gap: "var(--space-sm)",
          alignItems: "end",
        }}
      >
        <div>
          <span style={{ ...labelStyle, fontSize: "11px" }}>Create from</span>
          <select
            data-testid={`child-instances-source-${name}`}
            value={source}
            onChange={(e) => setSource(e.target.value)}
            style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
          >
            <option value="none">Not created by this driver</option>
            <option value="count">Fixed count</option>
            <option value="count_from">Config field (how many)</option>
            <option value="ids_from">Config field (list of IDs)</option>
          </select>
        </div>
        {source === "count" && (
          <div>
            <span style={{ ...labelStyle, fontSize: "11px" }}>Count</span>
            <input
              data-testid={`child-instances-count-${name}`}
              type="number"
              min={1}
              value={inst?.count ?? ""}
              onChange={(e) => {
                const n = parseInt(e.target.value, 10);
                onUpdate({
                  instances: {
                    count: Number.isFinite(n) ? n : 1,
                    ...keepLabel,
                  },
                });
              }}
              style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
            />
          </div>
        )}
        {source === "count_from" && (
          <div>
            <span style={{ ...labelStyle, fontSize: "11px" }}>Config field</span>
            {fieldSelect("count_from", inst?.count_from ?? "")}
          </div>
        )}
        {source === "ids_from" && (
          <div>
            <span style={{ ...labelStyle, fontSize: "11px" }}>Config field</span>
            {fieldSelect("ids_from", inst?.ids_from ?? "")}
          </div>
        )}
        {source !== "none" && (
          <div>
            <span style={{ ...labelStyle, fontSize: "11px" }}>
              Label template
            </span>
            <input
              data-testid={`child-instances-label-${name}`}
              value={inst?.label ?? ""}
              onChange={(e) => {
                const label = e.target.value;
                const base = { ...(inst ?? {}) };
                if (label) base.label = label;
                else delete base.label;
                onUpdate({ instances: base });
              }}
              placeholder={`${type.label || "Item"} {id}`}
              style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
            />
          </div>
        )}
      </div>
      <div style={helpStyle}>
        {source === "none" ? (
          <>
            No children are created at runtime. Pick a source to register
            them automatically on connect.
          </>
        ) : source === "count" ? (
          <>Registers IDs 1..count on connect.</>
        ) : source === "count_from" ? (
          <>
            Reads an integer from the named config field and registers IDs
            1..N — lets one driver cover different frame sizes.
          </>
        ) : (
          <>
            Reads a comma-separated list from the named config field (e.g.{" "}
            <code>1,2,4</code>) — for sparse or installer-chosen IDs.
          </>
        )}{" "}
        The label template seeds each child&apos;s display name (
        <code>{"{id}"}</code> inserts the ID); a name set in the project
        always wins.
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Per-child state variables — mirrors StateVariableEditor's grid with
// `cloud_priority` added in the second row.
// ──────────────────────────────────────────────────────────────────────────
function StateVarsSection({
  type,
  onUpdate,
}: {
  type: DriverChildEntityType;
  onUpdate: (partial: Partial<DriverChildEntityType>) => void;
}) {
  const vars = type.state_variables ?? {};
  const varNames = Object.keys(vars);

  const writeVars = (next: Record<string, DriverChildStateVarDef>) => {
    onUpdate({ state_variables: next });
  };

  const addVar = () => {
    const name = nextChildFieldId(varNames);
    writeVars({
      ...vars,
      [name]: { type: "string", label: "New Field" },
    });
  };

  const removeVar = (name: string) => {
    const next = { ...vars };
    delete next[name];
    writeVars(next);
  };

  const renameVar = (oldName: string, newName: string): RenameResult => {
    const cleaned = sanitizeFieldId(newName);
    const check = checkRename(cleaned, oldName, varNames);
    if (!check.ok || cleaned === oldName) return check;
    const next: typeof vars = {};
    for (const [k, v] of Object.entries(vars)) {
      next[k === oldName ? cleaned : k] = v;
    }
    writeVars(next);
    return { ok: true };
  };

  const updateVar = (
    name: string,
    field: string,
    value: unknown,
  ) => {
    const merged = { ...vars[name], [field]: value } as Record<string, unknown>;
    if (value === undefined) delete merged[field];
    writeVars({
      ...vars,
      [name]: merged as unknown as DriverChildStateVarDef,
    });
  };

  // Atomic replace of a var def — used when several fields change together (a
  // type switch clears min/max/step/values). Writing them as separate updateVar
  // calls would each read the same stale `vars` snapshot and clobber the type.
  const updateVarDef = (name: string, def: DriverChildStateVarDef) => {
    writeVars({ ...vars, [name]: def });
  };

  return (
    <div>
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          fontWeight: 600,
          marginBottom: "var(--space-xs)",
        }}
      >
        State fields per {type.label || "child"}
      </div>
      <div
        style={{
          ...helpStyle,
          marginTop: 0,
          marginBottom: "var(--space-sm)",
        }}
      >
        Each registered child gets one state key per field. The platform also
        injects a boolean <code>online</code> and a string <code>label</code>
        — you don't need to declare those.
      </div>

      {varNames.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr 1fr auto auto",
            gap: "var(--space-sm)",
            marginBottom: "var(--space-xs)",
            alignItems: "center",
          }}
        >
          <span style={{ ...labelStyle, fontSize: "11px" }}>Field ID</span>
          <span style={{ ...labelStyle, fontSize: "11px" }}>Label</span>
          <span style={{ ...labelStyle, fontSize: "11px" }}>Help</span>
          <span style={{ ...labelStyle, fontSize: "11px" }}>Type</span>
          <span />
        </div>
      )}

      {varNames.map((name) => {
        const v = vars[name];
        const isNumeric = v.type === "integer" || v.type === "number";
        const isEnum = v.type === "enum";
        return (
          <div key={name} style={{ marginBottom: "var(--space-xs)" }}>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr 1fr auto auto",
                gap: "var(--space-sm)",
                alignItems: "center",
              }}
            >
              <IdRenameInput
                data-testid={`child-field-id-${name}`}
                value={name}
                sanitize={sanitizeFieldId}
                onCommit={(next) => renameVar(name, next)}
                style={{
                  fontSize: "var(--font-size-sm)",
                  fontFamily: "var(--font-mono)",
                }}
              />
              <input
                value={v.label ?? ""}
                onChange={(e) => updateVar(name, "label", e.target.value)}
                placeholder={name}
                style={{ fontSize: "var(--font-size-sm)" }}
              />
              <input
                value={v.help ?? ""}
                onChange={(e) =>
                  updateVar(name, "help", e.target.value || undefined)
                }
                placeholder="Description..."
                style={{ fontSize: "var(--font-size-sm)" }}
              />
              <select
                value={v.type}
                onChange={(e) =>
                  updateVarDef(name, applyChildVarTypeChange(v, e.target.value))
                }
                style={{ width: 100, fontSize: "var(--font-size-sm)" }}
              >
                <option value="string">String</option>
                <option value="integer">Integer</option>
                <option value="number">Number</option>
                <option value="boolean">Boolean</option>
                <option value="enum">Enum</option>
              </select>
              <button
                onClick={() => removeVar(name)}
                style={{ padding: "2px", color: "var(--text-muted)" }}
              >
                <Trash2 size={14} />
              </button>
            </div>

            {(isNumeric || isEnum) && (
              <div
                style={{
                  marginTop: "var(--space-xs)",
                  marginLeft: "var(--space-sm)",
                  paddingLeft: "var(--space-sm)",
                  borderLeft: "2px solid var(--border-color)",
                }}
              >
                {isNumeric && (
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "100px 100px 100px 1fr",
                      gap: "var(--space-sm)",
                      alignItems: "center",
                      marginBottom: "var(--space-xs)",
                    }}
                  >
                    <input
                      type="number"
                      value={v.min ?? ""}
                      onChange={(e) => {
                        const raw = e.target.value;
                        updateVar(
                          name,
                          "min",
                          raw === ""
                            ? undefined
                            : v.type === "integer"
                              ? parseInt(raw, 10)
                              : parseFloat(raw),
                        );
                      }}
                      placeholder="min"
                      style={{ fontSize: "var(--font-size-sm)" }}
                    />
                    <input
                      type="number"
                      value={v.max ?? ""}
                      onChange={(e) => {
                        const raw = e.target.value;
                        updateVar(
                          name,
                          "max",
                          raw === ""
                            ? undefined
                            : v.type === "integer"
                              ? parseInt(raw, 10)
                              : parseFloat(raw),
                        );
                      }}
                      placeholder="max"
                      style={{ fontSize: "var(--font-size-sm)" }}
                    />
                    <input
                      type="number"
                      value={v.step ?? ""}
                      onChange={(e) => {
                        const raw = e.target.value;
                        updateVar(
                          name,
                          "step",
                          raw === "" ? undefined : parseFloat(raw),
                        );
                      }}
                      placeholder="step"
                      style={{ fontSize: "var(--font-size-sm)" }}
                    />
                    <div style={helpStyle}>Numeric bounds.</div>
                  </div>
                )}
                {isEnum && (
                  <div>
                    <input
                      value={(v.values ?? []).join(", ")}
                      onChange={(e) => {
                        const values = e.target.value
                          .split(",")
                          .map((s) => s.trim())
                          .filter(Boolean);
                        updateVar(
                          name,
                          "values",
                          values.length ? values : undefined,
                        );
                      }}
                      placeholder="Comma-separated values, e.g.: idle, active, fault"
                      style={{
                        width: "100%",
                        fontSize: "var(--font-size-sm)",
                        fontFamily: "var(--font-mono)",
                      }}
                    />
                    <div style={helpStyle}>
                      Allowed enum values, separated by commas.
                    </div>
                  </div>
                )}
              </div>
            )}

            <div
              style={{
                marginTop: "var(--space-xs)",
                marginLeft: "var(--space-sm)",
                paddingLeft: "var(--space-sm)",
                borderLeft: "2px solid var(--border-color)",
                display: "grid",
                gridTemplateColumns: "180px 1fr",
                gap: "var(--space-sm)",
                alignItems: "center",
              }}
            >
              <select
                value={v.cloud_priority ?? ""}
                onChange={(e) => {
                  const raw = e.target.value;
                  updateVar(
                    name,
                    "cloud_priority",
                    raw === "" ? undefined : raw,
                  );
                }}
                style={{ fontSize: "var(--font-size-sm)" }}
              >
                <option value="">Cloud priority: default (5s)</option>
                <option value="high">High (2s, like top-level)</option>
                <option value="low">Low (30s, verbose state)</option>
              </select>
              <div style={helpStyle}>
                How often the cloud agent flushes this field. Leave default
                for ordinary per-child telemetry; mark{" "}
                <strong>high</strong> for latency-sensitive routing/mute
                fields and <strong>low</strong> for verbose per-IO state.
              </div>
            </div>
          </div>
        );
      })}

      <button
        data-testid="add-child-field"
        onClick={addVar}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          padding: "var(--space-sm) var(--space-md)",
          borderRadius: "var(--border-radius)",
          background: "var(--bg-hover)",
          fontSize: "var(--font-size-sm)",
          marginTop: "var(--space-sm)",
        }}
      >
        <Plus size={14} /> Add Field
      </button>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Presentation (summary_fields + label_field)
// ──────────────────────────────────────────────────────────────────────────
function PresentationSection({
  type,
  onUpdate,
}: {
  type: DriverChildEntityType;
  onUpdate: (partial: Partial<DriverChildEntityType>) => void;
}) {
  const varNames = Object.keys(type.state_variables ?? {});
  const summary = type.summary_fields ?? [];

  const toggleSummary = (name: string, on: boolean) => {
    const next = on
      ? Array.from(new Set([...summary, name]))
      : summary.filter((n) => n !== name);
    onUpdate({ summary_fields: next.length ? next : undefined });
  };

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "2fr 1fr",
        gap: "var(--space-md)",
      }}
    >
      <div>
        <label style={labelStyle}>Summary fields</label>
        <div style={helpStyle}>
          Which fields appear as columns in the device's Child Entities tab
          list view. Other fields stay visible in the expanded per-child
          state.
        </div>
        {varNames.length === 0 ? (
          <div
            style={{
              fontSize: "11px",
              color: "var(--text-muted)",
              fontStyle: "italic",
              marginTop: 4,
            }}
          >
            Add state fields above first.
          </div>
        ) : (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "var(--space-xs)",
              marginTop: 4,
            }}
          >
            {varNames.map((n) => (
              <label
                key={n}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                  fontSize: "var(--font-size-sm)",
                  padding: "2px var(--space-xs)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)",
                }}
              >
                <input
                  type="checkbox"
                  checked={summary.includes(n)}
                  onChange={(e) => toggleSummary(n, e.target.checked)}
                />
                <span style={{ fontFamily: "var(--font-mono)" }}>{n}</span>
              </label>
            ))}
          </div>
        )}
      </div>
      <div>
        <label style={labelStyle}>Device-set name field</label>
        <select
          value={type.label_field ?? ""}
          onChange={(e) =>
            onUpdate({ label_field: e.target.value || undefined })
          }
          style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
        >
          <option value="">(none)</option>
          {varNames.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <div style={helpStyle}>
          Which field carries the controller-owned name. The user's friendly
          label is separate and lives in the project file.
        </div>
      </div>
    </div>
  );
}

export { CHILD_TYPE_ID_RE };
