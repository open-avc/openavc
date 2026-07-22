import { useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import type {
  DriverChildEntityType,
  DriverChildSetEntry,
  DriverDefinition,
  DriverResponseDef,
  DriverResponseMapping,
} from "../../api/types";
import { IdRenameInput, type RenameResult } from "./IdRenameInput";
import {
  addValueMapEntry,
  buildJsonResponse,
  buildResponse,
  checkValueMapKeyRename,
  childIdFromParts,
  childIdMap,
  childIdToText,
  declaredStateType,
  getJsonRows,
  getMappings,
  getPattern,
  oscChildIdFromParts,
  oscChildIdToText,
  oscChildPropFromText,
  oscChildPropToText,
  parseRequireText,
  renameValueMapKey,
  requireToList,
  type JsonRuleRow,
} from "./responseBuilderHelpers";

/** Coercion types offered on a JSON field row (what coerce_json_value
 *  distinguishes). A loaded rule may carry another spelling ("number",
 *  "enum") — the select shows it as an extra option rather than lying. */
const JSON_ROW_TYPES = ["string", "integer", "float", "boolean"];

function _ordinal(n: number): string {
  if (n === 1) return "1st";
  if (n === 2) return "2nd";
  if (n === 3) return "3rd";
  return `${n}th`;
}

interface ResponseBuilderProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

export function ResponseBuilder({ draft, onUpdate }: ResponseBuilderProps) {
  const responses = draft.responses ?? [];
  const stateVars = draft.state_variables;

  const addResponse = () => {
    if (draft.transport === "osc") {
      onUpdate({
        responses: [
          ...responses,
          { address: "", mappings: [{ group: 0, arg: 0, state: "", type: "float" }] },
        ],
      });
    } else {
      onUpdate({
        responses: [
          ...responses,
          buildResponse("", [{ group: 1, state: "", type: "string" }], {}, stateVars),
        ],
      });
    }
  };

  const removeResponse = (index: number) => {
    onUpdate({ responses: responses.filter((_, i) => i !== index) });
  };

  const updateResponse = (index: number, updated: DriverResponseDef) => {
    const next = [...responses];
    next[index] = updated;
    onUpdate({ responses: next });
  };

  /** Rebuild a json rule from edited rows, keeping its require scope. */
  const updateJsonRows = (index: number, rows: JsonRuleRow[]) => {
    const resp = responses[index];
    updateResponse(
      index,
      buildJsonResponse(resp, rows, requireToList(resp.require), stateVars),
    );
  };

  /** Convert a rule between text (regex) and JSON body, confirming before
   *  authored content is dropped. Throttle survives the switch; child_set
   *  does not survive to JSON (the runtime rejects it there). */
  const switchKind = (index: number, kind: string) => {
    const resp = responses[index];
    const wasJson = resp.address === undefined && !!resp.json;
    if (kind === "json" && !wasJson) {
      const dropped: string[] = [];
      if ((resp.match ?? "").trim()) {
        dropped.push("its match pattern");
      }
      if (
        (resp.mappings?.length ?? 0) > 0 ||
        Object.keys(resp.set ?? {}).length > 0
      ) {
        dropped.push("its capture mappings");
      }
      if ((resp.child_set?.length ?? 0) > 0) {
        dropped.push("its child entity routing (not supported on JSON rules)");
      }
      if (
        dropped.length > 0 &&
        !window.confirm(
          `Switching this rule to JSON body drops ${dropped.join(", ")}. Continue?`,
        )
      ) {
        return;
      }
      const next: DriverResponseDef = { json: true, set: {} };
      if (resp.throttle !== undefined) next.throttle = resp.throttle;
      updateResponse(index, next);
    } else if (kind === "regex" && wasJson) {
      const dropped: string[] = [];
      if (getJsonRows(resp, stateVars).length > 0) {
        dropped.push("its JSON field rows");
      }
      if (requireToList(resp.require).length > 0) {
        dropped.push("its body-key scope");
      }
      if (
        dropped.length > 0 &&
        !window.confirm(
          `Switching this rule to text matching drops ${dropped.join(" and ")}. Continue?`,
        )
      ) {
        return;
      }
      const next = buildResponse(
        "",
        [{ group: 1, state: "", type: "string" }],
        {},
        stateVars,
      );
      if (resp.throttle !== undefined) next.throttle = resp.throttle;
      updateResponse(index, next);
    }
  };

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
  };

  // Collect state variable names for dropdown
  const stateVarNames = Object.keys(draft.state_variables);

  return (
    <div>
      <p
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          marginBottom: "var(--space-md)",
        }}
      >
        Define rules that turn device responses into state variable values. A
        text rule matches with a regex — use parentheses to capture the parts
        you want. A JSON body rule parses the whole reply as JSON and reads
        fields from it (common for HTTP devices).
      </p>
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          background: "var(--bg-hover)",
          padding: "var(--space-sm) var(--space-md)",
          borderRadius: "var(--border-radius)",
          marginBottom: "var(--space-md)",
          lineHeight: 1.6,
        }}
      >
        <strong>Quick reference:</strong>{" "}
        <code style={{ background: "var(--bg-surface)", padding: "1px 4px", borderRadius: 3 }}>(\d+)</code> captures a number,{" "}
        <code style={{ background: "var(--bg-surface)", padding: "1px 4px", borderRadius: 3 }}>(\w+)</code> captures a word,{" "}
        <code style={{ background: "var(--bg-surface)", padding: "1px 4px", borderRadius: 3 }}>(.+)</code> captures anything.
        <br />
        Example: if the device sends <code style={{ background: "var(--bg-surface)", padding: "1px 4px", borderRadius: 3 }}>Vol65</code>, the
        pattern <code style={{ background: "var(--bg-surface)", padding: "1px 4px", borderRadius: 3 }}>Vol(\d+)</code> captures <strong>65</strong> as
        Group 1, which you map to your <em>volume</em> state variable.
      </div>

      {responses.map((resp, i) => {
        // Rule kind mirrors the runtime's dispatch order (compile_driver):
        // an address is OSC first, then json: true, then regex.
        const isJson = resp.address === undefined && !!resp.json;
        const pattern = getPattern(resp);
        const mappings = isJson ? [] : getMappings(resp, stateVars);
        const jsonRows = isJson ? getJsonRows(resp, stateVars) : [];
        return (
        <div
          key={i}
          style={{
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            padding: "var(--space-md)",
            marginBottom: "var(--space-sm)",
            background: "var(--bg-surface)",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: "var(--space-sm)",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-sm)",
              }}
            >
              <span
                style={{
                  fontSize: "var(--font-size-sm)",
                  fontWeight: 600,
                }}
              >
                Response Rule {i + 1}
              </span>
              {draft.transport !== "osc" && (
                <select
                  value={isJson ? "json" : "regex"}
                  onChange={(e) => switchKind(i, e.target.value)}
                  title="How this rule reads the reply: match text with a regex pattern, or parse the whole body as JSON and read fields from it"
                  style={{ fontSize: "var(--font-size-sm)" }}
                >
                  <option value="regex">Text (regex)</option>
                  <option value="json">JSON body</option>
                </select>
              )}
            </div>
            <button
              onClick={() => removeResponse(i)}
              style={{ padding: "2px", color: "var(--text-muted)" }}
            >
              <Trash2 size={14} />
            </button>
          </div>

          {isJson ? (
            <div style={{ marginBottom: "var(--space-md)" }}>
              <div
                style={{
                  fontSize: "var(--font-size-sm)",
                  color: "var(--text-secondary)",
                  marginBottom: "var(--space-xs)",
                }}
              >
                The whole reply is parsed as a JSON object; each row reads one
                field into a state variable:
              </div>
              {jsonRows.map((row, ri) => (
                <div
                  key={ri}
                  style={{
                    display: "flex",
                    gap: "var(--space-sm)",
                    marginBottom: "var(--space-xs)",
                    alignItems: "center",
                  }}
                >
                  <input
                    value={row.path}
                    onChange={(e) =>
                      updateJsonRows(
                        i,
                        jsonRows.map((r, j) =>
                          j === ri ? { ...r, path: e.target.value } : r,
                        ),
                      )
                    }
                    placeholder="status.power"
                    title="The JSON field to read — dot-separated keys and list indices (status.power, data.0)"
                    style={{
                      width: 150,
                      fontFamily: "var(--font-mono)",
                      fontSize: "var(--font-size-sm)",
                    }}
                  />
                  <span
                    style={{
                      fontSize: "var(--font-size-sm)",
                      color: "var(--text-muted)",
                    }}
                  >
                    →
                  </span>
                  <select
                    value={row.state}
                    onChange={(e) => {
                      const state = e.target.value;
                      updateJsonRows(
                        i,
                        jsonRows.map((r, j) =>
                          j === ri
                            ? {
                                ...r,
                                state,
                                type: declaredStateType(stateVars, state),
                              }
                            : r,
                        ),
                      );
                    }}
                    style={{ flex: 1, fontSize: "var(--font-size-sm)" }}
                  >
                    <option value="">Select state variable...</option>
                    {stateVarNames.map((sv) => (
                      <option key={sv} value={sv}>
                        {sv}
                      </option>
                    ))}
                  </select>
                  <select
                    value={row.type}
                    onChange={(e) =>
                      updateJsonRows(
                        i,
                        jsonRows.map((r, j) =>
                          j === ri ? { ...r, type: e.target.value } : r,
                        ),
                      )
                    }
                    style={{ width: 90, fontSize: "var(--font-size-sm)" }}
                  >
                    {!JSON_ROW_TYPES.includes(row.type) && (
                      <option value={row.type}>{row.type}</option>
                    )}
                    <option value="string">String</option>
                    <option value="integer">Integer</option>
                    <option value="float">Float</option>
                    <option value="boolean">Boolean</option>
                  </select>
                  <button
                    onClick={() =>
                      updateJsonRows(
                        i,
                        jsonRows.filter((_, j) => j !== ri),
                      )
                    }
                    style={{ padding: "2px", color: "var(--text-muted)" }}
                  >
                    <Trash2 size={12} />
                  </button>
                  <ValueMapEditor
                    mapping={{ group: 0, state: row.state, map: row.map }}
                    onChange={(updated) =>
                      updateJsonRows(
                        i,
                        jsonRows.map((r, j) =>
                          j === ri ? { ...r, map: updated.map } : r,
                        ),
                      )
                    }
                  />
                </div>
              ))}
              <button
                onClick={() =>
                  updateJsonRows(i, [
                    ...jsonRows,
                    { state: "", path: "", type: "string" },
                  ])
                }
                style={{
                  fontSize: "var(--font-size-sm)",
                  color: "var(--accent)",
                  padding: "var(--space-xs) 0",
                }}
              >
                + Add Field
              </button>
              <RequireInput
                key={requireToList(resp.require).join(",")}
                value={requireToList(resp.require)}
                onCommit={(keys) =>
                  updateResponse(
                    i,
                    buildJsonResponse(resp, jsonRows, keys, stateVars),
                  )
                }
              />
            </div>
          ) : draft.transport === "osc" ? (
            <div style={{ marginBottom: "var(--space-md)" }}>
              <label style={labelStyle}>OSC Address Pattern</label>
              <input
                value={resp.address ?? pattern}
                onChange={(e) =>
                  updateResponse(
                    i,
                    buildResponse(e.target.value, mappings, resp, stateVars),
                  )
                }
                placeholder='e.g., /ch/01/mix/fader'
                style={{
                  width: "100%",
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--font-size-sm)",
                }}
              />
              <div
                style={{
                  fontSize: "11px",
                  color: "var(--text-muted)",
                  marginTop: "var(--space-xs)",
                }}
              >
                OSC address to match. Use * for wildcards (e.g., /ch/*/mix/fader).
              </div>
            </div>
          ) : (
            <div style={{ marginBottom: "var(--space-md)" }}>
              <label style={labelStyle}>Regex Pattern</label>
              <input
                value={pattern}
                onChange={(e) =>
                  updateResponse(i, buildResponse(e.target.value, mappings, resp, stateVars))
                }
                placeholder="e.g., In(\d+) All"
                style={{
                  width: "100%",
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--font-size-sm)",
                  borderColor: pattern && (() => { try { new RegExp(pattern); return false; } catch { return true; } })()
                    ? "var(--color-error, #f44336)" : undefined,
                }}
              />
              {pattern && (() => { try { new RegExp(pattern); return null; } catch (e) { return (
                <div style={{ fontSize: 11, color: "var(--color-error, #f44336)", marginTop: 2 }}>
                  Invalid regex: {String(e).replace("SyntaxError: ", "")}
                </div>
              ); } })()}
            </div>
          )}

          {!isJson && (
          <>
          <div
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Extract captured values into state variables:
          </div>
          {mappings.map((mapping, mi) => (
            <div
              key={mi}
              style={{
                display: "flex",
                gap: "var(--space-sm)",
                marginBottom: "var(--space-xs)",
                alignItems: "center",
              }}
            >
              <label
                style={{
                  fontSize: "var(--font-size-sm)",
                  color: "var(--text-muted)",
                  width: 80,
                  whiteSpace: "nowrap",
                }}
                title={draft.transport === "osc"
                  ? `Argument index ${mapping.arg ?? mapping.group} from the OSC message`
                  : `Capture group ${mapping.group} from the regex pattern (the ${_ordinal(mapping.group)} set of parentheses)`}
              >
                {draft.transport === "osc" ? `Arg ${mapping.arg ?? mapping.group}` : `Capture ${mapping.group}`} →
              </label>
              <select
                value={mapping.state}
                onChange={(e) => {
                  const next = [...mappings];
                  next[mi] = { ...mapping, state: e.target.value };
                  updateResponse(i, buildResponse(pattern, next, resp, stateVars));
                }}
                style={{ flex: 1, fontSize: "var(--font-size-sm)" }}
              >
                <option value="">Select state variable...</option>
                {stateVarNames.map((sv) => (
                  <option key={sv} value={sv}>
                    {sv}
                  </option>
                ))}
              </select>
              <select
                value={mapping.type ?? "string"}
                onChange={(e) => {
                  const next = [...mappings];
                  next[mi] = { ...mapping, type: e.target.value };
                  updateResponse(i, buildResponse(pattern, next, resp, stateVars));
                }}
                style={{ width: 90, fontSize: "var(--font-size-sm)" }}
              >
                <option value="string">String</option>
                <option value="integer">Integer</option>
                <option value="float">Float</option>
                <option value="boolean">Boolean</option>
              </select>
              <button
                onClick={() => {
                  const next = mappings.filter((_, j) => j !== mi);
                  updateResponse(i, buildResponse(pattern, next, resp, stateVars));
                }}
                style={{ padding: "2px", color: "var(--text-muted)" }}
              >
                <Trash2 size={12} />
              </button>
              <ValueMapEditor
                mapping={mapping}
                onChange={(updated) => {
                  const next = [...mappings];
                  next[mi] = updated;
                  updateResponse(i, buildResponse(pattern, next, resp, stateVars));
                }}
              />
            </div>
          ))}
          <button
            onClick={() => {
              if (draft.transport === "osc") {
                const nextArg = mappings.length > 0
                  ? Math.max(...mappings.map((m) => m.arg ?? m.group ?? 0)) + 1
                  : 0;
                updateResponse(i, buildResponse(
                  resp.address ?? pattern,
                  [
                    ...mappings,
                    { group: 0, arg: nextArg, state: "", type: "float" },
                  ],
                  resp,
                  stateVars,
                ));
              } else {
                const nextGroup =
                  mappings.length > 0
                    ? Math.max(...mappings.map((m) => m.group)) + 1
                    : 1;
                updateResponse(i, buildResponse(pattern, [
                  ...mappings,
                  { group: nextGroup, state: "", type: "string" },
                ], resp, stateVars));
              }
            }}
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--accent)",
              padding: "var(--space-xs) 0",
            }}
          >
            + Add Mapping
          </button>
          </>
          )}
          <div
            style={{
              display: "flex",
              gap: "var(--space-sm)",
              alignItems: "center",
              marginTop: "var(--space-sm)",
            }}
          >
            <label
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--text-muted)",
                whiteSpace: "nowrap",
              }}
            >
              Throttle (s)
            </label>
            <input
              type="number"
              value={resp.throttle ?? ""}
              onChange={(e) => {
                const raw = e.target.value;
                const n = parseFloat(raw);
                const next = { ...resp };
                if (raw === "" || Number.isNaN(n)) {
                  delete next.throttle;
                } else {
                  next.throttle = n;
                }
                updateResponse(i, next);
              }}
              min={0}
              step={0.1}
              placeholder="off"
              style={{ width: 80, fontSize: "var(--font-size-sm)" }}
            />
            <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>
              Drop re-matches of this rule for N seconds — for high-rate
              telemetry frames (meters). Leave blank for normal responses.
            </span>
          </div>
          {!isJson && Object.keys(draft.child_entity_types ?? {}).length > 0 && (
            <ChildSetEditor
              mode={draft.transport === "osc" ? "osc" : "regex"}
              entries={resp.child_set ?? []}
              childTypes={draft.child_entity_types ?? {}}
              onChange={(entries) => {
                const rebuilt = buildResponse(pattern, mappings, resp, stateVars);
                if (entries.length) {
                  rebuilt.child_set = entries;
                } else {
                  delete rebuilt.child_set;
                }
                updateResponse(i, rebuilt);
              }}
            />
          )}
        </div>
        );
      })}

      <button
        onClick={addResponse}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          padding: "var(--space-sm) var(--space-md)",
          borderRadius: "var(--border-radius)",
          background: "var(--bg-hover)",
          fontSize: "var(--font-size-sm)",
        }}
      >
        <Plus size={14} /> Add Response Pattern
      </button>
    </div>
  );
}


/** Comma-separated `require:` scope for a json rule — the rule only applies
 *  to bodies carrying every named key. Committed on blur/Enter so a comma
 *  the user just typed isn't eaten by re-normalization mid-keystroke. */
function RequireInput({
  value,
  onCommit,
}: {
  value: string[];
  onCommit: (keys: string[]) => void;
}) {
  const [text, setText] = useState(value.join(", "));
  const commit = () => {
    const keys = parseRequireText(text);
    if (keys.join(",") !== value.join(",")) onCommit(keys);
    setText(keys.join(", "));
  };
  return (
    <div
      style={{
        display: "flex",
        gap: "var(--space-sm)",
        alignItems: "center",
        marginTop: "var(--space-sm)",
      }}
    >
      <label
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          whiteSpace: "nowrap",
        }}
      >
        Only when body has key(s)
      </label>
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
        }}
        placeholder="serialNumber, status"
        title="Apply this rule only to bodies carrying every named JSON key (comma-separated; dot paths allowed). Leave blank to apply to any JSON body — scope it when different endpoints reuse a field name."
        style={{
          flex: 1,
          fontFamily: "var(--font-mono)",
          fontSize: "var(--font-size-sm)",
        }}
      />
    </div>
  );
}

/** Route captures into child-entity state: one row per child_set entry —
 *  pick the child type, say which capture (or literal) is the child ID, and
 *  map child properties to captures or literals. Only offered when the
 *  driver declares child_entity_types. In "osc" mode (address-matched rules
 *  — no capture groups) the ID comes from an address segment ("seg:1") and
 *  property values from positional args ("arg:0"). */
function ChildSetEditor({
  mode,
  entries,
  childTypes,
  onChange,
}: {
  mode: "regex" | "osc";
  entries: DriverChildSetEntry[];
  childTypes: Record<string, DriverChildEntityType>;
  onChange: (entries: DriverChildSetEntry[]) => void;
}) {
  const [open, setOpen] = useState(entries.length > 0);
  const typeNames = Object.keys(childTypes);
  const isOsc = mode === "osc";
  const idToText = isOsc ? oscChildIdToText : childIdToText;

  const updateEntry = (idx: number, updated: DriverChildSetEntry) => {
    const next = [...entries];
    next[idx] = updated;
    onChange(next);
  };

  const addEntry = () => {
    onChange([
      ...entries,
      { type: typeNames[0] ?? "", id: isOsc ? { segment: 1 } : "$1", state: {} },
    ]);
    setOpen(true);
  };

  if (!open && entries.length === 0) {
    return (
      <div>
        <button
          onClick={addEntry}
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--accent)",
            padding: "var(--space-xs) 0",
            display: "block",
          }}
        >
          + Route to Child Entities
        </button>
      </div>
    );
  }

  return (
    <div
      style={{
        marginTop: "var(--space-sm)",
        padding: "var(--space-sm)",
        background: "var(--bg-hover)",
        borderRadius: "var(--border-radius)",
      }}
      data-testid="child-set-editor"
    >
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
          marginBottom: "var(--space-xs)",
        }}
      >
        Route captured values into child entities:
      </div>
      {entries.map((entry, idx) => {
        const props = Object.keys(
          childTypes[entry.type]?.state_variables ?? {},
        );
        const stateEntries = Object.entries(entry.state ?? {});
        return (
          <div
            key={idx}
            style={{
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              padding: "var(--space-xs) var(--space-sm)",
              marginBottom: "var(--space-xs)",
              background: "var(--bg-surface)",
            }}
          >
            <div
              style={{
                display: "flex",
                gap: "var(--space-sm)",
                alignItems: "center",
                marginBottom: "var(--space-xs)",
              }}
            >
              <select
                value={entry.type}
                onChange={(e) =>
                  updateEntry(idx, { ...entry, type: e.target.value, state: {} })
                }
                style={{ fontSize: "var(--font-size-sm)" }}
              >
                {typeNames.map((t) => (
                  <option key={t} value={t}>
                    {childTypes[t]?.label || t}
                  </option>
                ))}
              </select>
              <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                ID from
              </span>
              <input
                value={idToText(entry.id)}
                onChange={(e) => {
                  updateEntry(idx, {
                    ...entry,
                    id: isOsc
                      ? oscChildIdFromParts(e.target.value, childIdMap(entry.id))
                      : childIdFromParts(e.target.value, childIdMap(entry.id)),
                  });
                }}
                placeholder={isOsc ? "seg:1 or literal" : "$1 or a number"}
                title={
                  isOsc
                    ? "Which address segment holds the child ID (seg:1 = the second /-separated part, 0-based) — or a literal ID when the address is specific to one child"
                    : "Which capture group holds the child ID ($1, $2, ...) — or a literal ID when the pattern is specific to one child"
                }
                style={{
                  width: 110,
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--font-size-sm)",
                }}
              />
              <span style={{ flex: 1 }} />
              <button
                onClick={() => onChange(entries.filter((_, j) => j !== idx))}
                style={{ padding: "2px", color: "var(--text-muted)" }}
              >
                <Trash2 size={12} />
              </button>
            </div>
            {(isOsc
              ? /^seg:\d+$/.test(idToText(entry.id))
              : /^\$\d+$/.test(idToText(entry.id))) && (
              <WireIdMapRows
                idMap={childIdMap(entry.id)}
                onChange={(map) =>
                  updateEntry(idx, {
                    ...entry,
                    id: isOsc
                      ? oscChildIdFromParts(idToText(entry.id), map)
                      : childIdFromParts(idToText(entry.id), map),
                  })
                }
              />
            )}
            {stateEntries.map(([prop, expr], si) => (
              <div
                key={si}
                style={{
                  display: "flex",
                  gap: "var(--space-sm)",
                  alignItems: "center",
                  marginBottom: 2,
                }}
              >
                <select
                  value={prop}
                  onChange={(e) => {
                    const nextState: Record<string, unknown> = {};
                    for (const [k, v] of stateEntries) {
                      nextState[k === prop ? e.target.value : k] = v;
                    }
                    updateEntry(idx, { ...entry, state: nextState });
                  }}
                  style={{ flex: 1, fontSize: "var(--font-size-sm)" }}
                >
                  <option value="">Select property...</option>
                  {props.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
                <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                  =
                </span>
                <input
                  value={isOsc ? oscChildPropToText(expr) : String(expr ?? "")}
                  onChange={(e) => {
                    const nextValue = isOsc
                      ? oscChildPropFromText(e.target.value, expr)
                      : e.target.value;
                    const nextState = { ...entry.state, [prop]: nextValue };
                    updateEntry(idx, { ...entry, state: nextState });
                  }}
                  placeholder={isOsc ? "arg:0 or literal" : "$2 or literal"}
                  title={
                    isOsc
                      ? "A positional OSC argument (arg:0 is the first) or a literal value; coerced by the property's declared type"
                      : "A capture group ($2) or a literal value; coerced by the property's declared type"
                  }
                  style={{
                    width: 110,
                    fontFamily: "var(--font-mono)",
                    fontSize: "var(--font-size-sm)",
                  }}
                />
                <button
                  onClick={() => {
                    const nextState = { ...entry.state };
                    delete nextState[prop];
                    updateEntry(idx, { ...entry, state: nextState });
                  }}
                  style={{ padding: "2px", color: "var(--text-muted)" }}
                >
                  <Trash2 size={10} />
                </button>
              </div>
            ))}
            <button
              onClick={() => {
                const unused = props.find((p) => !(p in (entry.state ?? {})));
                updateEntry(idx, {
                  ...entry,
                  state: {
                    ...entry.state,
                    [unused ?? ""]: isOsc ? { arg: 0 } : "$2",
                  },
                });
              }}
              style={{ fontSize: "11px", color: "var(--accent)", padding: "2px 0" }}
            >
              + Property
            </button>
          </div>
        );
      })}
      <button
        onClick={addEntry}
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--accent)",
          padding: "var(--space-xs) 0",
        }}
      >
        + Route to Child Entities
      </button>
    </div>
  );
}

