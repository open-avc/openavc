import { Plus, Trash2 } from "lucide-react";
import type { DriverDefinition, DriverResponseDef } from "../../api/types";

interface ResponseBuilderProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

export function ResponseBuilder({ draft, onUpdate }: ResponseBuilderProps) {
  const responses = draft.responses;

  const addResponse = () => {
    onUpdate({
      responses: [
        ...responses,
        { pattern: "", mappings: [{ group: 1, state: "", type: "string" }] },
      ],
    });
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
        Define regex patterns to match device responses and map capture groups
        to state variables.
      </p>

      {responses.map((resp, i) => (
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

          <div style={{ marginBottom: "var(--space-md)" }}>
            <label style={labelStyle}>Regex Pattern</label>
            <input
              value={resp.pattern}
              onChange={(e) =>
                updateResponse(i, { ...resp, pattern: e.target.value })
              }
              placeholder="e.g., In(\d+) All"
              style={{
                width: "100%",
                fontFamily: "var(--font-mono)",
                fontSize: "var(--font-size-sm)",
                borderColor: resp.pattern && (() => { try { new RegExp(resp.pattern); return false; } catch { return true; } })()
                  ? "var(--color-error, #f44336)" : undefined,
              }}
            />
            {resp.pattern && (() => { try { new RegExp(resp.pattern); return null; } catch (e) { return (
              <div style={{ fontSize: 11, color: "var(--color-error, #f44336)", marginTop: 2 }}>
                Invalid regex: {String(e).replace("SyntaxError: ", "")}
              </div>
            ); } })()}
          </div>

          <div
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Mappings
          </div>
          {resp.mappings.map((mapping, mi) => (
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
                  width: 60,
                }}
              >
                Group {mapping.group}
              </label>
              <select
                value={mapping.state}
                onChange={(e) => {
                  const next = [...resp.mappings];
                  next[mi] = { ...mapping, state: e.target.value };
                  updateResponse(i, { ...resp, mappings: next });
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
                  const next = [...resp.mappings];
                  next[mi] = { ...mapping, type: e.target.value };
                  updateResponse(i, { ...resp, mappings: next });
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
                  const next = resp.mappings.filter((_, j) => j !== mi);
                  updateResponse(i, { ...resp, mappings: next });
                }}
                style={{ padding: "2px", color: "var(--text-muted)" }}
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
          <button
            onClick={() => {
              const nextGroup =
                resp.mappings.length > 0
                  ? Math.max(...resp.mappings.map((m) => m.group)) + 1
                  : 1;
              updateResponse(i, {
                ...resp,
                mappings: [
                  ...resp.mappings,
                  { group: nextGroup, state: "", type: "string" },
                ],
              });
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
      ))}

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
