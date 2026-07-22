import { useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import type {
  DriverActionDef,
  DriverDefinition,
  DriverVisibleWhen,
  DriverVisibleWhenCondition,
} from "../../api/types";
import {
  ACTION_AVAILABILITIES,
  ACTION_KINDS_YAML,
  VISIBLE_WHEN_OPERATORS,
} from "../../api/types";
import { ParamEditor } from "./CommandBuilder";
import {
  convertQuickActionsToActions,
  extraKeys,
  visibleWhenConditions,
  visibleWhenMode,
  coerceConditionValue,
  type VisibleWhenMode,
} from "./actionsEditorHelpers";

interface ActionsEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  marginBottom: "var(--space-xs)",
};

const helpStyle: React.CSSProperties = {
  fontSize: "11px",
  color: "var(--text-muted)",
  marginTop: "var(--space-xs)",
};

/**
 * Edits the `actions` list (commands promoted to buttons at the top of the
 * device view, plus link actions that open the device's web interface) and
 * the `web_ui` flag (auto-adds an Open Web UI button). A legacy
 * `quick_actions` list renders read-only with a one-click conversion into
 * explicit actions.
 */
export function ActionsEditor({ draft, onUpdate }: ActionsEditorProps) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const actions = draft.actions ?? [];
  const commands = draft.commands ?? {};
  const commandIds = Object.keys(commands);
  const quickActions = draft.quick_actions ?? [];

  const writeActions = (next: DriverActionDef[]) => {
    // An empty list drops the key entirely so minimal YAML stays minimal.
    onUpdate({ actions: next.length > 0 ? next : undefined });
  };

  const updateAction = (
    index: number,
    partial: Partial<DriverActionDef>,
  ) => {
    // Merge, then strip undefined keys — same delete-undefined pattern as
    // CommandBuilder, so cleared optional fields vanish from the YAML
    // instead of serializing as null. Keys not named in the partial are
    // spread through untouched, which is what preserves hand-authored
    // extras (params overrides included) verbatim.
    const merged = { ...actions[index], ...partial } as Record<string, unknown>;
    for (const k of Object.keys(merged)) {
      if (merged[k] === undefined) delete merged[k];
    }
    const next = actions.slice();
    next[index] = merged as unknown as DriverActionDef;
    writeActions(next);
  };

  const addAction = () => {
    // Seed from the first command not yet promoted — valid the moment it
    // exists, and matches the promote-a-command mental model. Fall back to
    // a unique placeholder id when every command is already promoted.
    const used = new Set(actions.map((a) => a.id));
    let id = commandIds.find((c) => !used.has(c));
    if (!id) {
      let counter = actions.length + 1;
      id = `action_${counter}`;
      while (used.has(id)) {
        counter++;
        id = `action_${counter}`;
      }
    }
    writeActions([...actions, { id, kind: "command" }]);
    setExpanded(actions.length);
  };

  const removeAction = (index: number) => {
    writeActions(actions.filter((_, i) => i !== index));
    if (expanded === index) setExpanded(null);
    else if (expanded !== null && expanded > index) setExpanded(expanded - 1);
  };

  const convertQuick = () => {
    onUpdate({
      actions: convertQuickActionsToActions(draft.actions, draft.quick_actions),
      quick_actions: undefined,
    });
  };

  const webUi = draft.web_ui;
  const webUiEnabled = webUi !== undefined && webUi !== false;

  return (
    <div>
      <p
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          marginTop: 0,
          marginBottom: "var(--space-md)",
        }}
      >
        Actions promote a command to a one-click button at the top of the
        device view — power on, reboot, recall a preset — instead of leaving it
        buried in the Send Command list. A link action opens a URL (usually the
        device&apos;s own web interface) in a new tab.
      </p>

      {/* web_ui — the zero-effort way to get an Open Web UI button. */}
      <div style={{ marginBottom: "var(--space-lg)" }}>
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-sm)",
            fontSize: "var(--font-size-sm)",
          }}
        >
          <input
            type="checkbox"
            checked={webUiEnabled}
            onChange={(e) =>
              onUpdate({ web_ui: e.target.checked ? true : undefined })
            }
          />
          Device has a web interface (adds an Open Web UI button)
        </label>
        {webUiEnabled && (
          <div style={{ marginTop: "var(--space-sm)", marginLeft: 24 }}>
            <label style={labelStyle}>URL Template (optional)</label>
            <input
              value={typeof webUi === "string" ? webUi : ""}
              onChange={(e) =>
                onUpdate({ web_ui: e.target.value || true })
              }
              placeholder="https://{host}"
              style={{ width: "100%", fontFamily: "var(--font-mono)" }}
            />
            <div style={helpStyle}>
              Leave blank to open <code>{"https://{host}"}</code>.{" "}
              <code>{"{host}"}</code>, <code>{"{port}"}</code>, and any{" "}
              <code>{"{config_field}"}</code> are substituted from the
              device&apos;s connection settings. Declaring an explicit link
              action below replaces the automatic button. Requires OpenAVC
              0.24.0 or newer.
            </div>
          </div>
        )}
      </div>

      {/* Legacy quick_actions — read-only, with one-click conversion. */}
      {quickActions.length > 0 && (
        <div
          style={{
            marginBottom: "var(--space-lg)",
            padding: "var(--space-md)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-surface)",
          }}
        >
          <div style={{ ...labelStyle, marginBottom: "var(--space-sm)" }}>
            Quick actions (legacy) — shown as buttons; new drivers should
            declare actions
          </div>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "var(--space-xs)",
              marginBottom: "var(--space-sm)",
            }}
          >
            {quickActions.map((id, i) => (
              <span
                key={`${id}-${i}`}
                style={{
                  padding: "2px 8px",
                  borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)",
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                {id}
              </span>
            ))}
          </div>
          <button
            onClick={convertQuick}
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--accent)",
              padding: "var(--space-xs) 0",
            }}
          >
            Convert to actions
          </button>
          <div style={helpStyle}>
            Rewrites each id as an explicit action (ids already declared as
            actions are skipped) and removes the legacy list. Behavior is
            unchanged — explicit actions just unlock labels, icons,
            confirmation, and visibility rules.
          </div>
        </div>
      )}

      {actions.length === 0 && quickActions.length === 0 && (
        <p
          style={{
            color: "var(--text-muted)",
            fontSize: "var(--font-size-sm)",
            marginBottom: "var(--space-md)",
          }}
        >
          No actions defined yet. Promote your most-used commands to buttons.
        </p>
      )}

      {actions.map((action, index) => (
        <ActionCard
          key={index}
          action={action}
          draft={draft}
          isOpen={expanded === index}
          onToggle={() => setExpanded(expanded === index ? null : index)}
          onUpdate={(partial) => updateAction(index, partial)}
          onRemove={() => removeAction(index)}
        />
      ))}

      <button
        onClick={addAction}
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
        <Plus size={14} /> Add Action
      </button>
    </div>
  );
}

