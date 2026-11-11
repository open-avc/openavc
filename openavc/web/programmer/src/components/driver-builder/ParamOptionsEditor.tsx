/** Where a parameter's choices come from, in a parameter's row of the Driver
 *  Builder: one select for the source (whatever the type allows, a list the
 *  device publishes, or the controls of a child picked in another parameter),
 *  the source's own field, a cascade's narrowing, and Input Type Follows. The
 *  fields' rules are `paramChoices.ts`. */

import { useState } from "react";
import type { DriverParamDef } from "../../api/types";
import {
  CHOICE_SOURCES,
  cascadeRepointed,
  cascadeSiblings,
  childIdSiblings,
  choiceSource,
  narrowed,
  showsTypeFrom,
  sourceChanged,
  typeFollows,
  typesTicked,
  unitsFromText,
  type CascadeDef,
  type CascadeType,
} from "./paramChoices";
import type { ParamUpdate } from "./paramEdits";
import { PARAM_CAPTION, PARAM_HINT, PARAM_WARNING } from "./commandFieldParts";

const MONO_SELECT = {
  width: "100%",
  fontSize: "var(--font-size-sm)",
  fontFamily: "var(--font-mono)",
} as const;

/** The control types a cascade can be narrowed to, named as the child
 *  state-variable editor names them. */
const CASCADE_TYPES: { value: CascadeType; label: string }[] = [
  { value: "boolean", label: "Boolean" },
  { value: "number", label: "Number" },
  { value: "integer", label: "Integer" },
  { value: "float", label: "Float" },
  { value: "enum", label: "Enum" },
  { value: "string", label: "String" },
];

/** A cascade for a command that works on only some controls: a toggle takes
 *  Boolean controls, a level step takes Numbers in dB. Nothing ticked and no
 *  unit means every control is offered. */
function CascadeNarrowing({
  name,
  optionsFrom,
  onChange,
}: {
  name: string;
  optionsFrom: CascadeDef;
  onChange: (next: CascadeDef) => void;
}) {
  // The unit box keeps what is typed ("dB, " mid-entry) and writes the parsed
  // list, so a trailing comma does not vanish under the cursor.
  const [unitText, setUnitText] = useState((optionsFrom.units ?? []).join(", "));
  const types = optionsFrom.types ?? [];
  const write = (patch: Partial<CascadeDef>) => onChange(narrowed(optionsFrom, patch));

  return (
    <div style={{ marginTop: "var(--space-xs)" }}>
      <span style={PARAM_CAPTION}>Only Offer Controls Of Type</span>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-sm)" }}>
        {CASCADE_TYPES.map((t) => (
          <label key={t.value} style={{ fontSize: "var(--font-size-sm)", display: "flex", gap: 4, alignItems: "center" }}>
            <input
              type="checkbox"
              checked={types.includes(t.value)}
              onChange={(e) => write({ types: typesTicked(types, t.value, e.target.checked) })}
              data-testid={`param-options-from-type-${name}-${t.value}`}
            />
            {t.label}
          </label>
        ))}
      </div>
      <span style={{ display: "block", fontSize: "11px", color: "var(--text-muted)", margin: "var(--space-xs) 0 2px" }}>
        Only Offer Controls With Unit
      </span>
      <input
        value={unitText}
        onChange={(e) => {
          setUnitText(e.target.value);
          write({ units: unitsFromText(e.target.value) });
        }}
        placeholder="Any unit, or e.g. dB"
        data-testid={`param-options-from-units-${name}`}
        style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
      />
      <div style={PARAM_HINT}>
        For a command that works on only some controls: a toggle takes Boolean
        controls, a level step takes Numbers in dB. Leave both empty to offer
        every control. Separate several units with commas.
      </div>
    </div>
  );
}

/** The state variable a device publishes its list as, with the driver's
 *  variables suggested (only suggested: the driver may write one at runtime
 *  that it does not declare). */
