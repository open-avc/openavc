import { useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import type { DriverCommandDef, DriverDefinition } from "../../api/types";

interface CommandBuilderProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

export function CommandBuilder({ draft, onUpdate }: CommandBuilderProps) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const commands = draft.commands;
  const commandNames = Object.keys(commands);

  const addCommand = () => {
    // Generate unique name that doesn't collide with existing commands
    let counter = commandNames.length + 1;
    let name = `command_${counter}`;
    while (name in commands) {
      counter++;
      name = `command_${counter}`;
    }
    onUpdate({
      commands: {
        ...commands,
        [name]: { label: "New Command", string: "", params: {} },
      },
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
    onUpdate({
      commands: {
        ...commands,
        [name]: { ...commands[name], ...partial },
      },
    });
  };

  const renameCommand = (oldName: string, newName: string) => {
    if (!newName || newName === oldName || newName in commands) return;
    const next: Record<string, DriverCommandDef> = {};
    for (const [k, v] of Object.entries(commands)) {
      next[k === oldName ? newName : k] = v;
    }
    onUpdate({ commands: next });
    if (expanded === oldName) setExpanded(newName);
  };

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
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
        const cmd = commands[name];
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
            <button
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
                {cmd.label}
              </span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  removeCommand(name);
                }}
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
                }}
              >
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: "var(--space-md)",
                    marginBottom: "var(--space-md)",
                  }}
                >
                  <div>
                    <label style={labelStyle}>Command ID</label>
                    <input
                      value={name}
                      onChange={(e) => renameCommand(name, e.target.value)}
                      style={{ width: "100%" }}
                    />
                  </div>
                  <div>
                    <label style={labelStyle}>Display Label</label>
                    <input
                      value={cmd.label}
                      onChange={(e) =>
                        updateCommand(name, { label: e.target.value })
                      }
                      style={{ width: "100%" }}
                    />
                  </div>
                </div>

                <div style={{ marginBottom: "var(--space-md)" }}>
                  <label style={labelStyle}>Help Text</label>
                  <input
                    value={(cmd as any).help ?? ""}
                    onChange={(e) =>
                      updateCommand(name, { help: e.target.value } as any)
                    }
                    placeholder="Brief description of what this command does"
                    style={{ width: "100%" }}
                  />
                  <div
                    style={{
                      fontSize: "11px",
                      color: "var(--text-muted)",
                      marginTop: "var(--space-xs)",
                    }}
                  >
                    Shown to users when selecting this command.
                  </div>
                </div>

                <div style={{ marginBottom: "var(--space-md)" }}>
                  <label style={labelStyle}>Command String</label>
                  <input
                    value={cmd.string}
                    onChange={(e) =>
                      updateCommand(name, { string: e.target.value })
                    }
                    placeholder="e.g., %1POWR {value}\r"
                    style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                  />
                  <div
                    style={{
                      fontSize: "11px",
                      color: "var(--text-muted)",
                      marginTop: "var(--space-xs)",
                    }}
                  >
                    Use {"{param_name}"} for parameter placeholders. Use \r, \n
                    for control characters.
                  </div>
                </div>

                <ParamEditor
                  params={cmd.params}
                  onChange={(params) => updateCommand(name, { params })}
                />
              </div>
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

function ParamEditor({
  params,
  onChange,
}: {
  params: Record<
    string,
    { type: string; required?: boolean; values?: string[] }
  >;
  onChange: (
    params: Record<
      string,
      { type: string; required?: boolean; values?: string[] }
    >
  ) => void;
}) {
  const paramNames = Object.keys(params);

  const addParam = () => {
    const name = `param${paramNames.length + 1}`;
    onChange({ ...params, [name]: { type: "string" } });
  };

  const removeParam = (name: string) => {
    const next = { ...params };
    delete next[name];
    onChange(next);
  };

  const renameParam = (oldName: string, newName: string) => {
    const cleaned = newName.replace(/[^a-zA-Z0-9_]/g, "");
    if (!cleaned || cleaned === oldName) return;
    if (cleaned in params) return; // prevent collision
    const entries = Object.entries(params);
    const next: typeof params = {};
    for (const [k, v] of entries) {
      next[k === oldName ? cleaned : k] = v;
    }
    onChange(next);
  };

  return (
    <div>
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
        <div
          key={name}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-sm)",
            marginBottom: "var(--space-xs)",
          }}
        >
          <input
            value={name}
            onChange={(e) => renameParam(name, e.target.value)}
            onBlur={(e) => renameParam(name, e.target.value)}
            style={{
              width: 120,
              fontSize: "var(--font-size-sm)",
              fontFamily: "var(--font-mono)",
            }}
          />
          <select
            value={params[name].type}
            onChange={(e) =>
              onChange({
                ...params,
                [name]: { ...params[name], type: e.target.value },
              })
            }
            style={{ width: 100, fontSize: "var(--font-size-sm)" }}
          >
            <option value="string">String</option>
            <option value="integer">Integer</option>
            <option value="enum">Enum</option>
          </select>
          <button
            onClick={() => removeParam(name)}
            style={{ padding: "2px", color: "var(--text-muted)" }}
          >
            <Trash2 size={14} />
          </button>
        </div>
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
