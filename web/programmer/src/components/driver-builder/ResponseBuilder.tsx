import { useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import type { DriverDefinition, DriverResponseDef, DriverResponseMapping } from "../../api/types";

function _ordinal(n: number): string {
  if (n === 1) return "1st";
  if (n === 2) return "2nd";
  if (n === 3) return "3rd";
  return `${n}th`;
}

/** Read the pattern from whichever key is present. */
function getPattern(resp: DriverResponseDef): string {
  return resp.address ?? resp.pattern ?? resp.match ?? "";
}

/** Read mappings, converting set shorthand if needed. Preserves static
 *  literal values (so round-trip doesn't lose them on edit). */
function getMappings(resp: DriverResponseDef): DriverResponseMapping[] {
  if (resp.mappings) return resp.mappings;
  if (!resp.set) return [];
  const mappings: DriverResponseMapping[] = [];
  for (const [stateKey, valueExpr] of Object.entries(resp.set)) {
    if (typeof valueExpr === "string" && /^\$\d+$/.test(valueExpr)) {
      // Capture-group reference like "$1"
      const group = parseInt(valueExpr.slice(1), 10);
      mappings.push({ group, state: stateKey, type: "string" });
    } else {
      // Static literal — preserve the value verbatim under `value`
      mappings.push({ group: 0, state: stateKey, value: valueExpr });
    }
  }
  return mappings;
}

/** True if every mapping fits the `set:` shorthand: each is either a
 *  pure capture-group reference (no `type`/`map`/`arg` extras) or a
 *  static literal. Used to decide which output form preserves the
 *  driver author's original intent. */
function canUseSetShorthand(mappings: DriverResponseMapping[]): boolean {
  if (mappings.length === 0) return false;
  const seenStates = new Set<string>();
  for (const m of mappings) {
    if (!m.state) return false;
    if (seenStates.has(m.state)) return false;
    seenStates.add(m.state);
    if (m.arg !== undefined) return false;
    if (m.map !== undefined) return false;
    // Static literal mapping: group=0, value present
    if (m.group === 0 && m.value !== undefined) continue;
    // Capture-group mapping: group>0, no `type` or default string type
    if (m.group > 0 && (m.type === undefined || m.type === "string")) continue;
    return false;
  }
  return true;
}

/** Build a response def, preserving the original form (set: shorthand or
 *  mappings:) of the loaded response when the new mappings still fit. */
function buildResponse(
  pattern: string,
  mappings: DriverResponseMapping[],
  original: DriverResponseDef,
): DriverResponseDef {
  // OSC responses always use mappings + address.
  if (original.address !== undefined) {
    return { address: pattern, mappings };
  }
  // Choose set: shorthand when (a) the original used it AND (b) the
  // current mapping shape still fits the shorthand. Otherwise fall back
  // to the explicit mappings form.
  const originalWasSet = original.set !== undefined && original.mappings === undefined;
  if (originalWasSet && canUseSetShorthand(mappings)) {
    const set: Record<string, unknown> = {};
    for (const m of mappings) {
      if (m.group === 0 && m.value !== undefined) {
        set[m.state] = m.value;
      } else {
        set[m.state] = `$${m.group}`;
      }
    }
    return { match: pattern, set };
  }
  return { match: pattern, mappings };
}

interface ResponseBuilderProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

export function ResponseBuilder({ draft, onUpdate }: ResponseBuilderProps) {
  const responses = draft.responses ?? [];

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
          buildResponse("", [{ group: 1, state: "", type: "string" }], {}),
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
        Define patterns to match device responses and extract values into state
        variables. Use parentheses to capture the parts you want to extract.
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
        const pattern = getPattern(resp);
        const mappings = getMappings(resp);
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
            <span
              style={{
                fontSize: "var(--font-size-sm)",
                fontWeight: 600,
              }}
            >
              Response Pattern {i + 1}
            </span>
            <button
              onClick={() => removeResponse(i)}
              style={{ padding: "2px", color: "var(--text-muted)" }}
            >
              <Trash2 size={14} />
            </button>
          </div>

          {draft.transport === "osc" ? (
            <div style={{ marginBottom: "var(--space-md)" }}>
              <label style={labelStyle}>OSC Address Pattern</label>
              <input
                value={resp.address ?? pattern}
                onChange={(e) =>
                  updateResponse(i, { address: e.target.value, mappings })
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
                  updateResponse(i, buildResponse(e.target.value, mappings, resp))
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
                  updateResponse(i, buildResponse(pattern, next, resp));
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
                  updateResponse(i, buildResponse(pattern, next, resp));
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
                  updateResponse(i, buildResponse(pattern, next, resp));
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
                  updateResponse(i, buildResponse(pattern, next, resp));
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
                updateResponse(i, { address: resp.address ?? pattern, mappings: [
                  ...mappings,
                  { group: 0, arg: nextArg, state: "", type: "float" },
                ] });
              } else {
                const nextGroup =
                  mappings.length > 0
                    ? Math.max(...mappings.map((m) => m.group)) + 1
                    : 1;
                updateResponse(i, buildResponse(pattern, [
                  ...mappings,
                  { group: nextGroup, state: "", type: "string" },
                ], resp));
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
    onChange({ ...mapping, map: { ...map, "": "" } });
    if (!open) setOpen(true);
  };

  const removeEntry = (key: string) => {
    const next = { ...map };
    delete next[key];
    onChange({ ...mapping, map: Object.keys(next).length > 0 ? next : undefined });
  };

  const updateEntry = (oldKey: string, newKey: string, value: string) => {
    const next: Record<string, string> = {};
    for (const [k, v] of Object.entries(map)) {
      next[k === oldKey ? newKey : k] = k === oldKey ? value : v;
    }
    onChange({ ...mapping, map: next });
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
            <div key={i} style={{ display: "flex", gap: 4, marginBottom: 2, alignItems: "center" }}>
              <input
                value={key}
                onChange={(e) => updateEntry(key, e.target.value, value)}
                placeholder="raw"
                style={{ width: 80, fontFamily: "var(--font-mono)", fontSize: "11px" }}
              />
              <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>→</span>
              <input
                value={value}
                onChange={(e) => updateEntry(key, key, e.target.value)}
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