function PublishedListField({
  name,
  def,
  stateKeys,
  onUpdate,
}: {
  name: string;
  def: DriverParamDef;
  stateKeys: string[];
  onUpdate: ParamUpdate;
}) {
  return (
    <div style={{ marginTop: "var(--space-xs)" }}>
      <input
        value={def.options_state ?? ""}
        onChange={(e) => onUpdate({ options_state: e.target.value })}
        list={`param-options-state-list-${name}`}
        placeholder="State variable holding the list, e.g. input_list"
        data-testid={`param-options-state-${name}`}
        style={MONO_SELECT}
      />
      <datalist id={`param-options-state-list-${name}`}>
        {stateKeys.map((key) => (
          <option key={key} value={key} />
        ))}
      </datalist>
      <div style={PARAM_HINT}>
        The driver publishes the choices as this state variable (a JSON list
        of values, or of value/label pairs). The picker offers them and
        still accepts anything typed.
      </div>
    </div>
  );
}

/** Which child_id parameter a cascade reads, or what to add first, and its
 *  narrowing. */
function CascadeField({
  name,
  def,
  childIds,
  onUpdate,
}: {
  name: string;
  def: DriverParamDef;
  childIds: string[];
  onUpdate: ParamUpdate;
}) {
  return (
    <div style={{ marginTop: "var(--space-xs)" }}>
      {childIds.length === 0 ? (
        <div style={PARAM_WARNING}>
          This command has no Child ID parameter to cascade from. Add one
          (Type: Child ID), then pick it here.
        </div>
      ) : (
        <select
          value={def.options_from?.param ?? ""}
          onChange={(e) => onUpdate(cascadeRepointed(def, e.target.value))}
          data-testid={`param-options-from-${name}`}
          style={MONO_SELECT}
        >
          <option value="">(select a Child ID parameter)</option>
          {childIds.map((sName) => (
            <option key={sName} value={sName}>
              {sName}
            </option>
          ))}
        </select>
      )}
      <div style={PARAM_HINT}>
        Picking a child in that parameter fills this one with the child's
        own controls.
      </div>
      {def.options_from && (
        <CascadeNarrowing
          name={name}
          optionsFrom={def.options_from}
          onChange={(next) => onUpdate({ options_from: next })}
        />
      )}
    </div>
  );
}

/** Which cascade this parameter's input type follows; one that is set but is
 *  not a cascade stays listed so it can be seen and cleared. */
function TypeFromField({
  name,
  def,
  cascades,
  onUpdate,
}: {
  name: string;
  def: DriverParamDef;
  cascades: string[];
  onUpdate: ParamUpdate;
}) {
  const current = def.type_from?.param;
  return (
    <div style={{ marginTop: "var(--space-sm)" }}>
      <span style={PARAM_CAPTION}>Input Type Follows</span>
      <select
        value={current ?? ""}
        onChange={(e) => onUpdate(typeFollows(e.target.value))}
        data-testid={`param-type-from-${name}`}
        style={MONO_SELECT}
      >
        <option value="">Nothing (use the type above)</option>
        {cascades.map((sName) => (
          <option key={sName} value={sName}>
            {sName}
          </option>
        ))}
        {current && !cascades.includes(current) && (
          <option value={current}>{current} (not a cascade)</option>
        )}
      </select>
      <div style={PARAM_HINT}>
        This box takes its type from whichever control that parameter picked:
        a number spinner for a level, Yes/No for a mute.
      </div>
    </div>
  );
}

export function ParamOptionsEditor({
  name,
  def,
  siblings,
  stateKeys,
  onUpdate,
}: {
  name: string;
  def: DriverParamDef;
  siblings: Record<string, DriverParamDef>;
  stateKeys: string[];
  onUpdate: ParamUpdate;
}) {
  const childIds = childIdSiblings(siblings, name);
  const cascades = cascadeSiblings(siblings, name);
  const source = choiceSource(def);

  return (
    <div style={{ marginTop: "var(--space-sm)" }}>
      <span style={PARAM_CAPTION}>Choices Come From</span>
      <select
        value={source}
        onChange={(e) => onUpdate(sourceChanged(e.target.value, def, { stateKeys, childIds }))}
        data-testid={`param-options-source-${name}`}
        style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
      >
        {CHOICE_SOURCES.map((s) => (
          <option key={s.value} value={s.value}>
            {s.label}
          </option>
        ))}
      </select>

      {source === "state" && (
        <PublishedListField name={name} def={def} stateKeys={stateKeys} onUpdate={onUpdate} />
      )}
      {source === "cascade" && (
        <CascadeField name={name} def={def} childIds={childIds} onUpdate={onUpdate} />
      )}
      {showsTypeFrom(def, cascades) && (
        <TypeFromField name={name} def={def} cascades={cascades} onUpdate={onUpdate} />
      )}
    </div>
  );
}
