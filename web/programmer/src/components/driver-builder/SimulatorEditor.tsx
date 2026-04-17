import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import type { DriverDefinition, DriverSimulatorDef } from "../../api/types";

interface SimulatorEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

export function SimulatorEditor({ draft, onUpdate }: SimulatorEditorProps) {
  const sim: DriverSimulatorDef = draft.simulator ?? {};

  const update = (partial: Partial<DriverSimulatorDef>) => {
    onUpdate({ simulator: { ...sim, ...partial } });
  };

  const rowStyle: React.CSSProperties = {
    marginBottom: "var(--space-md)",
  };

  const helpStyle: React.CSSProperties = {
    fontSize: "11px",
    color: "var(--text-muted)",
    marginTop: "var(--space-xs)",
  };

  // Collect state variable names for initial state
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
        Configure how this driver behaves in the device simulator. All YAML drivers
        auto-generate basic simulation, but setting initial state values and adding
        command handlers makes the simulation more realistic.
      </p>

      {/* Initial State */}
      <div style={rowStyle}>
        <h3 style={{ fontSize: "var(--font-size-base)", marginBottom: "var(--space-sm)" }}>
          Initial State Values
        </h3>
        <p style={helpStyle}>
          Default values for each state variable when the simulator starts. If not set,
          the simulator uses the driver defaults (0 for numbers, false for booleans).
        </p>
        <div style={{ marginTop: "var(--space-sm)" }}>
          {stateVarNames.map((varName) => {
            const varDef = draft.state_variables[varName];
            const initState = sim.initial_state ?? {};
            return (
              <div
                key={varName}
                style={{
                  display: "flex",
                  gap: "var(--space-sm)",
                  alignItems: "center",
                  marginBottom: "var(--space-xs)",
                }}
              >
                <label
                  style={{
                    fontSize: "var(--font-size-sm)",
                    fontFamily: "var(--font-mono)",
                    width: 160,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                  title={varName}
                >
                  {varName}
                </label>
                <input
                  value={
                    initState[varName] !== undefined
                      ? String(initState[varName])
                      : ""
                  }
                  onChange={(e) => {
                    const val = e.target.value;
                    let parsed: unknown = val;
                    if (varDef.type === "boolean") {
                      parsed = val.toLowerCase() === "true";
                    } else if (varDef.type === "integer") {
                      parsed = parseInt(val) || 0;
                    } else if (varDef.type === "number") {
                      parsed = parseFloat(val) || 0;
                    }
                    update({
                      initial_state: {
                        ...initState,
                        [varName]: parsed,
                      },
                    });
                  }}
                  placeholder={
                    varDef.type === "boolean"
                      ? "true / false"
                      : varDef.type === "enum"
                        ? (varDef.values ?? [])[0] ?? ""
                        : ""
                  }
                  style={{
                    flex: 1,
                    fontFamily: "var(--font-mono)",
                    fontSize: "var(--font-size-sm)",
                  }}
                />
                <span
                  style={{
                    fontSize: "11px",
                    color: "var(--text-muted)",
                    width: 60,
                  }}
                >
                  {varDef.type}
                </span>
              </div>
            );
          })}
          {stateVarNames.length === 0 && (
            <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
              Define state variables first (State Variables tab).
            </div>
          )}
        </div>
      </div>

      {/* Delays */}
      <div style={rowStyle}>
        <h3 style={{ fontSize: "var(--font-size-base)", marginBottom: "var(--space-sm)" }}>
          Response Delay
        </h3>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
          <input
            type="number"
            value={sim.delays?.command_response ?? 0.05}
            onChange={(e) =>
              update({ delays: { ...sim.delays, command_response: parseFloat(e.target.value) || 0.05 } })
            }
            min={0}
            step={0.01}
            style={{ width: 100 }}
          />
          <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
            seconds between receiving a command and sending a response
          </span>
        </div>
      </div>

      {/* Error Modes */}
      <div style={rowStyle}>
        <ErrorModesEditor sim={sim} onUpdate={update} />
      </div>

      {/* Raw YAML for advanced features */}
      <div style={rowStyle}>
        <h3 style={{ fontSize: "var(--font-size-base)", marginBottom: "var(--space-sm)" }}>
          Command Handlers (Advanced)
        </h3>
        <p style={helpStyle}>
          Command handlers define how the simulator responds to specific commands.
          For YAML drivers, basic handlers are auto-generated from your commands and
          responses. Add custom handlers here for more realistic behavior.
          See the Writing Simulators guide for the full syntax.
        </p>
        {(sim.command_handlers ?? []).length > 0 && (
          <div
            style={{
              background: "var(--bg-hover)",
              padding: "var(--space-sm) var(--space-md)",
              borderRadius: "var(--border-radius)",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-muted)",
              marginTop: "var(--space-sm)",
            }}
          >
            {(sim.command_handlers ?? []).length} command handler(s) defined.
            Edit the .avcdriver file directly for full control over handlers.
          </div>
        )}
      </div>
    </div>
  );
}


