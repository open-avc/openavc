import { useEffect, useMemo, useRef, useState } from "react";
import {
  Send,
  AlertCircle,
  AlertTriangle,
  ChevronRight,
  Pause,
  Play,
} from "lucide-react";
import type {
  DriverDefinition,
  DriverCommandDef,
  DriverParamDef,
} from "../../api/types";
import * as api from "../../api/restClient";
import type {
  TestCommandResult,
  TestPanelConflict,
} from "../../api/driverClient";

interface LiveTestPanelProps {
  draft: DriverDefinition;
}

interface ResultEntry {
  command: string;
  sent: string | null;
  received: string[];
  state_changes: Record<string, unknown>;
  error: string | null;
  /** Set when the request hit the 2s rate limit (A82). */
  throttled?: boolean;
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
  const isSerial = transport === "serial";
  // Serial transports store a port path (e.g. "COM3", "/dev/ttyUSB0") in
  // default_config.port. IP transports store a numeric port.
  const defaultPort: number | string = isSerial
    ? typeof draft.default_config?.port === "string"
      ? (draft.default_config.port as string)
      : ""
    : typeof draft.default_config?.port === "number"
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
  // A81 — production-device conflict tracking.
  const [conflicts, setConflicts] = useState<TestPanelConflict[]>([]);
  const [pausedDeviceIds, setPausedDeviceIds] = useState<string[]>([]);
  const [conflictAcknowledged, setConflictAcknowledged] = useState(false);
  const [pausingId, setPausingId] = useState<string | null>(null);
  // A82 — 2s rate-limit countdown shown on the Send button.
  const [cooldownUntil, setCooldownUntil] = useState<number | null>(null);
  const [cooldownRemainingMs, setCooldownRemainingMs] = useState(0);

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

  // A81 — pre-flight conflict check. Many AV devices accept only one TCP
  // control session, so before the test panel opens its competing socket
  // we look up which production device (if any) currently owns this
  // host:port and surface a warning so the user can choose to pause it.
  // Debounced to 300ms so rapid typing doesn't spam the endpoint.
  useEffect(() => {
    if (isSerial || transport !== "tcp" || !host.trim() || !port.trim()) {
      setConflicts([]);
      setConflictAcknowledged(false);
      return;
    }
    const handle = setTimeout(async () => {
      try {
        const result = await api.checkConnectionConflict(
          host.trim(),
          port.trim(),
          transport,
        );
        setConflicts(result.conflicts);
        setConflictAcknowledged(false);
      } catch {
        // A failed check shouldn't block testing; just clear any stale
        // conflicts and let the user proceed.
        setConflicts([]);
      }
    }, 300);
    return () => clearTimeout(handle);
  }, [host, port, transport, isSerial]);

  // A82 — rate-limit cooldown ticker. When the server returns 429 we paint
  // a countdown on Send so the user understands the brief disable isn't a
  // device failure.
  useEffect(() => {
    if (cooldownUntil === null) {
      setCooldownRemainingMs(0);
      return;
    }
    const tick = () => {
      const remaining = Math.max(0, cooldownUntil - Date.now());
      setCooldownRemainingMs(remaining);
      if (remaining === 0) {
        setCooldownUntil(null);
      }
    };
    tick();
    const interval = setInterval(tick, 100);
    return () => clearInterval(interval);
  }, [cooldownUntil]);

  // Resume any devices we paused when the panel unmounts. A useRef snapshot
  // keeps the cleanup closure pointing at the current paused-id list instead
  // of the stale value captured at mount time.
  const pausedRef = useRef<string[]>([]);
  useEffect(() => {
    pausedRef.current = pausedDeviceIds;
  }, [pausedDeviceIds]);
  useEffect(
    () => () => {
      for (const id of pausedRef.current) {
        api.resumeDevice(id).catch(() => {});
      }
    },
    [],
  );

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

  const baseCanSend =
    (isSerial ? !!port.trim() : !!host) &&
    (selectedCommand !== RAW_COMMAND
      ? command !== null
      : rawString.trim().length > 0);

  // Unresolved conflict: at least one matching production device that the
  // user hasn't paused or explicitly chosen to override.
  const unpausedConflicts = conflicts.filter(
    (c) => !pausedDeviceIds.includes(c.device_id),
  );
  const hasUnresolvedConflict =
    unpausedConflicts.length > 0 && !conflictAcknowledged;

  const isCooldown = cooldownRemainingMs > 0;
  const canSend = baseCanSend && !hasUnresolvedConflict && !isCooldown;

