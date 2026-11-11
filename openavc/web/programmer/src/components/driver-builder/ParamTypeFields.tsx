/** The fields of a parameter's row in the Driver Builder that its type
 *  decides: the value row (help and default, then min, max, decimals and unit
 *  as `valueBoxes` says), the free-text aids, an enum's values and a Child
 *  ID's child type. Which fields belong to which type, and how each box reads
 *  what is typed, are `paramEdits.ts`. */

import type { JSX } from "react";
import type { DriverParamDef } from "../../api/types";
import { EnumValuesEditor } from "../shared/EnumValuesEditor";
import {
  boundFromText,
  decimalsFromText,
  defaultFromText,
  defaultHint,
  valueBoxes,
  type ValueBox,
} from "./paramEdits";
import { PARAM_CAPTION, PARAM_HINT, PARAM_WARNING, RowFlag } from "./commandFieldParts";

export type ParamUpdate = (partial: Partial<DriverParamDef>) => void;

interface BoxProps {
  name: string;
  def: DriverParamDef;
  onUpdate: ParamUpdate;
}

const SMALL = { width: "100%", fontSize: "var(--font-size-sm)" } as const;

function HelpBox({ def, onUpdate }: BoxProps) {
  return (
    <div>
      <span style={PARAM_CAPTION}>Help Text</span>
      <input
        value={def.help ?? def.description ?? ""}
        onChange={(e) =>
          // Always `help`; a legacy `description` goes, so the two are never shipped together.
          onUpdate({ help: e.target.value || undefined, description: undefined })
        }
        placeholder="Brief description shown in tooltips"
        style={SMALL}
      />
    </div>
  );
}

function DefaultBox({ def, onUpdate }: BoxProps) {
  return (
    <div>
      <span style={PARAM_CAPTION}>Default</span>
      <input
        value={def.default == null ? "" : String(def.default)}
        onChange={(e) => onUpdate({ default: defaultFromText(def, e.target.value) })}
        placeholder={defaultHint(def)}
        style={SMALL}
      />
    </div>
  );
}

function boundBox(field: "min" | "max", caption: string) {
  return function BoundBox({ def, onUpdate }: BoxProps) {
    return (
      <div>
        <span style={PARAM_CAPTION}>{caption}</span>
        <input
          type="number"
          value={def[field] ?? ""}
          onChange={(e) => onUpdate({ [field]: boundFromText(def, e.target.value) })}
          style={SMALL}
        />
      </div>
    );
  };
}

function DecimalsBox({ def, onUpdate }: BoxProps) {
  return (
    <div>
      <span style={PARAM_CAPTION}>Decimals</span>
      <input
        type="number"
        value={def.decimals ?? ""}
        onChange={(e) => onUpdate({ decimals: decimalsFromText(e.target.value) })}
        min={0}
        max={6}
        step={1}
        placeholder="Any"
        title="Round the value to this many decimal places on the wire (0 = whole number)."
        style={SMALL}
      />
    </div>
  );
}

function UnitBox({ name, def, onUpdate }: BoxProps) {
  return (
    <div>
      <span style={PARAM_CAPTION}>Unit</span>
      <input
        value={def.unit ?? ""}
        onChange={(e) => onUpdate({ unit: e.target.value || undefined })}
        placeholder="%, ms, dB"
        title="Shown beside the input when the command is run. Display only. It is never sent to the device."
        data-testid={`param-unit-${name}`}
        style={SMALL}
      />
    </div>
  );
}

const VALUE_BOXES: Record<ValueBox, (props: BoxProps) => JSX.Element> = {
  help: HelpBox,
  default: DefaultBox,
  min: boundBox("min", "Min"),
  max: boundBox("max", "Max"),
  decimals: DecimalsBox,
  unit: UnitBox,
};

/** Help and default, then the boxes the type has: one column each. */
export function ValueRow(props: BoxProps) {
  const boxes = valueBoxes(props.def);
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: boxes.map(() => "1fr").join(" "),
        gap: "var(--space-sm)",
        marginTop: "var(--space-sm)",
      }}
    >
      {boxes.map((box) => {
        const Box = VALUE_BOXES[box];
        return <Box key={box} {...props} />;
      })}
    </div>
  );
}

/** The value's pattern, whether spaces are trimmed and whether it is masked. */
export function TextAids({ name, def, onUpdate }: BoxProps) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr auto auto",
        gap: "var(--space-sm)",
        marginTop: "var(--space-sm)",
        alignItems: "end",
      }}
    >
      <div>
        <span style={PARAM_CAPTION}>Value Pattern</span>
        <input
          value={def.pattern ?? ""}
          onChange={(e) => onUpdate({ pattern: e.target.value || undefined })}
          placeholder="Regex the whole value must match, e.g. \d{1,3}(\.\d{1,3}){3}"
          title="Shape check for a value that can't be listed: an IP, a hostname, a fixed-length ID. The command is refused when the value doesn't match."
          data-testid={`param-pattern-${name}`}
          style={{
            width: "100%",
            fontSize: "var(--font-size-sm)",
            fontFamily: "var(--font-mono)",
          }}
        />
      </div>
      <RowFlag
        checked={def.trim !== false}
        onChange={(on) => onUpdate({ trim: on ? undefined : false })}
        title="On by default: surrounding spaces are stripped before the value goes on the wire. Turn off for raw payloads where edge whitespace is part of the protocol."
        testId={`param-trim-${name}`}
        nowrap
      >
        Trim spaces
      </RowFlag>
      <RowFlag
        checked={def.secret === true}
        onChange={(on) => onUpdate({ secret: on ? true : undefined })}
        title="Mask the input when the command is run, for a password, PIN or key. Presentation only: the value still goes to the device as typed."
        testId={`param-secret-${name}`}
        nowrap
      >
        Masked
      </RowFlag>
    </div>
  );
}

export function AllowedValues({ def, onUpdate }: Omit<BoxProps, "name">) {
  return (
    <div style={{ marginTop: "var(--space-sm)" }}>
      <span style={PARAM_CAPTION}>Allowed Values</span>
      <EnumValuesEditor values={def.values} onChange={(values) => onUpdate({ values })} />
    </div>
  );
}

/** Which declared child type a Child ID picks from, or what to declare first. */
export function ChildTypeField({
  def,
  childTypes,
  onUpdate,
}: Omit<BoxProps, "name"> & { childTypes: string[] }) {
  return (
    <div style={{ marginTop: "var(--space-sm)" }}>
      <span style={PARAM_CAPTION}>Child Type</span>
      {childTypes.length === 0 ? (
        <div style={PARAM_WARNING}>
          No child entity types declared yet. Add one in the Child Entity
          Types section above, then pick it here.
        </div>
      ) : (
        <select
          value={def.child_type ?? ""}
          onChange={(e) => onUpdate({ child_type: e.target.value || undefined })}
          style={{
            width: "100%",
            fontSize: "var(--font-size-sm)",
            fontFamily: "var(--font-mono)",
          }}
        >
          <option value="">(select a child type)</option>
          {childTypes.map((ct) => (
            <option key={ct} value={ct}>
              {ct}
            </option>
          ))}
        </select>
      )}
      <div style={PARAM_HINT}>
        The runtime command picker shows a dropdown of registered{" "}
        {def.child_type ? <code>{def.child_type}</code> : "children"} for
        this parameter. The value sent is the integer local ID.
      </div>
    </div>
  );
}
