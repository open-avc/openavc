/** A command's Reports and Sets State fields in the Driver Builder: which
 *  state variable a status query's reply reports, and the variables the
 *  command changes, each to a parameter's value or a literal. What they may
 *  name and the rows' edits are `commandSemantics.ts`. */

import { Trash2 } from "lucide-react";
import type { DriverCommandDef, DriverDefinition } from "../../api/types";
import {
  canAddRow,
  coerceLiteral,
  commandStateScope,
  isParamReference,
  nothingToDeclare,
  readQueryFor,
  readSets,
  setsWrite,
  withNewRow,
  withoutRow,
  withRowKey,
  withRowValue,
  type CommandStateScope,
  type SetsTable,
  type SetValue,
} from "./commandSemantics";
import { FIELD_HINT, FIELD_LABEL } from "./commandFieldParts";

const MONO_SMALL = {
  fontSize: "var(--font-size-sm)",
  fontFamily: "var(--font-mono)",
} as const;

/** A variable select's options: the device's, then (on a child-addressed
 *  command) the child type's in their own group, and the current name first
 *  when it is not declared, so the select does not show another. `exclude`
 *  is the other rows' names; it never holds the current one. */
function VarOptions({
  scope,
  current,
  exclude,
  format = (v) => v,
}: {
  scope: CommandStateScope;
  current: string;
  exclude?: Set<string>;
  format?: (v: string) => string;
}) {
  const offered = (names: string[]) => names.filter((v) => !exclude?.has(v));
  const dev = offered(scope.deviceVarNames);
  const chi = offered(scope.childVarNames);
  const unknown = current !== "" && !dev.includes(current) && !chi.includes(current);
  const options = (names: string[]) =>
    names.map((v) => (
      <option key={v} value={v}>
        {format(v)}
      </option>
    ));
  return (
    <>
      {unknown && <option value={current}>{format(current)} (not declared)</option>}
      {scope.childType !== null ? (
        <>
          {dev.length > 0 && <optgroup label="Device variables">{options(dev)}</optgroup>}
          {chi.length > 0 && <optgroup label={`${scope.childType} variables`}>{options(chi)}</optgroup>}
        </>
      ) : (
        options(dev)
      )}
    </>
  );
}

function ReportsField({
  scope,
  value,
  onChange,
}: {
  scope: CommandStateScope;
  value: string;
  onChange: (queryFor: string | undefined) => void;
}) {
  return (
    <div style={{ marginBottom: "var(--space-md)" }}>
      <label style={FIELD_LABEL}>Reports</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value || undefined)}
        style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
      >
        <option value="">Reports (auto)</option>
        <VarOptions scope={scope} current={value} format={(v) => `Reports ${v}`} />
      </select>
      <div style={FIELD_HINT}>
        For status queries: the device answers this command by reporting
        this variable, and the simulator replies with its value, so Live
        Test and simulation answer the query correctly.
      </div>
    </div>
  );
}

/** One Sets State row: the variable, a parameter or "literal…", the literal
 *  (read as the variable's type when the box is left) and remove. */
function SetsRow({
  scope,
  varName,
  value,
  otherKeys,
  paramNames,
  onKey,
  onValue,
  onRemove,
}: {
  scope: CommandStateScope;
  varName: string;
  value: SetValue;
  otherKeys: Set<string>;
  paramNames: string[];
  onKey: (key: string) => void;
  onValue: (value: SetValue) => void;
  onRemove: () => void;
}) {
  const strValue = typeof value === "string" ? value : String(value);
  const isParamMode = isParamReference(value, paramNames);
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: isParamMode ? "1fr 1fr auto" : "1fr 1fr 1fr auto",
        gap: "var(--space-sm)",
        marginBottom: "var(--space-xs)",
        alignItems: "center",
      }}
    >
      <select
        value={varName}
        onChange={(e) => onKey(e.target.value)}
        title="State variable this command sets"
        style={MONO_SMALL}
      >
        <VarOptions scope={scope} current={varName} exclude={otherKeys} />
      </select>
      <select
        value={isParamMode ? strValue : "__literal__"}
        onChange={(e) => onValue(e.target.value === "__literal__" ? "" : e.target.value)}
        title="Set it to a parameter's value, or a literal"
        style={MONO_SMALL}
      >
        {paramNames.map((p) => (
          <option key={p} value={`{${p}}`}>
            {`{${p}}`}
          </option>
        ))}
        <option value="__literal__">literal…</option>
      </select>
      {!isParamMode && (
        <input
          value={strValue}
          onChange={(e) => onValue(e.target.value)}
          onBlur={(e) => onValue(coerceLiteral(e.target.value, scope.varType(varName)))}
          placeholder={scope.varType(varName) === "boolean" ? "true / false" : "value"}
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "var(--font-size-sm)",
          }}
        />
      )}
      <button onClick={onRemove} style={{ padding: "2px", color: "var(--text-muted)" }}>
        <Trash2 size={14} />
      </button>
    </div>
  );
}

function SetsStateField({
  scope,
  sets,
  paramNames,
  onChange,
}: {
  scope: CommandStateScope;
  sets: SetsTable;
  paramNames: string[];
  onChange: (next: SetsTable) => void;
}) {
  const addRow = () => {
    const next = withNewRow(scope, sets, paramNames);
    if (next) onChange(next);
  };
  return (
    <div>
      <label style={FIELD_LABEL}>Sets State</label>
      {Object.entries(sets).map(([varName, value], i) => (
        <SetsRow
          key={i}
          scope={scope}
          varName={varName}
          value={value}
          otherKeys={new Set(Object.keys(sets).filter((k) => k !== varName))}
          paramNames={paramNames}
          onKey={(key) => onChange(withRowKey(sets, i, key))}
          onValue={(v) => onChange(withRowValue(sets, i, v))}
          onRemove={() => onChange(withoutRow(sets, i))}
        />
      ))}
      {canAddRow(scope, sets) && (
        <button
          onClick={addRow}
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--accent)",
            padding: "var(--space-xs) 0",
          }}
        >
          + Set a variable
        </button>
      )}
      <div style={FIELD_HINT}>
        State variables this command sets on the device, to a parameter's
        value or a literal. The auto-generated simulator applies these when
        the command fires, so Live Test and simulation show the effect.
        {scope.childType !== null && (
          <>
            {" "}
            Variables of the <code>{scope.childType}</code> child type apply to the
            child addressed by the command's Child ID parameter.
          </>
        )}
      </div>
    </div>
  );
}

/** The Reports and Sets State fields, left off a command with nothing to name
 *  and nothing written. */
export function CommandSemanticsEditor({
  cmd,
  draft,
  onUpdate,
}: {
  cmd: DriverCommandDef;
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverCommandDef>) => void;
}) {
  const scope = commandStateScope(cmd, draft);
  const sets = readSets(cmd);
  const queryFor = readQueryFor(cmd);
  if (nothingToDeclare(scope, sets, queryFor)) return null;
  return (
    <div style={{ marginTop: "var(--space-md)" }}>
      <ReportsField scope={scope} value={queryFor} onChange={(query_for) => onUpdate({ query_for })} />
      <SetsStateField
        scope={scope}
        sets={sets}
        paramNames={Object.keys(cmd.params ?? {})}
        onChange={(next) => onUpdate(setsWrite(next))}
      />
    </div>
  );
}