function ErrorModesEditor({
  sim,
  onUpdate,
}: {
  sim: DriverSimulatorDef;
  onUpdate: (partial: Partial<DriverSimulatorDef>) => void;
}) {
  const [newMode, setNewMode] = useState("");
  const errorModes = sim.error_modes ?? {};

  const addMode = () => {
    const key = newMode.replace(/[^a-z0-9_]/gi, "_").toLowerCase();
    if (!key || key in errorModes) return;
    onUpdate({
      error_modes: {
        ...errorModes,
        [key]: { behavior: "no_response", description: "" },
      },
    });
    setNewMode("");
  };

  return (
    <div>
      <h3 style={{ fontSize: "var(--font-size-base)", marginBottom: "var(--space-sm)" }}>
        Error Modes
      </h3>
      <p style={{ fontSize: "11px", color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
        Define error scenarios that can be injected during simulation to test error handling.
      </p>

      {Object.entries(errorModes).map(([key, mode]) => (
        <div
          key={key}
          style={{
            display: "flex",
            gap: "var(--space-sm)",
            alignItems: "center",
            marginBottom: "var(--space-xs)",
            padding: "var(--space-xs) var(--space-sm)",
            background: "var(--bg-surface)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
          }}
        >
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "var(--font-size-sm)",
              width: 160,
            }}
          >
            {key}
          </span>
          <select
            value={mode.behavior}
            onChange={(e) =>
              onUpdate({
                error_modes: {
                  ...errorModes,
                  [key]: { ...mode, behavior: e.target.value },
                },
              })
            }
            style={{ width: 160, fontSize: "var(--font-size-sm)" }}
          >
            <option value="no_response">No Response</option>
            <option value="corrupt_response">Corrupt Response</option>
            <option value="disconnect">Disconnect</option>
            <option value="custom_state">Custom State</option>
          </select>
          <input
            value={mode.description ?? ""}
            onChange={(e) =>
              onUpdate({
                error_modes: {
                  ...errorModes,
                  [key]: { ...mode, description: e.target.value },
                },
              })
            }
            placeholder="Description"
            style={{ flex: 1, fontSize: "var(--font-size-sm)" }}
          />
          <button
            onClick={() => {
              const next = { ...errorModes };
              delete next[key];
              onUpdate({ error_modes: next });
            }}
            style={{ padding: "2px", color: "var(--text-muted)" }}
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}

      <div style={{ display: "flex", gap: "var(--space-sm)", marginTop: "var(--space-sm)" }}>
        <input
          value={newMode}
          onChange={(e) => setNewMode(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addMode()}
          placeholder="Error mode name"
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "var(--font-size-sm)",
            width: 200,
          }}
        />
        <button
          onClick={addMode}
          disabled={!newMode}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            fontSize: "var(--font-size-sm)",
            padding: "var(--space-xs) var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-hover)",
          }}
        >
          <Plus size={12} /> Add Error Mode
        </button>
      </div>
    </div>
  );
}
