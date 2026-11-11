import { useEffect, useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import type {
  DriverCommandDef,
  DriverDefinition,
  DriverParamDef,
} from "../../api/types";
import { EnumValuesEditor } from "../shared/EnumValuesEditor";
import { readableKey } from "../shared/paramLabel";
import { IdRenameInput, type RenameResult } from "./IdRenameInput";
import { ValueConversionFields } from "./ValueConversionFields";
import { WireValueMapEditor } from "./WireValueMapEditor";
import { commandsRenamed, newCommand, renamePlaceholder, type CommandUpdate } from "./commandEdits";
import { OfflineFlag, RestartWindowField, SleepsDeviceField } from "./CommandAwayFields";
import { CommandConfirmField } from "./CommandConfirmField";
import { ReadBackField, readBackForm } from "./ReadBackField";
import { CommandSendFields, UdpToggle } from "./CommandSendFields";
import { FIELD_HINT, FIELD_LABEL } from "./commandFieldParts";
import { CommandSemanticsEditor } from "./CommandSemanticsEditor";

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

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "11px",
    color: "var(--text-muted)",
    marginBottom: 2,
  };
  const isNumeric = def.type === "integer" || def.type === "number";
  const isEnum = def.type === "enum";
  const isBool = def.type === "boolean";
  const isChildId = def.type === "child_id";
  const showUnit = isNumeric || def.unit !== undefined;

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
          <span style={labelStyle}>Name</span>
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
          <span style={labelStyle}>Display Label</span>
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
          <span style={labelStyle}>Type</span>
          <select
            value={def.type}
            onChange={(e) => {
              const t = e.target.value;
              const partial: Partial<DriverParamDef> = { type: t };
              // Strip type-incompatible fields when switching types so
              // round-tripped YAML stays clean.
              if (t !== "integer" && t !== "number") {
                partial.min = undefined;
                partial.max = undefined;
                partial.scale = undefined;
                partial.offset = undefined;
              }
              if (t !== "number") {
                partial.decimals = undefined;
              }
              if (t !== "enum") {
                partial.values = undefined;
              }
              if (t !== "child_id") {
                partial.child_type = undefined;
              } else {
                // Default the child_type to the first declared type so the
                // param is valid the moment it's switched (if any exist).
                partial.child_type = def.child_type ?? childTypes[0];
              }
              onUpdate(partial);
            }}
            style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
          >
            <option value="string">String</option>
            <option value="integer">Integer</option>
            <option value="number">Number</option>
            <option value="boolean">Boolean</option>
            <option value="enum">Enum</option>
            <option value="child_id">Child ID</option>
          </select>
        </div>
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            paddingBottom: 6,
          }}
        >
          <input
            type="checkbox"
            checked={!!def.required}
            onChange={(e) =>
              onUpdate({ required: e.target.checked || undefined })
            }
          />
          Required
        </label>
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

      <div
        style={{
          display: "grid",
          gridTemplateColumns: [
            "1fr", // Help Text
            "1fr", // Default
            ...(isNumeric ? ["1fr", "1fr"] : []), // Min, Max
            ...(def.type === "number" ? ["1fr"] : []), // Decimals
            ...(showUnit ? ["1fr"] : []), // Unit
          ].join(" "),
          gap: "var(--space-sm)",
          marginTop: "var(--space-sm)",
        }}
      >
        <div>
          <span style={labelStyle}>Help Text</span>
          <input
            value={def.help ?? def.description ?? ""}
            onChange={(e) => {
              const v = e.target.value;
              // Always write `help`. If the existing def used `description`
              // (legacy), drop it so we don't ship two equivalent fields.
              onUpdate({
                help: v || undefined,
                description: undefined,
              });
            }}
            placeholder="Brief description shown in tooltips"
            style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
          />
        </div>
        <div>
          <span style={labelStyle}>Default</span>
          <input
            value={def.default == null ? "" : String(def.default)}
            onChange={(e) => {
              const raw = e.target.value;
              if (!raw) {
                onUpdate({ default: undefined });
                return;
              }
              if (def.type === "integer") {
                const n = parseInt(raw, 10);
                onUpdate({ default: Number.isFinite(n) ? n : raw });
              } else if (def.type === "number") {
                const n = parseFloat(raw);
                onUpdate({ default: Number.isFinite(n) ? n : raw });
              } else if (def.type === "boolean") {
                onUpdate({ default: raw === "true" });
              } else {
                onUpdate({ default: raw });
              }
            }}
            placeholder={
              isBool ? "true / false" : isEnum ? "must be one of values" : ""
            }
            style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
          />
        </div>
        {isNumeric && (
          <>
            <div>
              <span style={labelStyle}>Min</span>
              <input
                type="number"
                value={def.min ?? ""}
                onChange={(e) => {
                  const v = e.target.value;
                  onUpdate({
                    min:
                      v === ""
                        ? undefined
                        : def.type === "integer"
                          ? parseInt(v, 10)
                          : parseFloat(v),
                  });
                }}
                style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
              />
            </div>
            <div>
              <span style={labelStyle}>Max</span>
              <input
                type="number"
                value={def.max ?? ""}
                onChange={(e) => {
                  const v = e.target.value;
                  onUpdate({
                    max:
                      v === ""
                        ? undefined
                        : def.type === "integer"
                          ? parseInt(v, 10)
                          : parseFloat(v),
                  });
                }}
                style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
              />
            </div>
            {def.type === "number" && (
              <div>
                <span style={labelStyle}>Decimals</span>
                <input
                  type="number"
                  value={def.decimals ?? ""}
                  onChange={(e) => {
                    const v = e.target.value;
                    onUpdate({ decimals: v === "" ? undefined : parseInt(v, 10) });
                  }}
                  min={0}
                  max={6}
                  step={1}
                  placeholder="Any"
                  title="Round the value to this many decimal places on the wire (0 = whole number)."
                  style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
                />
              </div>
            )}
          </>
        )}
        {/* Shown for numeric params, where a unit is the usual need — and for
            any param that already carries one, so a value set by hand in YAML
            stays visible and editable instead of silently kept. */}
        {showUnit && (
          <div>
            <span style={labelStyle}>Unit</span>
            <input
              value={def.unit ?? ""}
              onChange={(e) => onUpdate({ unit: e.target.value || undefined })}
              placeholder="%, ms, dB"
              title="Shown beside the input when the command is run. Display only. It is never sent to the device."
              data-testid={`param-unit-${name}`}
              style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
            />
          </div>
        )}
      </div>

      {isNumeric && (
        <ValueConversionFields
          forParam
          scale={def.scale}
          offset={def.offset}
          onChange={(field, value) =>
            onUpdate({ [field]: value } as Partial<DriverParamDef>)
          }
        />
      )}

      {/* Free-text aids. Shown for string params, where the runtime applies
          them — and for any param that already carries one, so a value set by
          hand in YAML stays visible and editable instead of silently kept. */}
      {(def.type === "string" ||
        def.pattern !== undefined ||
        def.trim !== undefined ||
        def.secret !== undefined) && (
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
            <span style={labelStyle}>Value Pattern</span>
            <input
              value={def.pattern ?? ""}
              onChange={(e) =>
                onUpdate({ pattern: e.target.value || undefined })
              }
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
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              paddingBottom: 6,
              whiteSpace: "nowrap",
            }}
            title="On by default: surrounding spaces are stripped before the value goes on the wire. Turn off for raw payloads where edge whitespace is part of the protocol."
          >
            <input
              type="checkbox"
              checked={def.trim !== false}
              onChange={(e) =>
                onUpdate({ trim: e.target.checked ? undefined : false })
              }
              data-testid={`param-trim-${name}`}
            />
            Trim spaces
          </label>
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              paddingBottom: 6,
              whiteSpace: "nowrap",
            }}
            title="Mask the input when the command is run, for a password, PIN or key. Presentation only: the value still goes to the device as typed."
          >
            <input
              type="checkbox"
              checked={def.secret === true}
              onChange={(e) =>
                onUpdate({ secret: e.target.checked ? true : undefined })
              }
              data-testid={`param-secret-${name}`}
            />
            Masked
          </label>
        </div>
      )}

      {isEnum && (
        <div style={{ marginTop: "var(--space-sm)" }}>
          <span style={labelStyle}>Allowed Values</span>
          <EnumValuesEditor
            values={def.values}
            onChange={(values) => onUpdate({ values })}
          />
        </div>
      )}

      {isChildId && (
        <div style={{ marginTop: "var(--space-sm)" }}>
          <span style={labelStyle}>Child Type</span>
          {childTypes.length === 0 ? (
            <div
              style={{
                fontSize: "11px",
                color: "var(--color-warning, #d97706)",
                padding: "var(--space-xs) var(--space-sm)",
                borderRadius: "var(--border-radius)",
                background: "rgba(255, 152, 0, 0.12)",
                border: "1px solid rgba(255, 152, 0, 0.35)",
              }}
            >
              No child entity types declared yet. Add one in the Child Entity
              Types section above, then pick it here.
            </div>
          ) : (
            <select
              value={def.child_type ?? ""}
              onChange={(e) =>
                onUpdate({ child_type: e.target.value || undefined })
              }
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
          <div
            style={{
              fontSize: "11px",
              color: "var(--text-muted)",
              marginTop: 2,
            }}
          >
            The runtime command picker shows a dropdown of registered{" "}
            {def.child_type ? <code>{def.child_type}</code> : "children"} for
            this parameter. The value sent is the integer local ID.
          </div>
        </div>
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

/** The control types a cascade can be narrowed to, named as the child
 *  state-variable editor names them. */
const CASCADE_TYPES: { value: string; label: string }[] = [
  { value: "boolean", label: "Boolean" },
  { value: "number", label: "Number" },
  { value: "integer", label: "Integer" },
  { value: "float", label: "Float" },
  { value: "enum", label: "Enum" },
  { value: "string", label: "String" },
];

type CascadeDef = NonNullable<DriverParamDef["options_from"]>;

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

  const write = (patch: Partial<CascadeDef>) => {
    const next: CascadeDef = { ...optionsFrom, ...patch };
    if (!next.types?.length) delete next.types;
    if (!next.units?.length) delete next.units;
    onChange(next);
  };

  return (
    <div style={{ marginTop: "var(--space-xs)" }}>
      <span style={{ display: "block", fontSize: "11px", color: "var(--text-muted)", marginBottom: 2 }}>
        Only Offer Controls Of Type
      </span>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-sm)" }}>
        {CASCADE_TYPES.map((t) => (
          <label key={t.value} style={{ fontSize: "var(--font-size-sm)", display: "flex", gap: 4, alignItems: "center" }}>
            <input
              type="checkbox"
              checked={(types as readonly string[]).includes(t.value)}
              onChange={(e) =>
                write({
                  types: (e.target.checked
                    ? [...types, t.value]
                    : types.filter((x) => x !== t.value)) as CascadeDef["types"],
                })
              }
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
          write({
            units: e.target.value.split(",").map((u) => u.trim()).filter(Boolean),
          });
        }}
        placeholder="Any unit, or e.g. dB"
        data-testid={`param-options-from-units-${name}`}
        style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
      />
      <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: 2 }}>
        For a command that works on only some controls: a toggle takes Boolean
        controls, a level step takes Numbers in dB. Leave both empty to offer
        every control. Separate several units with commas.
      </div>
    </div>
  );
}

/** Where a parameter's choices come from.
 *
 *  One control for the three fields that answer that question, because to an
 *  author they are one decision, not three:
 *
 *  - ``options_state`` — the device publishes the list as a state variable.
 *  - ``options_from``  — the list is the controls of a child picked in a
 *                        sibling ``child_id`` param (a cascade).
 *  - ``type_from``     — a companion to the cascade: this param's *input type*
 *                        follows whichever control the cascade picked, so a
 *                        Value box becomes a dB spinner for a level and Yes/No
 *                        for a mute.
 *
 *  ``options_from.source`` has exactly one legal value, so the editor writes it
 *  rather than showing a one-item dropdown. Switching source clears the other
 *  field, and nothing is written just by rendering — a param that arrived with
 *  an odd combination keeps it until the author changes something. */
function ParamOptionsEditor({
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
  onUpdate: (partial: Partial<DriverParamDef>) => void;
}) {
  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "11px",
    color: "var(--text-muted)",
    marginBottom: 2,
  };
  const hintStyle: React.CSSProperties = {
    fontSize: "11px",
    color: "var(--text-muted)",
    marginTop: 2,
  };

  // A cascade reads the controls of a child, so it can only point at a
  // sibling child_id param — the same rule the platform enforces.
  const childIdSiblings = Object.entries(siblings)
    .filter(([sName, sDef]) => sName !== name && sDef?.type === "child_id")
    .map(([sName]) => sName);
  // `type_from` points at a sibling that is itself a child_schema cascade.
  const cascadeSiblings = Object.entries(siblings)
    .filter(
      ([sName, sDef]) =>
        sName !== name && sDef?.options_from?.source === "child_schema",
    )
    .map(([sName]) => sName);

  const source: "fixed" | "state" | "cascade" = def.options_from
    ? "cascade"
    : def.options_state !== undefined
      ? "state"
      : "fixed";

  const changeSource = (next: string) => {
    if (next === "fixed") {
      onUpdate({ options_state: undefined, options_from: undefined });
    } else if (next === "state") {
      onUpdate({
        options_from: undefined,
        options_state: def.options_state ?? stateKeys[0] ?? "",
      });
    } else {
      onUpdate({
        options_state: undefined,
        options_from: {
          param: def.options_from?.param ?? childIdSiblings[0] ?? "",
          source: "child_schema",
        },
      });
    }
  };

  return (
    <div style={{ marginTop: "var(--space-sm)" }}>
      <span style={labelStyle}>Choices Come From</span>
      <select
        value={source}
        onChange={(e) => changeSource(e.target.value)}
        data-testid={`param-options-source-${name}`}
        style={{ width: "100%", fontSize: "var(--font-size-sm)" }}
      >
        <option value="fixed">Whatever the type allows</option>
        <option value="state">A list the device publishes</option>
        <option value="cascade">
          The controls of a child picked in another parameter
        </option>
      </select>

      {source === "state" && (
        <div style={{ marginTop: "var(--space-xs)" }}>
          <input
            value={def.options_state ?? ""}
            onChange={(e) => onUpdate({ options_state: e.target.value })}
            list={`param-options-state-list-${name}`}
            placeholder="State variable holding the list, e.g. input_list"
            data-testid={`param-options-state-${name}`}
            style={{
              width: "100%",
              fontSize: "var(--font-size-sm)",
              fontFamily: "var(--font-mono)",
            }}
          />
          <datalist id={`param-options-state-list-${name}`}>
            {stateKeys.map((key) => (
              <option key={key} value={key} />
            ))}
          </datalist>
          <div style={hintStyle}>
            The driver publishes the choices as this state variable (a JSON list
            of values, or of value/label pairs). The picker offers them and
            still accepts anything typed.
          </div>
        </div>
      )}

      {source === "cascade" && (
        <div style={{ marginTop: "var(--space-xs)" }}>
          {childIdSiblings.length === 0 ? (
            <div
              style={{
                fontSize: "11px",
                color: "var(--color-warning, #d97706)",
                padding: "var(--space-xs) var(--space-sm)",
                borderRadius: "var(--border-radius)",
                background: "rgba(255, 152, 0, 0.12)",
                border: "1px solid rgba(255, 152, 0, 0.35)",
              }}
            >
              This command has no Child ID parameter to cascade from. Add one
              (Type: Child ID), then pick it here.
            </div>
          ) : (
            <select
              value={def.options_from?.param ?? ""}
              onChange={(e) =>
                onUpdate({
                  options_from: {
                    ...def.options_from,
                    param: e.target.value,
                    source: "child_schema",
                  },
                })
              }
              data-testid={`param-options-from-${name}`}
              style={{
                width: "100%",
                fontSize: "var(--font-size-sm)",
                fontFamily: "var(--font-mono)",
              }}
            >
              <option value="">(select a Child ID parameter)</option>
              {childIdSiblings.map((sName) => (
                <option key={sName} value={sName}>
                  {sName}
                </option>
              ))}
            </select>
          )}
          <div style={hintStyle}>
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
      )}

      {/* The type companion. Hidden when there is no cascade to follow, so it
          doesn't sit there unusable — unless one is already set, which must
          stay visible to be cleared. */}
      {(cascadeSiblings.length > 0 || def.type_from) && (
        <div style={{ marginTop: "var(--space-sm)" }}>
          <span style={labelStyle}>Input Type Follows</span>
          <select
            value={def.type_from?.param ?? ""}
            onChange={(e) =>
              onUpdate({
                type_from: e.target.value
                  ? { param: e.target.value }
                  : undefined,
              })
            }
            data-testid={`param-type-from-${name}`}
            style={{
              width: "100%",
              fontSize: "var(--font-size-sm)",
              fontFamily: "var(--font-mono)",
            }}
          >
            <option value="">Nothing (use the type above)</option>
            {cascadeSiblings.map((sName) => (
              <option key={sName} value={sName}>
                {sName}
              </option>
            ))}
            {def.type_from?.param &&
              !cascadeSiblings.includes(def.type_from.param) && (
                <option value={def.type_from.param}>
                  {def.type_from.param} (not a cascade)
                </option>
              )}
          </select>
          <div style={hintStyle}>
            This box takes its type from whichever control that parameter picked:
            a number spinner for a level, Yes/No for a mute.
          </div>
        </div>
      )}
    </div>
  );
}
