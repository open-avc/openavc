import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import * as api from "../../api/restClient";
import type { ChildEntityEntry, DriverParamDef } from "../../api/types";
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
 *   - integer/number/float -> number input (honors min/max)
 *   - password/secret -> masked input (never pre-filled)
 *   - everything else -> text input
 *  With `allowDynamic`, a "$" toggle swaps the static control for a state-key
 *  picker ($var/$state, plus $trigger when `showTriggerContext`) — for surfaces
 *  whose runtime resolves $-prefixed values (macro steps). */

export interface ParamInputProps {
  // DriverParamDef plus `secret` (carried by action params) so password fields
  // render masked from either schema source.
  def: Partial<DriverParamDef> & { secret?: boolean };
  value: string;
  onChange: (value: string) => void;
  /** Enables the child_id dropdown (fetches the device's live children). */
  deviceId?: string;
  /** Show the "$" toggle -> VariableKeyPicker for dynamic state references. */
  allowDynamic?: boolean;
  /** Pass-through to VariableKeyPicker (offer $trigger.<field> refs). */
  showTriggerContext?: boolean;
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
  allowDynamic,
  showTriggerContext,
  placeholder,
  style,
}: ParamInputProps) {
  const type = def.type || "string";
  const childType = type === "child_id" ? def.child_type : undefined;

  // child_id renders a dropdown of the device's registered children. Fetched
  // fresh per field — children register dynamically as the driver discovers
  // them. `undefined` => still loading.
  const [children, setChildren] = useState<ChildEntityEntry[] | undefined>(
    undefined,
  );
  useEffect(() => {
    if (!childType || !deviceId) return;
    let cancelled = false;
    api
      .listChildEntitiesByType(deviceId, childType)
      .then((resp) => {
        if (!cancelled) setChildren(resp.children);
      })
      .catch(() => {
        if (!cancelled) setChildren([]);
      });
    return () => {
      cancelled = true;
    };
  }, [childType, deviceId]);

  const rowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 4,
    ...style,
  };

  // A child reference as a state key is a non-sensible combo, so child_id never
  // shows the dynamic toggle.
  const canToggle = allowDynamic && type !== "child_id";
  const dynamic = canToggle && isDynamicParamValue(value);

  const toggle = canToggle ? (
    <button
      type="button"
      onClick={() => onChange(dynamic ? "" : "$var.")}
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
          placeholder="Select state key..."
          style={{ flex: 1 }}
        />
        {toggle}
      </div>
    );
  }

  let widget: React.ReactNode;
  if (type === "enum" && def.values) {
    widget = (
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{ flex: 1 }}
      >
        {!def.required && <option value="">(none)</option>}
        {def.values.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>
    );
  } else if (type === "boolean") {
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
  } else if (childType && deviceId) {
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
              : `(select ${childType})`}
          </option>
          {registered.map((c) => (
            <option key={c.local_id} value={String(c.local_id)}>
              {c.label ? `${c.label} (${c.local_id})` : `${childType} ${c.local_id}`}
            </option>
          ))}
        </select>
        {children !== undefined && registered.length === 0 && (
          <div
            style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}
          >
            No registered {childType} entries on this device yet — see the Child
            Entities tab.
          </div>
        )}
      </div>
    );
  } else {
    const isNumber =
      type === "integer" || type === "number" || type === "float";
    const numberRange =
      def.min !== undefined && def.max !== undefined
        ? `${def.min}-${def.max}`
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
        min={def.min}
        max={def.max}
        onChange={(e) => onChange(e.target.value)}
        placeholder={numberRange ?? placeholder ?? ""}
        style={{ flex: 1 }}
      />
    );
  }

  return (
    <div style={rowStyle}>
      {widget}
      {toggle}
    </div>
  );
}
