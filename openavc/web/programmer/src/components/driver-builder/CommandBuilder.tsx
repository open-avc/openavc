import { useEffect, useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import type {
  DriverCommandDef,
  DriverDefinition,
  DriverParamDef,
} from "../../api/types";
import { readableKey } from "../shared/paramLabel";
import { IdRenameInput, type RenameResult } from "./IdRenameInput";
import { ValueConversionFields } from "./ValueConversionFields";
import { WireValueMapEditor } from "./WireValueMapEditor";
import { commandsRenamed, newCommand, renamePlaceholder, type CommandUpdate } from "./commandEdits";
import { OfflineFlag, RestartWindowField, SleepsDeviceField } from "./CommandAwayFields";
import { CommandConfirmField } from "./CommandConfirmField";
import { ReadBackField, readBackForm } from "./ReadBackField";
import { CommandSendFields, UdpToggle } from "./CommandSendFields";
import { FIELD_HINT, FIELD_LABEL, PARAM_CAPTION, RowFlag } from "./commandFieldParts";
import { PARAM_TYPE_OPTIONS, retyped, showsTextAids, typeHas } from "./paramEdits";
import { AllowedValues, ChildTypeField, TextAids, ValueRow } from "./ParamTypeFields";
import { CommandSemanticsEditor } from "./CommandSemanticsEditor";
import { ParamOptionsEditor } from "./ParamOptionsEditor";

interface CommandBuilderProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

/** A command's row in the list: its id and label, opening or closing its
 *  card, and removing it (without opening it). */
function CommandHeader({
  name,
  label,
  isOpen,
  onToggle,
  onRemove,
}: {
  name: string;
  label: string;
  isOpen: boolean;
  onToggle: () => void;
  onRemove: () => void;
}) {
  return (
    <button
      onClick={onToggle}
      style={{
        display: "flex",
        alignItems: "center",
        width: "100%",
        padding: "var(--space-sm) var(--space-md)",
        gap: "var(--space-sm)",
        textAlign: "left",
      }}
    >
      {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
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
        {label}
      </span>
      <button
        onClick={(e) => {
          e.stopPropagation();
          onRemove();
        }}
        style={{ padding: "2px", color: "var(--text-muted)" }}
      >
        <Trash2 size={14} />
      </button>
    </button>
  );
}

/** What a command is called: its id (renamed through the list, which carries
 *  the commands naming it), the label people see and its help text. */
function CommandNaming({
  name,
  cmd,
  onUpdate,
  onRename,
}: {
  name: string;
  cmd: DriverCommandDef;
  onUpdate: CommandUpdate;
  onRename: (next: string) => RenameResult;
}) {
  return (
    <>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "var(--space-md)",
          marginBottom: "var(--space-md)",
        }}
      >
        <div>
          <label style={FIELD_LABEL}>Command ID</label>
          <IdRenameInput value={name} sanitize={(raw) => raw} onCommit={onRename} />
        </div>
        <div>
          <label style={FIELD_LABEL}>Display Label</label>
          <input
            value={cmd.label}
            onChange={(e) => onUpdate({ label: e.target.value })}
            style={{ width: "100%" }}
          />
        </div>
      </div>

      <div style={{ marginBottom: "var(--space-md)" }}>
        <label style={FIELD_LABEL}>Help Text</label>
        <input
          value={cmd.help ?? ""}
          onChange={(e) => onUpdate({ help: e.target.value })}
          placeholder="Brief description of what this command does"
          style={{ width: "100%" }}
        />
        <div style={FIELD_HINT}>Shown to users when selecting this command.</div>
      </div>
    </>
  );
}

/** One command's open card, its fields in the order drawn. Placement only:
 *  every field writes through `onUpdate`. */
