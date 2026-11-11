/** A command's parameters in the Driver Builder, and an action's when it
 *  overrides its command's: the list (add, remove, rename, a field's write)
 *  and each parameter's row. The row places its header (name, label, type,
 *  required), the fields its type decides (`ParamTypeFields.tsx`), the value
 *  conversion, where its choices come from (`ParamOptionsEditor.tsx`) and its
 *  wire value map. The list's rules and the type's are `paramEdits.ts`. */

import { useState } from "react";
import { Trash2 } from "lucide-react";
import type { DriverParamDef } from "../../api/types";
import { readableKey } from "../shared/paramLabel";
import { ValueConversionFields } from "./ValueConversionFields";
import { WireValueMapEditor } from "./WireValueMapEditor";
import {
  PARAM_TYPE_OPTIONS,
  newParamName,
  paramMerged,
  paramsRenamed,
  retyped,
  showsTextAids,
  typeHas,
  type ParamUpdate,
  type Params,
} from "./paramEdits";
import { AllowedValues, ChildTypeField, TextAids, ValueRow } from "./ParamTypeFields";
import { ParamOptionsEditor } from "./ParamOptionsEditor";
import { PARAM_CAPTION, RowFlag } from "./commandFieldParts";

type TryRename = (next: string) => { ok: boolean; reason?: string };

/** The name box's own text: typed freely (an illegal name too, without the
 *  box being rewritten underfoot), tried on leaving the box, and kept with
 *  the refusal beside it when refused, so it can be fixed rather than guessed
 *  at. Escape puts the name back. A rename that lands remounts the row (the
 *  list keys rows by name), which starts the text over from the new name. */
function useNameDraft(name: string, tryRename: TryRename) {
  const [draftName, setDraftName] = useState(name);
  const [renameError, setRenameError] = useState<string | null>(null);

  const commit = () => {
    if (draftName === name) {
      setRenameError(null);
      return;
    }
    const result = tryRename(draftName);
    setRenameError(result.ok ? null : (result.reason ?? "Invalid name."));
  };
  const cancel = () => {
    setDraftName(name);
    setRenameError(null);
  };
  return { draftName, setDraftName, renameError, commit, cancel };
}

type NameDraft = ReturnType<typeof useNameDraft>;

function ParamNameField({ draft }: { draft: NameDraft }) {
  return (
    <div>
      <span style={PARAM_CAPTION}>Name</span>
      <input
        value={draft.draftName}
        onChange={(e) => draft.setDraftName(e.target.value)}
        onBlur={draft.commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") draft.cancel();
        }}
        style={{
          width: "100%",
          fontSize: "var(--font-size-sm)",
          fontFamily: "var(--font-mono)",
          borderColor: draft.renameError ? "var(--color-error)" : undefined,
        }}
      />
      {draft.renameError && (
        <div
          style={{
            fontSize: 11,
            color: "var(--color-error)",
            marginTop: 2,
          }}
        >
          {draft.renameError}
        </div>
      )}
    </div>
  );
}

/** The row's first line: name, label (its hint follows the name as typed),
 *  type, required and remove. */
function ParamHeader({
  name,
  def,
  childTypes,
  tryRename,
  onUpdate,
  onRemove,
}: {
  name: string;
  def: DriverParamDef;
  childTypes: string[];
  tryRename: TryRename;
  onUpdate: ParamUpdate;
  onRemove: () => void;
}) {
  const draft = useNameDraft(name, tryRename);
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr 110px auto auto",
        gap: "var(--space-sm)",
        alignItems: "end",
      }}
    >
      <ParamNameField draft={draft} />
      <div>
        <span style={PARAM_CAPTION}>Display Label</span>
        <input
          value={def.label ?? ""}
          onChange={(e) => onUpdate({ label: e.target.value || undefined })}
          placeholder={readableKey(draft.draftName.trim() || name)}
          style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
        />
      </div>
      <div>
        <span style={PARAM_CAPTION}>Type</span>
        <select
          value={def.type}
          onChange={(e) => onUpdate(retyped(def, e.target.value, childTypes))}
          style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
        >
          {PARAM_TYPE_OPTIONS.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </div>
      <RowFlag checked={!!def.required} onChange={(on) => onUpdate({ required: on || undefined })}>
        Required
      </RowFlag>
      <button
        onClick={onRemove}
        style={{
          padding: "4px",
          color: "var(--text-muted)",
          alignSelf: "center",
        }}
        title="Remove parameter"
      >
        <Trash2 size={14} />
      </button>
    </div>
  );
}

