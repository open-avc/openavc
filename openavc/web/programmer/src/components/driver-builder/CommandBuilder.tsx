import { useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import type { DriverCommandDef, DriverDefinition } from "../../api/types";
import { IdRenameInput, type RenameResult } from "./IdRenameInput";
import { commandsRenamed, newCommand, renamePlaceholder, type CommandUpdate } from "./commandEdits";
import { OfflineFlag, RestartWindowField, SleepsDeviceField } from "./CommandAwayFields";
import { CommandConfirmField } from "./CommandConfirmField";
import { ReadBackField, readBackForm } from "./ReadBackField";
import { CommandSendFields, UdpToggle } from "./CommandSendFields";
import { FIELD_HINT, FIELD_LABEL } from "./commandFieldParts";
import { CommandSemanticsEditor } from "./CommandSemanticsEditor";
import { ParamEditor } from "./ParamEditor";

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
