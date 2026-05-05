import { useEffect, useMemo, useState } from "react";
import { Send, AlertCircle, ChevronRight } from "lucide-react";
import type {
  DriverDefinition,
  DriverCommandDef,
  DriverParamDef,
} from "../../api/types";
import * as api from "../../api/restClient";
import type { TestCommandResult } from "../../api/driverClient";

interface LiveTestPanelProps {
  draft: DriverDefinition;
}

interface ResultEntry {
  command: string;
  sent: string | null;
  received: string[];
  state_changes: Record<string, unknown>;
  error: string | null;
  timestamp: number;
}

const RAW_COMMAND = "__raw__";

/**
 * Live driver tester. Sends commands through the real ConfigurableDriver
 * runtime — auth handshake and on_connect run before each test, so anything
 * that works here will work at runtime.
 *
 * Three modes per the driver's transport:
 *   - TCP / serial: pick a defined command, fill its params, run.
 *   - HTTP:        same — the request is built from method/path/headers/body
 *                  declared on the command.
 *   - OSC:         same — args come from the command definition.
 *
 * A "raw" mode is also available for one-off probes that aren't yet declared
 * as commands. Raw mode skips auth/on_connect.
 */
export function LiveTestPanel({ draft }: LiveTestPanelProps) {
  const transport = draft.transport || "tcp";
  const defaultPort =
    typeof draft.default_config?.port === "number"
      ? (draft.default_config.port as number)
      : transport === "http"
        ? 80
        : transport === "osc"
          ? 8000
          : 23;

  const [host, setHost] = useState("");
  const [port, setPort] = useState(String(defaultPort));
  const [configOverrides, setConfigOverrides] = useState<Record<string, string>>(
    {},
  );
  const [selectedCommand, setSelectedCommand] = useState<string>(() => {
    const names = Object.keys(draft.commands);
    return names[0] ?? RAW_COMMAND;
  });
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [rawString, setRawString] = useState("");
  const [results, setResults] = useState<ResultEntry[]>([]);
  const [sending, setSending] = useState(false);

  // Reset port + selection when the draft's transport switches under us.
  useEffect(() => {
    setPort(String(defaultPort));
  }, [defaultPort]);
  useEffect(() => {
    if (selectedCommand !== RAW_COMMAND && !(selectedCommand in draft.commands)) {
      const names = Object.keys(draft.commands);
      setSelectedCommand(names[0] ?? RAW_COMMAND);
    }
  }, [draft.commands, selectedCommand]);

  // Seed param values with the command's defaults whenever the command changes.
  useEffect(() => {
    if (selectedCommand === RAW_COMMAND) {
      setParamValues({});
      return;
    }
    const cmd = draft.commands[selectedCommand];
    if (!cmd) return;
    const seeded: Record<string, string> = {};
    for (const [name, def] of Object.entries(cmd.params ?? {})) {
      seeded[name] = def.default !== undefined ? String(def.default) : "";
    }
    setParamValues(seeded);
  }, [selectedCommand, draft.commands]);

  // Authoring-time config fields (anything declared in config_schema that
  // isn't a baseline transport key). Surface these so users can fill in
  // credentials or instance tags without wiring up a real device first.
  const customConfigFields = useMemo(() => {
    const builtin = new Set([
      "host",
      "port",
      "baudrate",
      "parity",
      "poll_interval",
      "inter_command_delay",
    ]);
    const schema = (draft.config_schema ?? {}) as Record<
      string,
      { label?: string; secret?: boolean; type?: string }
    >;
    return Object.entries(schema)
      .filter(([k]) => !builtin.has(k))
      .map(([key, def]) => ({
        key,
        label: def.label ?? key,
        secret: !!def.secret,
        type: def.type ?? "string",
      }));
  }, [draft.config_schema]);

  const command: DriverCommandDef | null =
    selectedCommand !== RAW_COMMAND ? draft.commands[selectedCommand] ?? null : null;

  const canSend =
    !!host &&
    (selectedCommand !== RAW_COMMAND
      ? command !== null
      : rawString.trim().length > 0);

  const handleSend = async () => {
    if (!canSend) return;
    setSending(true);
    try {
      const overrides: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(configOverrides)) {
        if (v !== "") overrides[k] = v;
      }

      const data: Parameters<typeof api.testDriverCommand>[1] =
        selectedCommand === RAW_COMMAND
          ? {
              host,
              port: parseInt(port) || defaultPort,
              transport,
              command_string: rawString,
              delimiter: draft.delimiter,
              timeout: 5,
            }
          : {
              host,
              port: parseInt(port) || defaultPort,
              transport,
              definition: draft,
              command_name: selectedCommand,
              params: coerceParams(paramValues, command?.params ?? {}),
              config_overrides: overrides,
              timeout: 5,
            };

      const result: TestCommandResult = await api.testDriverCommand(
        draft.id || "test",
        data,
      );
      setResults((prev) => [
        {
          command:
            selectedCommand === RAW_COMMAND
              ? rawString
              : command?.label || selectedCommand,
          sent: result.sent,
          received: result.received,
          state_changes: result.state_changes,
          error: result.error,
          timestamp: Date.now(),
        },
        ...prev,
      ]);
    } catch (e) {
      setResults((prev) => [
        {
          command:
            selectedCommand === RAW_COMMAND
              ? rawString
              : command?.label || selectedCommand,
          sent: null,
          received: [],
          state_changes: {},
          error: e instanceof Error ? e.message : String(e),
          timestamp: Date.now(),
        },
        ...prev,
      ]);
    } finally {
      setSending(false);
    }
  };

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
  };
  const helpStyle: React.CSSProperties = {
    fontSize: "11px",
    color: "var(--text-muted)",
    marginTop: 4,
  };

  const onConnectCount = (draft.on_connect ?? []).length;
  const authEnabled = !!draft.auth;
  const productionPath =
    selectedCommand !== RAW_COMMAND && (onConnectCount > 0 || authEnabled);

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
        Send a command to a live device through the real driver runtime —
        auth and connect-sequence run first, parameters resolve the same way
        they will in production.
      </p>

      {/* Connection */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 110px",
          gap: "var(--space-md)",
          marginBottom: "var(--space-md)",
        }}
      >
        <div>
          <label style={labelStyle}>Host / IP Address</label>
          <input
            value={host}
            onChange={(e) => setHost(e.target.value)}
            placeholder="192.168.1.100"
            style={{ width: "100%" }}
          />
        </div>
        <div>
          <label style={labelStyle}>Port</label>
          <input
            value={port}
            onChange={(e) => setPort(e.target.value)}
            inputMode="numeric"
            style={{ width: "100%" }}
          />
          <div style={helpStyle}>{transport.toUpperCase()}</div>
        </div>
      </div>

      {/* Driver-declared config (credentials, instance tags, etc.) */}
      {customConfigFields.length > 0 && (
        <div style={{ marginBottom: "var(--space-md)" }}>
          <label style={labelStyle}>Driver Config</label>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
              gap: "var(--space-sm)",
              padding: "var(--space-sm)",
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-surface)",
            }}
          >
            {customConfigFields.map((field) => (
              <div key={field.key}>
                <label
                  style={{
                    display: "block",
                    fontSize: "11px",
                    color: "var(--text-muted)",
                    marginBottom: 2,
                  }}
                >
                  {field.label}
                </label>
                {field.type === "text" ? (
                  <textarea
                    value={configOverrides[field.key] ?? ""}
                    onChange={(e) =>
                      setConfigOverrides((prev) => ({
                        ...prev,
                        [field.key]: e.target.value,
                      }))
                    }
                    placeholder={field.key}
                    rows={4}
                    style={{
                      width: "100%",
                      fontFamily: "var(--font-mono)",
                      fontSize: "var(--font-size-sm)",
                      resize: "vertical",
                    }}
                  />
                ) : (
                  <input
                    type={field.secret ? "password" : "text"}
                    value={configOverrides[field.key] ?? ""}
                    onChange={(e) =>
                      setConfigOverrides((prev) => ({
                        ...prev,
                        [field.key]: e.target.value,
                      }))
                    }
                    placeholder={field.key}
                    style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                  />
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Command picker */}
      <div style={{ marginBottom: "var(--space-md)" }}>
        <label style={labelStyle}>Command</label>
        <select
          value={selectedCommand}
          onChange={(e) => setSelectedCommand(e.target.value)}
          style={{ width: "100%" }}
        >
          {Object.entries(draft.commands).map(([name, cmd]) => (
            <option key={name} value={name}>
              {cmd.label || name} ({name})
            </option>
          ))}
          <option value={RAW_COMMAND}>— Raw probe (no auth, no on_connect) —</option>
        </select>
      </div>

      {/* Per-command form */}
      {selectedCommand !== RAW_COMMAND && command && (
        <CommandPreview
          transport={transport}
          command={command}
          paramValues={paramValues}
          onParamChange={(name, value) =>
            setParamValues((prev) => ({ ...prev, [name]: value }))
          }
        />
      )}

      {/* Raw input */}
      {selectedCommand === RAW_COMMAND && (
        <div style={{ marginBottom: "var(--space-md)" }}>
          <label style={labelStyle}>
            {transport === "osc"
              ? "OSC Address"
              : transport === "http"
                ? "HTTP Request (e.g. GET /api/status)"
                : "Wire String"}
          </label>
          <input
            value={rawString}
            onChange={(e) => setRawString(e.target.value)}
            placeholder={
              transport === "osc"
                ? "/info"
                : transport === "http"
                  ? "GET /api/status"
                  : "%1POWR ?\\r"
            }
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSend();
            }}
            style={{
              width: "100%",
              fontFamily: "var(--font-mono)",
              fontSize: "var(--font-size-sm)",
            }}
          />
          <div style={helpStyle}>
            Bypasses the driver — sent as-is to {host || "the device"} on port{" "}
            {port || defaultPort}. Useful for one-off probes; for real testing
            pick a defined command above.
          </div>
        </div>
      )}

      {/* Status hint + send */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-md)",
          marginBottom: "var(--space-md)",
        }}
      >
        <div style={{ flex: 1, fontSize: "11px", color: "var(--text-muted)" }}>
          {productionPath ? (
            <>
              Will run{" "}
              {authEnabled && <span>login handshake</span>}
              {authEnabled && onConnectCount > 0 && " then "}
              {onConnectCount > 0 && (
                <span>
                  {onConnectCount} connect-sequence command
                  {onConnectCount === 1 ? "" : "s"}
                </span>
              )}{" "}
              before sending.
            </>
          ) : selectedCommand === RAW_COMMAND ? (
            <span>Raw mode — auth and on_connect are skipped.</span>
          ) : (
            <span>Direct connect, no auth or connect sequence configured.</span>
          )}
        </div>
        <button
          onClick={handleSend}
          disabled={!canSend || sending}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            padding: "var(--space-sm) var(--space-lg)",
            borderRadius: "var(--border-radius)",
            background: canSend ? "var(--accent-bg)" : "var(--bg-hover)",
            color: canSend ? "var(--text-on-accent)" : "var(--text-muted)",
            opacity: sending ? 0.6 : 1,
          }}
        >
          <Send size={14} /> {sending ? "Sending..." : "Send"}
        </button>
      </div>

      {/* Results log */}
      {results.length > 0 && (
        <div>
          <div
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Results
          </div>
          <div
            style={{
              background: "var(--bg-base)",
              borderRadius: "var(--border-radius)",
              border: "1px solid var(--border-color)",
              maxHeight: 360,
              overflow: "auto",
            }}
          >
            {results.map((r, i) => (
              <ResultRow
                key={i}
                entry={r}
                isLast={i === results.length - 1}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ResultRow({ entry, isLast }: { entry: ResultEntry; isLast: boolean }) {
  return (
    <div
      style={{
        padding: "var(--space-sm) var(--space-md)",
        borderBottom: isLast ? "none" : "1px solid var(--border-color)",
        fontFamily: "var(--font-mono)",
        fontSize: "var(--font-size-sm)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          color: "var(--text-secondary)",
          marginBottom: 4,
        }}
      >
        <span style={{ flex: 1 }}>{entry.command}</span>
        <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>
          {new Date(entry.timestamp).toLocaleTimeString()}
        </span>
      </div>
      {entry.sent && (
        <div style={{ color: "var(--text-muted)" }}>
          → {visibleBytes(entry.sent)}
        </div>
      )}
      {entry.received.map((r, j) => (
        <div key={j} style={{ color: "var(--color-success, #4caf50)" }}>
          ← {visibleBytes(r)}
        </div>
      ))}
      {Object.entries(entry.state_changes).length > 0 && (
        <div
          style={{
            marginTop: 4,
            padding: "4px 6px",
            background: "var(--bg-surface)",
            borderRadius: 4,
            fontSize: "11px",
          }}
        >
          <span style={{ color: "var(--text-muted)" }}>State changes:</span>{" "}
          {Object.entries(entry.state_changes).map(([k, v], i, arr) => (
            <span key={k}>
              <span style={{ color: "var(--accent)" }}>{k}</span>={String(v)}
              {i < arr.length - 1 ? ", " : ""}
            </span>
          ))}
        </div>
      )}
      {entry.error && (
        <div
          style={{
            color: "var(--color-error)",
            display: "flex",
            alignItems: "center",
            gap: 4,
            marginTop: 4,
          }}
        >
          <AlertCircle size={12} /> {entry.error}
        </div>
      )}
    </div>
  );
}

/**
 * Render an editable preview of the selected command — params first, then
 * a transport-specific summary of what will go on the wire.
 */
function CommandPreview({
  transport,
  command,
  paramValues,
  onParamChange,
}: {
  transport: string;
  command: DriverCommandDef;
  paramValues: Record<string, string>;
  onParamChange: (name: string, value: string) => void;
}) {
  const params = Object.entries(command.params ?? {});

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "11px",
    color: "var(--text-muted)",
    marginBottom: 2,
  };

  return (
    <div
      style={{
        marginBottom: "var(--space-md)",
        padding: "var(--space-md)",
        border: "1px solid var(--border-color)",
        borderRadius: "var(--border-radius)",
        background: "var(--bg-surface)",
      }}
    >
      {params.length > 0 ? (
        <>
          <div
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-sm)",
            }}
          >
            Parameters
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
              gap: "var(--space-sm)",
              marginBottom: "var(--space-md)",
            }}
          >
            {params.map(([name, def]) => (
              <div key={name}>
                <label style={labelStyle}>
                  {def.label || name}
                  {def.required ? " *" : ""}
                </label>
                <ParamInput
                  def={def}
                  value={paramValues[name] ?? ""}
                  onChange={(v) => onParamChange(name, v)}
                />
                {(def.help || def.description) && (
                  <div
                    style={{
                      fontSize: "10px",
                      color: "var(--text-muted)",
                      marginTop: 2,
                    }}
                  >
                    {def.help || def.description}
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      ) : (
        <div
          style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            marginBottom: "var(--space-sm)",
          }}
        >
          No parameters.
        </div>
      )}

      <div
        style={{
          fontSize: "11px",
          color: "var(--text-muted)",
          marginBottom: 4,
        }}
      >
        Wire format
      </div>
      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "var(--font-size-sm)",
          background: "var(--bg-base)",
          border: "1px solid var(--border-color)",
          borderRadius: "var(--border-radius)",
          padding: "var(--space-xs) var(--space-sm)",
          color: "var(--text-primary)",
          display: "flex",
          alignItems: "center",
          gap: 4,
          overflow: "auto",
        }}
      >
        <ChevronRight size={12} />
        <span style={{ whiteSpace: "pre" }}>
          {previewWire(transport, command, paramValues)}
        </span>
      </div>
    </div>
  );
}