const KIND_LABELS: Record<string, string> = {
  command: "Command (runs a declared command)",
  link: "Link (opens a URL in a new tab)",
};

const AVAILABILITY_LABELS: Record<string, string> = {
  online: "Online (hidden while the device is offline)",
  offline: "Offline (shown only while offline)",
  always: "Always (ignores connection state)",
};

function ActionCard({
  action,
  draft,
  isOpen,
  onToggle,
  onUpdate,
  onRemove,
}: {
  action: DriverActionDef;
  draft: DriverDefinition;
  isOpen: boolean;
  onToggle: () => void;
  onUpdate: (partial: Partial<DriverActionDef>) => void;
  onRemove: () => void;
}) {
  const commands = draft.commands ?? {};
  const commandIds = Object.keys(commands);
  const kind = action.kind ?? "command";
  const isLink = kind === "link";

  // The label the button falls back to at runtime: the promoted command's
  // label, else the action id — surfaced as the input placeholder.
  const targetCommand = isLink ? undefined : action.command || action.id;
  const labelFallback =
    (targetCommand && commands[targetCommand]?.label) || action.id;

  // Links default to always-visible at runtime; commands to online-only.
  const availabilityDefault = isLink ? "always" : "online";

  const setKind = (next: string) => {
    // Scrub the field the new kind can't carry (url is link-only; command
    // is meaningless on a link) so switching never leaves an invalid key.
    if (next === "link") {
      onUpdate({ kind: "link", command: undefined });
    } else {
      onUpdate({ kind: "command", url: undefined });
    }
  };

  const confirm = action.confirm;
  const confirmEnabled = confirm === true || typeof confirm === "string";

  const paramsOverridden = action.params !== undefined;
  const inheritedParamCount = targetCommand
    ? Object.keys(commands[targetCommand]?.params ?? {}).length
    : 0;

  return (
    <div
      style={{
        border: "1px solid var(--border-color)",
        borderRadius: "var(--border-radius)",
        marginBottom: "var(--space-sm)",
        background: "var(--bg-surface)",
      }}
    >
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
          {action.id || "(no id)"}
        </span>
        <span style={{ color: "var(--text-muted)", fontSize: "11px" }}>
          {isLink ? "link" : action.label || labelFallback}
        </span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          style={{ padding: "2px", color: "var(--text-muted)" }}
          title="Remove action"
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
              <label style={labelStyle}>Action ID</label>
              <input
                value={action.id}
                onChange={(e) =>
                  onUpdate({
                    id: e.target.value.replace(/[^a-z0-9_]/gi, "").toLowerCase(),
                  })
                }
                placeholder="e.g. power_on"
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
              />
              <div style={helpStyle}>
                Unique within the driver. For a command action it doubles as
                the command to run unless one is picked below.
              </div>
            </div>
            <div>
              <label style={labelStyle}>Kind</label>
              <select
                value={kind}
                onChange={(e) => setKind(e.target.value)}
                style={{ width: "100%" }}
              >
                {ACTION_KINDS_YAML.map((k) => (
                  <option key={k} value={k}>
                    {KIND_LABELS[k] ?? k}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "var(--space-md)",
              marginBottom: "var(--space-md)",
            }}
          >
            <div>
              <label style={labelStyle}>Button Label (optional)</label>
              <input
                value={action.label ?? ""}
                onChange={(e) =>
                  onUpdate({ label: e.target.value || undefined })
                }
                placeholder={labelFallback}
                style={{ width: "100%" }}
              />
            </div>
            <div>
              <label style={labelStyle}>Icon (optional)</label>
              <input
                value={action.icon ?? ""}
                onChange={(e) =>
                  onUpdate({ icon: e.target.value || undefined })
                }
                placeholder="e.g. power"
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
              />
              <div style={helpStyle}>
                Lucide icon name in kebab-case — power, rotate-cw,
                external-link.
              </div>
            </div>
          </div>

          {!isLink && (
            <div style={{ marginBottom: "var(--space-md)" }}>
              <label style={labelStyle}>Command</label>
              <select
                value={action.command ?? ""}
                onChange={(e) =>
                  onUpdate({ command: e.target.value || undefined })
                }
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
              >
                <option value="">(same as id)</option>
                {commandIds.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              <div style={helpStyle}>
                The declared command this button sends. Leave on &quot;same as
                id&quot; when the action id matches a command id.
              </div>
            </div>
          )}

          {isLink && (
            <div style={{ marginBottom: "var(--space-md)" }}>
              <label style={labelStyle}>URL</label>
              <input
                value={action.url ?? ""}
                onChange={(e) => onUpdate({ url: e.target.value || undefined })}
                placeholder="https://{host}"
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
              />
              <div style={helpStyle}>
                Opens in a new tab, client-side — nothing is sent to the
                device. <code>{"{host}"}</code>, <code>{"{port}"}</code>, and
                any <code>{"{config_field}"}</code> are substituted from the
                device&apos;s connection settings. Blank opens{" "}
                <code>{"https://{host}"}</code>.
              </div>
            </div>
          )}

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "var(--space-md)",
              marginBottom: "var(--space-md)",
              alignItems: "start",
            }}
          >
            <div>
              <label style={labelStyle}>Show Button</label>
              <select
                value={action.availability ?? availabilityDefault}
                onChange={(e) =>
                  onUpdate({
                    availability:
                      e.target.value === availabilityDefault
                        ? undefined
                        : (e.target
                            .value as DriverActionDef["availability"]),
                  })
                }
                style={{ width: "100%" }}
              >
                {ACTION_AVAILABILITIES.map((a) => (
                  <option key={a} value={a}>
                    {AVAILABILITY_LABELS[a] ?? a}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  fontSize: "var(--font-size-sm)",
                  color: "var(--text-secondary)",
                  marginTop: 22,
                }}
              >
                <input
                  type="checkbox"
                  checked={confirmEnabled}
                  onChange={(e) =>
                    onUpdate({ confirm: e.target.checked ? true : undefined })
                  }
                />
                Ask before running
              </label>
            </div>
          </div>

          {confirmEnabled && (
            <div style={{ marginBottom: "var(--space-md)" }}>
              <label style={labelStyle}>Confirmation Message (optional)</label>
              <input
                value={typeof confirm === "string" ? confirm : ""}
                onChange={(e) =>
                  onUpdate({ confirm: e.target.value || true })
                }
                placeholder="Leave blank for the generic prompt"
                style={{ width: "100%" }}
              />
            </div>
          )}

          {!isLink && (
            <div style={{ marginBottom: "var(--space-md)" }}>
              <div style={labelStyle}>Input Fields</div>
              {!paramsOverridden ? (
                <>
                  <div style={{ ...helpStyle, marginTop: 0 }}>
                    {targetCommand && commands[targetCommand]
                      ? inheritedParamCount > 0
                        ? `Uses ${targetCommand}'s ${inheritedParamCount} input field${inheritedParamCount === 1 ? "" : "s"}.`
                        : `${targetCommand} takes no inputs, so the button runs immediately.`
                      : "Uses the promoted command's input fields."}
                  </div>
                  <button
                    onClick={() =>
                      onUpdate({
                        params: JSON.parse(
                          JSON.stringify(
                            (targetCommand &&
                              commands[targetCommand]?.params) ??
                              {},
                          ),
                        ),
                      })
                    }
                    style={{
                      fontSize: "11px",
                      color: "var(--accent)",
                      padding: "2px 0",
                    }}
                  >
                    + Override input fields
                  </button>
                </>
              ) : (
                <>
                  <ParamEditor
                    params={action.params ?? {}}
                    childTypes={Object.keys(draft.child_entity_types ?? {})}
                    onChange={(params) => onUpdate({ params })}
                  />
                  <button
                    onClick={() => onUpdate({ params: undefined })}
                    style={{
                      fontSize: "11px",
                      color: "var(--accent)",
                      padding: "2px 0",
                    }}
                  >
                    Remove override (use the command&apos;s inputs)
                  </button>
                </>
              )}
            </div>
          )}

          <VisibleWhenEditor
            value={action.visible_when}
            onChange={(visible_when) => onUpdate({ visible_when })}
          />
        </div>
      )}
    </div>
  );
}