  // Serial uses the port string as a device path; IP transports need an int.
  const resolvePortForSend = (): number | string => {
    if (isSerial) return port;
    return parseInt(port) || (typeof defaultPort === "number" ? defaultPort : 23);
  };

  const handlePause = async (deviceId: string) => {
    setPausingId(deviceId);
    try {
      await api.pauseDevice(deviceId);
      setPausedDeviceIds((prev) =>
        prev.includes(deviceId) ? prev : [...prev, deviceId],
      );
    } catch {
      // Surface as a result entry so the user sees what went wrong instead
      // of getting silently blocked.
      setResults((prev) => [
        {
          command: `Pause ${deviceId}`,
          sent: null,
          received: [],
          state_changes: {},
          error: "Could not pause production device — try Connect anyway.",
          timestamp: Date.now(),
        },
        ...prev,
      ]);
    } finally {
      setPausingId(null);
    }
  };

  const handleResume = async (deviceId: string) => {
    try {
      await api.resumeDevice(deviceId);
    } catch {
      /* idempotent — drop errors */
    }
    setPausedDeviceIds((prev) => prev.filter((id) => id !== deviceId));
  };

  const handleSend = async () => {
    if (!canSend) return;
    setSending(true);
    try {
      const overrides: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(configOverrides)) {
        if (v !== "") overrides[k] = v;
      }

      const portForSend = resolvePortForSend();

      const data: Parameters<typeof api.testDriverCommand>[1] =
        selectedCommand === RAW_COMMAND
          ? {
              host,
              port: portForSend,
              transport,
              command_string: rawString,
              delimiter: draft.delimiter,
              timeout: 5,
            }
          : {
              host,
              port: portForSend,
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
      const message = e instanceof Error ? e.message : String(e);
      // A82 — distinguish "throttled by our own rate limiter" from
      // protocol or transport errors. Start a visible countdown on Send and
      // tag the result row so the user understands it isn't a device fail.
      const throttled = message.includes("API 429");
      if (throttled) {
        setCooldownUntil(Date.now() + 2000);
      }
      setResults((prev) => [
        {
          command:
            selectedCommand === RAW_COMMAND
              ? rawString
              : command?.label || selectedCommand,
          sent: null,
          received: [],
          state_changes: {},
          error: throttled
            ? "Throttled by test rate limit (2s between sends)."
            : message,
          throttled,
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
      {isSerial ? (
        <div style={{ marginBottom: "var(--space-md)" }}>
          <label style={labelStyle}>Serial Port</label>
          <input
            value={port}
            onChange={(e) => setPort(e.target.value)}
            placeholder="COM3 or /dev/ttyUSB0"
            style={{ width: "100%", fontFamily: "var(--font-mono)" }}
          />
          <div style={helpStyle}>
            Path to the serial device on this host. Prefix with <code>SIM:</code> to
            use the built-in simulator (e.g. <code>SIM:projector</code>).
          </div>
        </div>
      ) : (
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
      )}

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
            {isSerial ? (
              <>
                Bypasses the driver — sent as-is to {port || "the serial port"}.
                Useful for one-off probes; for real testing pick a defined
                command above.
              </>
            ) : (
              <>
                Bypasses the driver — sent as-is to {host || "the device"} on
                port {port || defaultPort}. Useful for one-off probes; for real
                testing pick a defined command above.
              </>
            )}
          </div>
        </div>
      )}

      {/* A81 — production-conflict banner. Shows when host:port matches a
          running device. The user can pause each conflicting device, or
          override and connect anyway (e.g. when they know the production
          device is already down). */}
      {conflicts.length > 0 && (
        <ConflictBanner
          conflicts={conflicts}
          pausedDeviceIds={pausedDeviceIds}
          pausingId={pausingId}
          acknowledged={conflictAcknowledged}
          onPause={handlePause}
          onResume={handleResume}
          onAcknowledge={() => setConflictAcknowledged(true)}
        />
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
            cursor: canSend ? "pointer" : "not-allowed",
          }}
        >
          <Send size={14} />{" "}
          {sending
            ? "Sending..."
            : isCooldown
              ? `Rate limited (${(cooldownRemainingMs / 1000).toFixed(1)}s)`
              : "Send"}
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
        {entry.throttled && (
          <span
            style={{
              fontSize: "10px",
              padding: "1px 6px",
              borderRadius: 8,
              background: "var(--bg-warning, #4a3a1a)",
              color: "var(--color-warning, #e8b250)",
              border: "1px solid var(--color-warning, #e8b250)",
              fontFamily: "var(--font-sans)",
            }}
            title="Blocked by the test panel's 2-second rate limit, not the device."
          >
            Throttled
          </span>
        )}
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
            color: entry.throttled
              ? "var(--color-warning, #e8b250)"
              : "var(--color-error)",
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
 * A81 — warn the user that the host:port they're testing against is already
 * owned by a production device. Many AV devices accept only one TCP control
 * session, so the test would kick the live device offline. The banner offers
 * to pause each conflicting device (cleanly disconnect, suppress auto-
 * reconnect) and then resume them on demand. A "Connect anyway" override is
 * provided for cases where the user knows the device is already gone.
 */
function ConflictBanner({
  conflicts,
  pausedDeviceIds,
  pausingId,
  acknowledged,
  onPause,
  onResume,
  onAcknowledge,
}: {
  conflicts: TestPanelConflict[];
  pausedDeviceIds: string[];
  pausingId: string | null;
  acknowledged: boolean;
  onPause: (deviceId: string) => void;
  onResume: (deviceId: string) => void;
  onAcknowledge: () => void;
}) {
  const allPaused = conflicts.every((c) =>
    pausedDeviceIds.includes(c.device_id),
  );
  const allResolved = allPaused || acknowledged;
  const tone = allResolved
    ? { bg: "var(--bg-info, #1a2a3a)", fg: "var(--color-info, #6aa3d6)" }
    : { bg: "var(--bg-warning, #4a3a1a)", fg: "var(--color-warning, #e8b250)" };
  return (
    <div
      style={{
        marginBottom: "var(--space-md)",
        padding: "var(--space-sm) var(--space-md)",
        border: `1px solid ${tone.fg}`,
        borderRadius: "var(--border-radius)",
        background: tone.bg,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          color: tone.fg,
          fontSize: "var(--font-size-sm)",
          fontWeight: 600,
          marginBottom: "var(--space-xs)",
        }}
      >
        <AlertTriangle size={14} />
        {allPaused
          ? "Production device paused for testing"
          : acknowledged
            ? "Testing over a live production address"
            : "Production device already uses this address"}
      </div>
      <div
        style={{
          fontSize: "11px",
          color: "var(--text-secondary)",
          marginBottom: "var(--space-sm)",
        }}
      >
        {allPaused
          ? "The conflicting device is offline while you test. Resume it when you're done."
          : acknowledged
            ? "You chose to connect anyway. The live device will likely drop while the test panel holds the connection."
            : "Many AV devices accept only one TCP control session at a time. Testing will kick the production driver offline until it reconnects."}
      </div>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-xs)",
        }}
      >
        {conflicts.map((c) => {
          const isPaused = pausedDeviceIds.includes(c.device_id);
          const isPausing = pausingId === c.device_id;
          return (
            <div
              key={c.device_id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-sm)",
              }}
            >
              <div style={{ flex: 1, fontSize: "var(--font-size-sm)" }}>
                <span style={{ color: "var(--text-primary)" }}>
                  {c.device_name}
                </span>{" "}
                <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
                  ({c.device_id}
                  {c.connected
                    ? ", connected"
                    : isPaused
                      ? ", paused"
                      : ", offline"}
                  )
                </span>
              </div>
              {isPaused ? (
                <button
                  onClick={() => onResume(c.device_id)}
                  style={pillButtonStyle}
                  title="Reconnect this device now"
                >
                  <Play size={12} /> Resume
                </button>
              ) : (
                <button
                  onClick={() => onPause(c.device_id)}
                  disabled={isPausing}
                  style={pillButtonStyle}
                  title="Cleanly disconnect this device so the test can run"
                >
                  <Pause size={12} />{" "}
                  {isPausing ? "Pausing..." : "Pause device"}
                </button>
              )}
            </div>
          );
        })}
      </div>
      {!allPaused && !acknowledged && (
        <div
          style={{
            marginTop: "var(--space-sm)",
            display: "flex",
            justifyContent: "flex-end",
          }}
        >
          <button
            onClick={onAcknowledge}
            style={{
              ...pillButtonStyle,
              background: "transparent",
              borderColor: "var(--text-muted)",
              color: "var(--text-muted)",
            }}
            title="Skip the pause and connect anyway (e.g. if the device is already offline)"
          >
            Connect anyway
          </button>
        </div>
      )}
    </div>
  );
}

const pillButtonStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  padding: "2px 10px",
  borderRadius: 12,
  border: "1px solid var(--accent)",
  background: "transparent",
  color: "var(--accent)",
  fontSize: 11,
  cursor: "pointer",
};

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