function ParamInput({
  def,
  value,
  onChange,
}: {
  def: DriverParamDef;
  value: string;
  onChange: (v: string) => void;
}) {
  if (def.type === "enum" && def.values) {
    return (
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{ width: "100%" }}
      >
        {!def.required && <option value="">(none)</option>}
        {def.values.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>
    );
  }
  if (def.type === "boolean") {
    return (
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{ width: "100%" }}
      >
        <option value="">(none)</option>
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    );
  }
  if (def.type === "integer" || def.type === "number") {
    return (
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        min={def.min}
        max={def.max}
        step={def.type === "integer" ? 1 : "any"}
        style={{ width: "100%" }}
      />
    );
  }
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{ width: "100%", fontFamily: "var(--font-mono)" }}
    />
  );
}

function coerceParams(
  raw: Record<string, string>,
  defs: Record<string, DriverParamDef>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [name, val] of Object.entries(raw)) {
    if (val === "") continue;
    const def = defs[name];
    if (!def) {
      out[name] = val;
      continue;
    }
    if (def.type === "integer") {
      const n = parseInt(val, 10);
      if (!Number.isNaN(n)) out[name] = n;
    } else if (def.type === "number") {
      const n = parseFloat(val);
      if (!Number.isNaN(n)) out[name] = n;
    } else if (def.type === "boolean") {
      out[name] = val === "true";
    } else {
      out[name] = val;
    }
  }
  return out;
}

