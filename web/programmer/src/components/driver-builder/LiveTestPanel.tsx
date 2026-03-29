import { useState } from "react";
import { Send } from "lucide-react";
import type { DriverDefinition } from "../../api/types";
import * as api from "../../api/restClient";

interface LiveTestPanelProps {
  draft: DriverDefinition;
}

export function LiveTestPanel({ draft }: LiveTestPanelProps) {
  const [host, setHost] = useState("");
  const [port, setPort] = useState(
    String((draft.default_config.port as number | undefined) ?? 23)
  );
  const [command, setCommand] = useState("");
  const [results, setResults] = useState<
    { cmd: string; response: string | null; error: string | null }[]
  >([]);
  const [sending, setSending] = useState(false);

  const handleSend = async () => {
    if (!host || !command) return;
    setSending(true);
    try {
      const result = await api.testDriverCommand(draft.id || "test", {
        host,
        port: parseInt(port) || 23,
        transport: draft.transport,
        command_string: command,
        delimiter: draft.delimiter,
        timeout: 5,
      });
      setResults((prev) => [
        { cmd: command, response: result.response, error: result.error },
        ...prev,
      ]);
    } catch (e) {
      setResults((prev) => [
        { cmd: command, response: null, error: String(e) },
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

  return (
    <div>
      <p
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          marginBottom: "var(--space-md)",
        }}
      >
        Test commands against a live device. Enter the device's address and send
        raw command strings to see the response.
      </p>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 100px",
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
            style={{ width: "100%" }}
          />
        </div>
      </div>

      <div style={{ marginBottom: "var(--space-md)" }}>
        <label style={labelStyle}>Command String</label>
        <div style={{ display: "flex", gap: "var(--space-sm)" }}>
          <input
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            placeholder="e.g., %1POWR ?\r"
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            style={{
              flex: 1,
              fontFamily: "var(--font-mono)",
              fontSize: "var(--font-size-sm)",
            }}
          />
          <button
            onClick={handleSend}
            disabled={!host || !command || sending}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: host && command ? "var(--accent)" : "var(--bg-hover)",
              color:
                host && command
                  ? "var(--text-on-accent)"
                  : "var(--text-muted)",
              opacity: sending ? 0.6 : 1,
            }}
          >
            <Send size={14} /> Send
          </button>
        </div>
      </div>

      {/* Quick commands from definition */}
      {Object.keys(draft.commands).length > 0 && (
        <div style={{ marginBottom: "var(--space-md)" }}>
          <label style={labelStyle}>Quick Send from Commands</label>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "var(--space-xs)",
            }}
          >
            {Object.entries(draft.commands).map(([name, cmd]) => (
              <button
                key={name}
                onClick={() => setCommand(cmd.string)}
                style={{
                  padding: "var(--space-xs) var(--space-sm)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--bg-hover)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                {cmd.label || name}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Results log */}
      {results.length > 0 && (
        <div
          style={{
            background: "var(--bg-base)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            maxHeight: 300,
            overflow: "auto",
          }}
        >
          {results.map((r, i) => (
            <div
              key={i}
              style={{
                padding: "var(--space-sm) var(--space-md)",
                borderBottom:
                  i < results.length - 1
                    ? "1px solid var(--border-color)"
                    : "none",
                fontFamily: "var(--font-mono)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              <div style={{ color: "var(--text-secondary)" }}>
                {">"} {r.cmd}
              </div>
              {r.response && (
                <div style={{ color: "var(--color-success, #4caf50)" }}>
                  {"<"} {r.response}
                </div>
              )}
              {r.error && (
                <div style={{ color: "var(--color-error)" }}>
                  Error: {r.error}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