function ParamRow({
  name,
  def,
  childTypes,
  stateKeys,
  siblings,
  tryRename,
  onUpdate,
  onRemove,
}: {
  name: string;
  def: DriverParamDef;
  childTypes: string[];
  stateKeys: string[];
  /** Every param of this command, this one included: where its choices come
   *  from may cascade off a sibling. */
  siblings: Params;
  tryRename: TryRename;
  onUpdate: ParamUpdate;
  onRemove: () => void;
}) {
  return (
    <div
      style={{
        border: "1px solid var(--border-color)",
        borderRadius: "var(--border-radius)",
        padding: "var(--space-sm) var(--space-md)",
        marginBottom: "var(--space-xs)",
        background: "var(--bg-surface)",
      }}
    >
      <ParamHeader
        name={name}
        def={def}
        childTypes={childTypes}
        tryRename={tryRename}
        onUpdate={onUpdate}
        onRemove={onRemove}
      />
      <ValueRow name={name} def={def} onUpdate={onUpdate} />
      {typeHas(def, "scale") && (
        <ValueConversionFields
          forParam
          scale={def.scale}
          offset={def.offset}
          onChange={(field, value) => onUpdate({ [field]: value } as Partial<DriverParamDef>)}
        />
      )}
      {showsTextAids(def) && <TextAids name={name} def={def} onUpdate={onUpdate} />}
      {typeHas(def, "values") && <AllowedValues def={def} onUpdate={onUpdate} />}
      {typeHas(def, "child_type") && (
        <ChildTypeField def={def} childTypes={childTypes} onUpdate={onUpdate} />
      )}
      <ParamOptionsEditor
        name={name}
        def={def}
        siblings={siblings}
        stateKeys={stateKeys}
        onUpdate={onUpdate}
      />
      <WireValueMapEditor map={def.map} onChange={(map) => onUpdate({ map })} />
    </div>
  );
}

export function ParamEditor({
  params,
  childTypes,
  stateKeys,
  onChange,
  onRename,
}: {
  params: Params;
  /** The driver's child types, offered to a Child ID parameter. Empty when
   *  the driver declares none. */
  childTypes: string[];
  /** The driver's state variables, suggested for a list the device
   *  publishes. Only suggested: the key is device-relative and may name a
   *  state the driver writes at runtime without declaring it. */
  stateKeys: string[];
  onChange: (params: Params) => void;
  /** A rename, with the renamed params: a command also renames the
   *  parameter's placeholder in its own strings. Without it, onChange. */
  onRename?: (from: string, to: string, params: Params) => void;
}) {
  const removeParam = (name: string) => {
    const next = { ...params };
    delete next[name];
    onChange(next);
  };

  const renameParam = (oldName: string, newName: string) => {
    const result = paramsRenamed(params, oldName, newName);
    if (result.params) {
      if (onRename) onRename(oldName, result.name!, result.params);
      else onChange(result.params);
    }
    return { ok: result.ok, reason: result.reason };
  };

  return (
    <div style={{ marginTop: "var(--space-md)" }}>
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
          marginBottom: "var(--space-xs)",
        }}
      >
        Parameters
      </div>
      {Object.keys(params).map((name) => (
        <ParamRow
          key={name}
          name={name}
          def={params[name]}
          childTypes={childTypes}
          stateKeys={stateKeys}
          siblings={params}
          tryRename={(next) => renameParam(name, next)}
          onUpdate={(partial) => onChange({ ...params, [name]: paramMerged(params[name], partial) })}
          onRemove={() => removeParam(name)}
        />
      ))}
      <button
        onClick={() => onChange({ ...params, [newParamName(params)]: { type: "string" } })}
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--accent)",
          padding: "var(--space-xs) 0",
        }}
      >
        + Add Parameter
      </button>
    </div>
  );
}
