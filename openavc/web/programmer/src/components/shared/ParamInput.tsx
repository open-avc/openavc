import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import * as api from "../../api/restClient";
import type {
  ChildEntityEntry,
  ChildEntityStateVarDef,
  DriverParamDef,
} from "../../api/types";
import { useConnectionStore } from "../../store/connectionStore";
import { InlineError } from "./InlineError";
import { ParamCombobox } from "./ParamCombobox";
import {
  childSchemaOptions,
  findChildByValue,
  normalizeOptionList,
  parseStateOptionList,
} from "./paramOptions";
import type { ParamOption } from "./paramOptions";
import { validateParam } from "./paramValidation";
import { VariableKeyPicker } from "./VariableKeyPicker";

/** The widget for a single command/action parameter — the part that varies by
 *  the param's declared type. One shared control so every authoring surface
 *  (device Send Command, Quick Actions, macro steps, UI Builder bindings)
 *  renders the same dropdowns instead of free-typing values that can be
 *  misspelled. The label/help chrome stays with each surface; this owns only
 *  the input control.
 *
 *  Value in/out is a string (the convention all surfaces already use; numeric
 *  and boolean coercion happens at submit). Supports:
 *   - enum            -> select of the declared `values`
 *   - boolean         -> Yes/No select
 *   - child_id        -> live dropdown of the device's registered children of
 *                        `child_type` (needs `deviceId`); falls back to text
 *   - options_from    -> cascade: a combobox of the controls on the child
 *                        chosen in a sibling `child_id` param (needs `deviceId`
 *                        + `values` + `params`)
 *   - options_state   -> combobox sourced from a state-published list
 *                        (a device-relative state key)
 *   - integer/number/float -> number input (honors min/max)
 *   - password/secret -> masked input (never pre-filled)
 *   - everything else -> text input
 *  With `allowDynamic`, a "$" toggle swaps the static control for a state-key
 *  picker ($var/$state, plus $trigger when `showTriggerContext`) — for surfaces
 *  whose runtime resolves $-prefixed values (macro steps). */

export interface ParamInputProps {
  def: Partial<DriverParamDef>;
  value: string;
  onChange: (value: string) => void;
  /** Enables the child_id dropdown (fetches the device's live children) and
   *  resolves `options_state` keys (`device.<deviceId>.<key>`). */
  deviceId?: string;
  /** The full param->value map of the command/action being authored. Lets a
   *  cascading param (`options_from`) read the sibling value it depends on. */
  values?: Record<string, unknown>;
  /** The full param schema of the command/action. Lets a cascading param find
   *  the sibling's `child_type` so it can offer that child's controls. */
  params?: Record<string, Partial<DriverParamDef>>;
  /** Show the "$" toggle -> VariableKeyPicker for dynamic state references. */
  allowDynamic?: boolean;
  /** Pass-through to VariableKeyPicker (offer $trigger.<field> refs). */
  showTriggerContext?: boolean;
  /** UI-event tokens this binding slot can deliver ($value/$input/...), shown
   *  as a "This control" group in the picker. When non-empty, toggling "$" on
   *  seeds the first token (e.g. $value) instead of $var. UI Builder bindings
   *  only — a macro step has no UI event. */
  eventContext?: { key: string; label: string }[];
  /** Placeholder for free-text inputs (defaults handled per-type). */
  placeholder?: string;
  /** Style for the widget row (e.g. { flex: 1 }). */
  style?: CSSProperties;
}

/** A param value is a dynamic state reference (and should render the picker). */
export function isDynamicParamValue(v: unknown): v is string {
  return typeof v === "string" && v.startsWith("$");
}

const toggleStyle = (active: boolean): CSSProperties => ({
  display: "flex",
  alignItems: "center",
  padding: "3px 6px",
  borderRadius: "var(--border-radius)",
  border: `1px solid ${active ? "var(--accent)" : "var(--border-color)"}`,
  background: active ? "rgba(138,180,147,0.15)" : "transparent",
  color: active ? "var(--accent)" : "var(--text-muted)",
  fontSize: 11,
  cursor: "pointer",
  flexShrink: 0,
  fontFamily: "var(--font-mono)",
});