/** Substitute {placeholder} tokens against the param map for the wire preview. */
function previewWire(
  transport: string,
  command: DriverCommandDef,
  paramValues: Record<string, string>,
): string {
  const subst = (template: string): string =>
    template.replace(/\{(\w+)\}/g, (m, key) =>
      paramValues[key] !== undefined && paramValues[key] !== ""
        ? paramValues[key]
        : m,
    );

  if (command.address) {
    const addr = subst(command.address);
    const args = (command.args ?? [])
      .map((a) => `${a.type}=${subst(a.value)}`)
      .join(", ");
    return args ? `${addr} [${args}]` : addr;
  }

  if (command.method || command.path || transport === "http") {
    const method = (command.method || "GET").toUpperCase();
    const path = subst(command.path ?? "/");
    const headers = command.headers
      ? Object.entries(command.headers)
          .map(([k, v]) => `${k}: ${subst(v)}`)
          .join("\n")
      : "";
    const body = command.body ? subst(command.body) : "";
    return [`${method} ${path}`, headers, body].filter(Boolean).join("\n");
  }

  return subst(command.send ?? command.string ?? "");
}

function visibleBytes(s: string): string {
  // Show whitespace-significant bytes so authors can tell \r from \n.
  return s
    .replace(/\r/g, "\\r")
    .replace(/\n/g, "\\n")
    .replace(/\t/g, "\\t");
}