// Operators sorted for a stable dropdown; "eq" is the default the runtime
// assumes when the key is omitted.
const OPERATOR_OPTIONS = [...VISIBLE_WHEN_OPERATORS].sort();
const NO_VALUE_OPERATORS = new Set(["truthy", "falsy"]);
const GROUP_KEYS = ["any", "all"] as const;

/**
 * Edits an action's visible_when block: always show, a single
 * {key, operator, value} condition, or an any/all group of them. Extra keys
 * the runtime tolerates are carried through restructures untouched.
 */
function VisibleWhenEditor({
  value,
  onChange,
}: {
  value: DriverVisibleWhen | undefined;
  onChange: (next: DriverVisibleWhen | undefined) => void;
}) {
  const mode = visibleWhenMode(value);
  const conditions = visibleWhenConditions(value);
  const record = (value ?? {}) as Record<string, unknown>;

  const setMode = (next: VisibleWhenMode) => {
    if (next === mode) return;
    if (next === "always") {
      onChange(undefined);
      return;
    }
    const first: DriverVisibleWhenCondition =
      conditions[0] ?? ({ key: "" } as DriverVisibleWhenCondition);
    if (next === "single") {
      // Collapse to the first condition; its own extras ride along.
      onChange(first);
      return;
    }
    // any/all: wrap the current condition(s), keeping container extras when
    // converting between the two group flavors.
    const extras =
      mode === "any" || mode === "all"
        ? extraKeys(record, GROUP_KEYS)
        : {};
    const list = conditions.length > 0 ? conditions : [first];
    onChange({ ...extras, [next]: list } as DriverVisibleWhen);
  };

  const writeConditions = (next: DriverVisibleWhenCondition[]) => {
    if (mode === "single") {
      onChange(next[0]);
    } else {
      const extras = extraKeys(record, GROUP_KEYS);
      onChange({ ...extras, [mode]: next } as DriverVisibleWhen);
    }
  };

  const updateCondition = (
    index: number,
    partial: Partial<DriverVisibleWhenCondition>,
  ) => {
    const merged = {
      ...conditions[index],
      ...partial,
    } as Record<string, unknown>;
    for (const k of Object.keys(merged)) {
      if (merged[k] === undefined) delete merged[k];
    }
    const next = conditions.slice();
    next[index] = merged as unknown as DriverVisibleWhenCondition;
    writeConditions(next);
  };

  return (
    <div>
      <label style={labelStyle}>Visibility</label>
      <select
        value={mode}
        onChange={(e) => setMode(e.target.value as VisibleWhenMode)}
        style={{ width: "100%" }}
      >
        <option value="always">Always show</option>
        <option value="single">Show when a condition matches</option>
        <option value="any">Show when ANY of several conditions match</option>
        <option value="all">Show when ALL of several conditions match</option>
      </select>

      {mode !== "always" && (
        <div style={{ marginTop: "var(--space-sm)" }}>
          {conditions.map((cond, i) => {
            const operator = cond.operator ?? "eq";
            const hideValue = NO_VALUE_OPERATORS.has(operator);
            return (
              <div
                key={i}
                style={{
                  display: "grid",
                  gridTemplateColumns: hideValue
                    ? "2fr 1fr auto"
                    : "2fr 1fr 1fr auto",
                  gap: "var(--space-sm)",
                  marginBottom: "var(--space-xs)",
                  alignItems: "center",
                }}
              >
                <input
                  value={cond.key ?? ""}
                  onChange={(e) => updateCondition(i, { key: e.target.value })}
                  placeholder="state key, e.g. power"
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "var(--font-size-sm)",
                  }}
                />
                <select
                  value={operator}
                  onChange={(e) => {
                    const op = e.target
                      .value as DriverVisibleWhenCondition["operator"];
                    updateCondition(i, {
                      operator: op === "eq" ? undefined : op,
                      // truthy/falsy take no value — drop it on switch.
                      ...(NO_VALUE_OPERATORS.has(op ?? "eq")
                        ? { value: undefined }
                        : {}),
                    });
                  }}
                  style={{ fontSize: "var(--font-size-sm)" }}
                >
                  {OPERATOR_OPTIONS.map((op) => (
                    <option key={op} value={op}>
                      {op}
                    </option>
                  ))}
                </select>
                {!hideValue && (
                  <input
                    value={cond.value === undefined ? "" : String(cond.value)}
                    onChange={(e) =>
                      updateCondition(i, {
                        value:
                          e.target.value === ""
                            ? undefined
                            : coerceConditionValue(e.target.value),
                      })
                    }
                    placeholder="value"
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: "var(--font-size-sm)",
                    }}
                  />
                )}
                {mode !== "single" && (
                  <button
                    onClick={() =>
                      writeConditions(conditions.filter((_, j) => j !== i))
                    }
                    style={{ padding: "2px", color: "var(--text-muted)" }}
                    title="Remove condition"
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
            );
          })}
          {mode !== "single" && (
            <button
              onClick={() =>
                writeConditions([
                  ...conditions,
                  { key: "" } as DriverVisibleWhenCondition,
                ])
              }
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--accent)",
                padding: "var(--space-xs) 0",
              }}
            >
              + Add Condition
            </button>
          )}
          <div style={helpStyle}>
            The key is a state key of this device (e.g. <code>power</code>);{" "}
            <code>$id</code> in a key is replaced with the device id. The{" "}
            <code>truthy</code>/<code>falsy</code> operators take no value.
          </div>
        </div>
      )}
    </div>
  );
}