export function ParamInput({
  def,
  value,
  onChange,
  deviceId,
  values,
  params,
  allowDynamic,
  showTriggerContext,
  eventContext,
  placeholder,
  style,
}: ParamInputProps) {
  const type = def.type || "string";
  const optionsFrom =
    def.options_from?.source === "child_schema" ? def.options_from : undefined;

  // type_from: this field takes its input type from the control chosen in a
  // sibling param (which itself cascades off a child_id `component`). Resolve
  // the chain: type_from.param -> its options_from.param (the component) ->
  // that param's child_type (the children we need to read the schema from).
  const typeFrom = def.type_from?.param ? def.type_from : undefined;
  const tfControlDef = typeFrom ? params?.[typeFrom.param] : undefined;
  const tfComponentParam =
    tfControlDef?.options_from?.source === "child_schema"
      ? tfControlDef.options_from.param
      : undefined;
  const tfChildType = tfComponentParam
    ? params?.[tfComponentParam]?.child_type
    : undefined;

  // The child type whose live children this field needs:
  //  - a child_id field needs its own declared child_type;
  //  - a cascading (options_from) field needs the sibling child_id's child_type;
  //  - a type_from field needs the component child_type to read the control schema.
  const ownChildType = type === "child_id" ? def.child_type : undefined;
  const siblingChildType = optionsFrom
    ? params?.[optionsFrom.param]?.child_type
    : undefined;
  const fetchChildType = ownChildType ?? siblingChildType ?? tfChildType;

  // Children register dynamically as the driver discovers them, so fetch fresh
  // per field. `undefined` => still loading. Each entry carries the server's
  // resolved display_name (project label, else the device-reported name).
  const [children, setChildren] = useState<ChildEntityEntry[] | undefined>(
    undefined,
  );
  useEffect(() => {
    if (!fetchChildType || !deviceId) return;
    let cancelled = false;
    api
      .listChildEntitiesByType(deviceId, fetchChildType)
      .then((resp) => {
        if (!cancelled) {
          setChildren(resp.children);
        }
      })
      .catch(() => {
        if (!cancelled) setChildren([]);
      });
    return () => {
      cancelled = true;
    };
  }, [fetchChildType, deviceId]);

  // State-sourced options: a device-relative key (`options_state`), resolved
  // against this device.
  const stateOptionKey =
    def.options_state && deviceId
      ? `device.${deviceId}.${def.options_state}`
      : undefined;
  const stateOptionRaw = useConnectionStore((s) =>
    stateOptionKey ? s.liveState[stateOptionKey] : undefined,
  );

  const rowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 4,
    ...style,
  };

  // The `$` toggle is available for child_id params too — "act on the child
  // picked on the panel" ($var.selected_outlet) is a real macro pattern. The
  // engine resolves the reference before the platform coerces the id, so an
  // integer-id child type still receives a proper id at send time.
  const canToggle = allowDynamic;
  const dynamic = canToggle && isDynamicParamValue(value);

  // Toggling "$" on seeds the binding's own control token (e.g. $value) when
  // this surface delivers a UI event, since that's the overwhelmingly common
  // case; otherwise it seeds the $var. namespace.
  const dynamicSeed =
    eventContext && eventContext.length > 0 ? `$${eventContext[0].key}` : "$var.";

  const toggle = canToggle ? (
    <button
      type="button"
      onClick={() => onChange(dynamic ? "" : dynamicSeed)}
      title={
        dynamic
          ? "Switch to a fixed value"
          : "Use a dynamic value read from state at runtime"
      }
      style={toggleStyle(!!dynamic)}
    >
      $
    </button>
  ) : null;

  if (dynamic) {
    return (
      <div style={rowStyle}>
        <VariableKeyPicker
          value={value.slice(1)}
          onChange={(key) => onChange(`$${key}`)}
          showDeviceState
          showTriggerContext={showTriggerContext}
          eventContext={eventContext}
          placeholder="Select state key..."
          style={{ flex: 1 }}
        />
        {toggle}
      </div>
    );
  }

  // Resolve option-provider lists. A combobox (input + datalist) renders these
  // so the user can pick a known value or type one the platform can't yet see
  // (offline device, control not discovered, escape-hatch command).
  let comboOptions: ParamOption[] | undefined;
  let comboHint: string | undefined;
  if (optionsFrom) {
    if (!deviceId || !siblingChildType) {
      comboOptions = []; // unresolvable here -> behaves as forgiving free text
    } else {
      const siblingValue = values?.[optionsFrom.param];
      const sv = siblingValue == null ? "" : String(siblingValue);
      if (!sv) {
        comboOptions = [];
        comboHint = `Pick ${optionsFrom.param} first to list its controls.`;
      } else {
        const chosen = findChildByValue(children, sv);
        comboOptions = childSchemaOptions(chosen?.schema);
        if (children !== undefined && !chosen) {
          comboHint = `No "${sv}" found — type the control name.`;
        }
      }
    }
  } else if (def.options_state) {
    comboOptions = parseStateOptionList(stateOptionRaw);
  }

  // Resolve the type_from cascade: the control chosen in the sibling param,
  // looked up in the chosen component's child schema, supplies this field's
  // effective type/min/max. Until both are chosen, fall back to the declared
  // type (a forgiving text box).
  let typeSpec: ChildEntityStateVarDef | undefined;
  if (typeFrom && tfComponentParam) {
    const comp = findChildByValue(children, values?.[tfComponentParam]);
    const ctrlVal = values?.[typeFrom.param];
    if (comp?.schema && ctrlVal != null && String(ctrlVal)) {
      typeSpec = comp.schema[String(ctrlVal)];
    }
  }
  const effType = typeSpec?.type ?? type;
  const effValues = typeSpec?.values ?? def.values;
  const effMin = typeSpec?.min ?? def.min;
  const effMax = typeSpec?.max ?? def.max;

  let widget: React.ReactNode;
  if (effType === "enum" && effValues) {
    // Options may be plain strings or {value, label} objects — show the label,
    // send the wire value (the runtime also maps a label back to its value).
    const enumOptions = normalizeOptionList(effValues);
    widget = (
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{ flex: 1 }}
      >
        {!def.required && <option value="">(none)</option>}
        {enumOptions.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  } else if (comboOptions !== undefined) {
    widget = (
      <div style={{ flex: 1 }}>
        {comboOptions.length > 0 ? (
          <ParamCombobox
            value={value}
            onChange={onChange}
            options={comboOptions}
            placeholder={placeholder ?? ""}
            style={{ width: "100%" }}
          />
        ) : (
          // No options resolved yet (offline device, sibling not picked) —
          // a plain text box keeps the field forgiving.
          <input
            type="text"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder ?? ""}
            style={{ width: "100%" }}
          />
        )}
        {comboHint && (
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
            {comboHint}
          </div>
        )}
      </div>
    );
  } else if (effType === "boolean") {
    widget = (
      <select
        value={value || "false"}
        onChange={(e) => onChange(e.target.value)}
        style={{ flex: 1 }}
      >
        <option value="true">Yes</option>
        <option value="false">No</option>
      </select>
    );
  } else if (ownChildType && deviceId) {
    const registered = (children ?? []).filter((c) => c.registered);
    widget = (
      <div style={{ flex: 1 }}>
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          style={{ width: "100%" }}
        >
          <option value="">
            {children === undefined
              ? "Loading children..."
              : `(select ${ownChildType})`}
          </option>
          {registered.map((c) => {
            // Project label, else the device-reported name -- settled on the
            // server (drivers/child_ids.child_display_name) so this picker and
            // the matrix picker cannot disagree about what a child is called.
            // The stored/sent value is always the id.
            const name = c.display_name ?? "";
            return (
              <option key={c.local_id} value={String(c.local_id)}>
                {name
                  ? `${name} (${c.local_id})`
                  : `${ownChildType} ${c.local_id}`}
              </option>
            );
          })}
        </select>
        {children !== undefined && registered.length === 0 && (
          <div
            style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}
          >
            No registered {ownChildType} entries on this device yet — see the
            Child Entities tab.
          </div>
        )}
      </div>
    );
  } else {
    const isNumber =
      effType === "integer" || effType === "number" || effType === "float";
    const numberRange =
      effMin !== undefined && effMax !== undefined
        ? `${effMin}-${effMax}`
        : undefined;
    widget = (
      <input
        type={
          def.secret || type === "password"
            ? "password"
            : isNumber
              ? "number"
              : "text"
        }
        autoComplete="new-password"
        value={value}
        min={effMin}
        max={effMax}
        onChange={(e) => onChange(e.target.value)}
        placeholder={numberRange ?? placeholder ?? ""}
        style={{ flex: 1 }}
      />
    );
  }

  // Authoring-time validation: surface a clear inline error for a bad literal
  // value (out of min/max range, wrong number, pattern mismatch) instead of
  // letting it submit silently. The runtime re-validates regardless — this just
  // catches it earlier. Dynamic refs and empty values return null (not checked
  // here). Routed through ParamInput so every surface gets it once.
  const error = validateParam(def, value);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, ...style }}>
      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        {widget}
        {/* Display only — the unit is never part of the value sent. */}
        {def.unit && (
          <span
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--text-muted)",
              whiteSpace: "nowrap",
            }}
          >
            {def.unit}
          </span>
        )}
        {toggle}
      </div>
      {error && (
        <InlineError
          message={error}
          style={{ padding: "2px 8px", marginTop: 0, fontSize: 11, borderRadius: 4 }}
        />
      )}
    </div>
  );
}