function CommandCard({
  name,
  cmd,
  draft,
  onUpdate,
  onRename,
}: {
  name: string;
  cmd: DriverCommandDef;
  draft: DriverDefinition;
  onUpdate: CommandUpdate;
  onRename: (next: string) => RenameResult;
}) {
  return (
    <div
      style={{
        padding: "var(--space-md)",
        borderTop: "1px solid var(--border-color)",
      }}
    >
      <CommandNaming name={name} cmd={cmd} onUpdate={onUpdate} onRename={onRename} />
      <CommandSendFields cmd={cmd} draft={draft} onUpdate={onUpdate} />
      <UdpToggle on={!!cmd.udp} onUpdate={onUpdate} />
      <OfflineFlag
        value={cmd.available_offline}
        onChange={(available_offline) => onUpdate({ available_offline })}
      />
      <RestartWindowField
        value={cmd.restarts_device_for}
        onChange={(restarts_device_for) => onUpdate({ restarts_device_for })}
      />
      <SleepsDeviceField
        commandId={name}
        value={cmd.sleeps_device}
        commands={draft.commands}
        onChange={(sleeps_device) => onUpdate({ sleeps_device })}
      />
      <CommandConfirmField confirm={cmd.confirm} onChange={(confirm) => onUpdate({ confirm })} />
      <ParamEditor
        params={cmd.params ?? {}}
        childTypes={Object.keys(draft.child_entity_types ?? {})}
        stateKeys={Object.keys(draft.state_variables ?? {})}
        onChange={(params) => onUpdate({ params })}
        onRename={(from, to, params) =>
          onUpdate({ params, ...renamePlaceholder(cmd, from, to) })
        }
      />
      <CommandSemanticsEditor cmd={cmd} draft={draft} onUpdate={onUpdate} />
      {!cmd.udp && (
        <ReadBackField
          commandId={name}
          value={cmd.read_back}
          form={readBackForm(cmd, draft.transport)}
          commandNames={Object.keys(draft.commands)}
          onChange={(read_back) => onUpdate({ read_back })}
        />
      )}
    </div>
  );
}

/** The Commands section of the Driver Builder: the driver's commands as a
 *  list, at most one open, each written back into the draft whole. */