function ValueMapEditor({
  mapping,
  onChange,
}: {
  mapping: DriverResponseMapping;
  onChange: (updated: DriverResponseMapping) => void;
}) {
  const [open, setOpen] = useState(false);
  const map = mapping.map ?? {};
  const entries = Object.entries(map);
  const hasMap = entries.length > 0;

  const toggleMap = () => {
    if (open && !hasMap) {
      setOpen(false);
      return;
    }
    setOpen(!open);
  };

  const addEntry = () => {
    // No-op while a blank draft row is pending — a second "" key would
    // silently reset the first draft's value in the backing record.
    const next = addValueMapEntry(map);
    if (next) onChange({ ...mapping, map: next });
    if (!open) setOpen(true);
  };

  const removeEntry = (key: string) => {
    const next = { ...map };
    delete next[key];
    onChange({ ...mapping, map: Object.keys(next).length > 0 ? next : undefined });
  };

  const setEntryValue = (key: string, value: string) => {
    onChange({ ...mapping, map: { ...map, [key]: value } });
  };

  const renameEntry = (oldKey: string, newKey: string): RenameResult => {
    const check = checkValueMapKeyRename(newKey, oldKey, Object.keys(map));
    if (!check.ok || newKey === oldKey) return check;
    onChange({ ...mapping, map: renameValueMapKey(map, oldKey, newKey) });
    return { ok: true };
  };

  return (
    <div style={{ gridColumn: "1 / -1", width: "100%" }}>
      <button
        onClick={hasMap ? toggleMap : addEntry}
        style={{
          fontSize: "11px",
          color: "var(--text-muted)",
          display: "flex",
          alignItems: "center",
          gap: 2,
          padding: "2px 0",
        }}
      >
        {hasMap ? (
          <>
            {open ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
            {entries.length} value map{entries.length !== 1 ? "s" : ""}
          </>
        ) : (
          <span style={{ color: "var(--accent)" }}>+ Value Map</span>
        )}
      </button>
      {open && (
        <div
          style={{
            marginTop: "var(--space-xs)",
            padding: "var(--space-sm)",
            background: "var(--bg-hover)",
            borderRadius: "var(--border-radius)",
          }}
        >
          <div style={{ fontSize: "11px", color: "var(--text-muted)", marginBottom: "var(--space-xs)" }}>
            Map raw values to friendly names (e.g., &quot;01&quot; → &quot;on&quot;)
          </div>
          {entries.map(([key, value], i) => (
            <div key={i} style={{ display: "flex", gap: 4, marginBottom: 2, alignItems: "flex-start" }}>
              <IdRenameInput
                value={key}
                sanitize={(raw) => raw}
                onCommit={(next) => renameEntry(key, next)}
                placeholder="raw"
                style={{ width: 80, fontFamily: "var(--font-mono)", fontSize: "11px" }}
              />
              <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>→</span>
              <input
                value={value}
                onChange={(e) => setEntryValue(key, e.target.value)}
                placeholder="mapped"
                style={{ width: 80, fontFamily: "var(--font-mono)", fontSize: "11px" }}
              />
              <button
                onClick={() => removeEntry(key)}
                style={{ padding: 1, color: "var(--text-muted)" }}
              >
                <Trash2 size={10} />
              </button>
            </div>
          ))}
          <button
            onClick={addEntry}
            style={{ fontSize: "11px", color: "var(--accent)", padding: "2px 0" }}
          >
            + Add
          </button>
        </div>
      )}
    </div>
  );
}

/** Optional wire-ID translation for a capture-ref child_set id: rows of
 *  "wire id on the device" -> "child ID in OpenAVC", for protocols whose
 *  channel numbers differ from the children (0-based wire, ST codes). A
 *  captured id the map doesn't cover skips the entry at runtime. */
function WireIdMapRows({
  idMap,
  onChange,
}: {
  idMap: Record<string, string | number> | undefined;
  onChange: (map: Record<string, string | number> | undefined) => void;
}) {
  const rows = Object.entries(idMap ?? {});
  if (rows.length === 0) {
    return (
      <button
        onClick={() => onChange({ "": "" })}
        title="Translate the captured wire id to the child ID (e.g. a 0-based protocol channel to a 1-based child)"
        style={{ fontSize: "11px", color: "var(--accent)", padding: "2px 0" }}
      >
        + Wire ID map
      </button>
    );
  }
  const rebuild = (
    mutate: (next: Record<string, string | number>) => void,
  ) => {
    const next: Record<string, string | number> = { ...(idMap ?? {}) };
    mutate(next);
    onChange(Object.keys(next).length > 0 ? next : undefined);
  };
  return (
    <div style={{ margin: "2px 0 var(--space-xs) 0" }}>
      <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>
        Wire ID → Child ID
      </div>
      {rows.map(([wire, local], ri) => (
        <div
          key={ri}
          style={{
            display: "flex",
            gap: "var(--space-xs)",
            alignItems: "center",
            marginBottom: 2,
          }}
        >
          <input
            value={wire}
            onChange={(e) =>
              rebuild((next) => {
                const rebuilt: Record<string, string | number> = {};
                for (const [k, v] of Object.entries(next)) {
                  rebuilt[k === wire ? e.target.value : k] = v;
                }
                for (const k of Object.keys(next)) delete next[k];
                Object.assign(next, rebuilt);
              })
            }
            placeholder="wire id"
            style={{
              width: 70,
              fontFamily: "var(--font-mono)",
              fontSize: "var(--font-size-sm)",
            }}
          />
          <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>→</span>
          <input
            value={String(local)}
            onChange={(e) =>
              rebuild((next) => {
                next[wire] = /^\d+$/.test(e.target.value)
                  ? parseInt(e.target.value, 10)
                  : e.target.value;
              })
            }
            placeholder="child id"
            style={{
              width: 70,
              fontFamily: "var(--font-mono)",
              fontSize: "var(--font-size-sm)",
            }}
          />
          <button
            onClick={() => rebuild((next) => delete next[wire])}
            style={{ padding: 1, color: "var(--text-muted)" }}
          >
            <Trash2 size={10} />
          </button>
        </div>
      ))}
      <button
        onClick={() => rebuild((next) => {
          if (!("" in next)) next[""] = "";
        })}
        style={{ fontSize: "11px", color: "var(--accent)", padding: "2px 0" }}
      >
        + Add
      </button>
    </div>
  );
}