export function CommandBuilder({ draft, onUpdate }: CommandBuilderProps) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const commands = draft.commands;
  const commandNames = Object.keys(commands);

  const addCommand = () => {
    const [name, initial] = newCommand(commands, draft.transport);
    onUpdate({
      commands: { ...commands, [name]: initial },
    });
    setExpanded(name);
  };

  const removeCommand = (name: string) => {
    const next = { ...commands };
    delete next[name];
    onUpdate({ commands: next });
    if (expanded === name) setExpanded(null);
  };

  const updateCommand = (name: string, partial: Partial<DriverCommandDef>) => {
    // Merge, then strip any keys whose value is `undefined` so we don't
    // serialize `key: null` into YAML when the caller wanted to delete a
    // legacy field (e.g., `description` after migrating to `help`).
    const merged = { ...commands[name], ...partial } as Record<string, unknown>;
    for (const k of Object.keys(merged)) {
      if (merged[k] === undefined) delete merged[k];
    }
    onUpdate({
      commands: {
        ...commands,
        [name]: merged as unknown as DriverCommandDef,
      },
    });
  };

  const renameCommand = (oldName: string, newName: string): RenameResult => {
    const result = commandsRenamed(commands, oldName, newName);
    if (result.commands) {
      onUpdate({ commands: result.commands });
      if (expanded === oldName) setExpanded(newName);
    }
    return { ok: result.ok, reason: result.reason };
  };

  return (
    <div>
      {commandNames.length === 0 && (
        <p
          style={{
            color: "var(--text-muted)",
            fontSize: "var(--font-size-sm)",
            marginBottom: "var(--space-md)",
          }}
        >
          No commands defined yet. Add commands that this driver can send to the
          device.
        </p>
      )}

      {commandNames.map((name) => {
        const isOpen = expanded === name;
        return (
          <div
            key={name}
            style={{
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              marginBottom: "var(--space-sm)",
              background: "var(--bg-surface)",
            }}
          >
            <CommandHeader
              name={name}
              label={commands[name].label}
              isOpen={isOpen}
              onToggle={() => setExpanded(isOpen ? null : name)}
              onRemove={() => removeCommand(name)}
            />
            {isOpen && (
              <CommandCard
                name={name}
                cmd={commands[name]}
                draft={draft}
                onUpdate={(partial) => updateCommand(name, partial)}
                onRename={(next) => renameCommand(name, next)}
              />
            )}
          </div>
        );
      })}

      <button
        onClick={addCommand}
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
        <Plus size={14} /> Add Command
      </button>
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
  params: Record<string, DriverParamDef>;
  /** Driver-declared child types — passed to ParamRow so the
   *  ``child_id`` type can render a dropdown of valid child types. Empty
   *  when the driver hasn't declared any. */
  childTypes: string[];
  /** Driver-declared state variable names — suggestions for a param whose
   *  options come from a list the device publishes. Only suggestions: the
   *  key is device-relative and may name a state the driver writes at
   *  runtime rather than one declared up front. */
  stateKeys: string[];
  onChange: (params: Record<string, DriverParamDef>) => void;
  /** A rename, with the renamed params: a command also renames the
   *  parameter's placeholder in its own strings. Without it, onChange. */
  onRename?: (from: string, to: string, params: Record<string, DriverParamDef>) => void;
}) {
  const paramNames = Object.keys(params);

  const addParam = () => {
    let counter = paramNames.length + 1;
    let name = `param${counter}`;
    while (name in params) {
      counter++;
      name = `param${counter}`;
    }
    onChange({ ...params, [name]: { type: "string" } });
  };

  const removeParam = (name: string) => {
    const next = { ...params };
    delete next[name];
    onChange(next);
  };

  const renameParam = (oldName: string, newName: string): { ok: boolean; reason?: string } => {
    const trimmed = newName.trim();
    if (!trimmed || trimmed === oldName) return { ok: true };
    if (trimmed in params) {
      return { ok: false, reason: `A parameter named "${trimmed}" already exists.` };
    }
    if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(trimmed)) {
      return {
        ok: false,
        reason: "Use letters, digits, and underscores only. Must start with a letter or underscore.",
      };
    }
    const next: typeof params = {};
    for (const [k, v] of Object.entries(params)) {
      next[k === oldName ? trimmed : k] = v;
    }
    if (onRename) onRename(oldName, trimmed, next);
    else onChange(next);
    return { ok: true };
  };

  const updateParam = (name: string, partial: Partial<DriverParamDef>) => {
    const merged = { ...params[name], ...partial } as Record<string, unknown>;
    for (const k of Object.keys(merged)) {
      if (merged[k] === undefined) delete merged[k];
    }
    onChange({ ...params, [name]: merged as unknown as DriverParamDef });
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
      {paramNames.map((name) => (
        <ParamRow
          key={name}
          name={name}
          def={params[name]}
          childTypes={childTypes}
          stateKeys={stateKeys}
          siblings={params}
          tryRename={(next) => renameParam(name, next)}
          onUpdate={(partial) => updateParam(name, partial)}
          onRemove={() => removeParam(name)}
        />
      ))}
      <button
        onClick={addParam}
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
  /** Every param of this command, including this one — the option-source
   *  editor cascades off a sibling, so it needs to see them. */
  siblings: Record<string, DriverParamDef>;
  tryRename: (next: string) => { ok: boolean; reason?: string };
  onUpdate: (partial: Partial<DriverParamDef>) => void;
  onRemove: () => void;
}) {
  // Local buffer for the name input — lets the user keep typing illegal
  // characters without us rewriting the field underfoot. We commit (or
  // revert) on blur, and surface the rejection reason inline so the user
  // knows what happened.
  const [draftName, setDraftName] = useState(name);
  const [renameError, setRenameError] = useState<string | null>(null);

  // Re-sync if the canonical name changes from outside (parent rename).
  useEffect(() => {
    setDraftName(name);
    setRenameError(null);
  }, [name]);

  const commitRename = () => {
    if (draftName === name) {
      setRenameError(null);
      return;
    }
    const result = tryRename(draftName);
    if (!result.ok) {
      setRenameError(result.reason ?? "Invalid name.");
      // Leave the bad text in the input so the user can fix it instead
      // of guessing what they typed.
    } else {
      setRenameError(null);
    }
  };

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
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr 110px auto auto",
          gap: "var(--space-sm)",
          alignItems: "end",
        }}
      >
        <div>
          <span style={PARAM_CAPTION}>Name</span>
          <input
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
              if (e.key === "Escape") {
                setDraftName(name);
                setRenameError(null);
              }
            }}
            style={{
              width: "100%",
              fontSize: "var(--font-size-sm)",
              fontFamily: "var(--font-mono)",
              borderColor: renameError ? "var(--color-error)" : undefined,
            }}
          />
          {renameError && (
            <div
              style={{
                fontSize: 11,
                color: "var(--color-error)",
                marginTop: 2,
              }}
            >
              {renameError}
            </div>
          )}
        </div>
        <div>
          <span style={PARAM_CAPTION}>Display Label</span>
          <input
            value={def.label ?? ""}
            onChange={(e) =>
              onUpdate({ label: e.target.value || undefined })
            }
            placeholder={readableKey(draftName.trim() || name)}
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
        <RowFlag
          checked={!!def.required}
          onChange={(on) => onUpdate({ required: on || undefined })}
        >
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

      <ValueRow name={name} def={def} onUpdate={onUpdate} />
      {typeHas(def, "scale") && (
        <ValueConversionFields
          forParam
          scale={def.scale}
          offset={def.offset}
          onChange={(field, value) =>
            onUpdate({ [field]: value } as Partial<DriverParamDef>)
          }
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
      <WireValueMapEditor
        map={def.map}
        onChange={(map) => onUpdate({ map })}
      />
    </div>
  );
}
