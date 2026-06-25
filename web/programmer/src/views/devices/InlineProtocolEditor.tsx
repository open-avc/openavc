import { useState, useEffect, useMemo, useCallback } from "react";
import {
  Plus,
  Trash2,
  Send,
  Save,
  Check,
  AlertCircle,
  ClipboardPaste,
} from "lucide-react";
import { useProjectStore } from "../../store/projectStore";
import * as api from "../../api/restClient";
import { CopyButton } from "../../components/shared/CopyButton";

// ── Row models (the editable shapes; config is built from these on save) ─────

type CommandRow = {
  key: string;
  name: string;
  label: string;
  send: string;
  method: string;
  path: string;
  body: string;
  poll: boolean;
};

type RespMode = "contains" | "prefix_number" | "prefix_text" | "regex" | "json";

type ResponseRow = {
  key: string;
  mode: RespMode;
  state: string;
  type: string;
  text: string; // contains
  value: string; // contains
  prefix: string; // prefix_number / prefix_text
  pattern: string; // regex
  group: number; // regex
  field: string; // json — the JSON field name (dot path allowed)
};

const HTTP_METHODS = ["GET", "POST", "PUT", "DELETE"];
const VALUE_TYPES = ["string", "integer", "number", "boolean"];

let _keySeq = 0;
const nextKey = () => `row${++_keySeq}`;

function slugify(s: string): string {
  return (
    s
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 48) || "command"
  );
}

// ── Parse saved config → editable rows ───────────────────────────────────────

function parseCommands(raw: unknown): CommandRow[] {
  const obj =
    raw && typeof raw === "object" && !Array.isArray(raw)
      ? (raw as Record<string, unknown>)
      : {};
  return Object.entries(obj).map(([name, def]) => {
    const d =
      def && typeof def === "object"
        ? (def as Record<string, unknown>)
        : { send: String(def ?? "") };
    return {
      key: nextKey(),
      name,
      label: String(d.label ?? name),
      send: String(d.send ?? ""),
      method: String(d.method ?? "GET").toUpperCase(),
      path: String(d.path ?? ""),
      body: String(d.body ?? ""),
      poll: d.poll === true,
    };
  });
}

function inferMode(r: Record<string, unknown>): RespMode {
  if (r.mode) return r.mode as RespMode;
  if ("contains" in r || ("text" in r && !("prefix" in r) && !("after" in r)))
    return "contains";
  if ("after" in r || "prefix" in r)
    return r.number ? "prefix_number" : "prefix_text";
  if ("json" in r || "field" in r) return "json";
  return "regex";
}

function parseResponses(raw: unknown): ResponseRow[] {
  const arr = Array.isArray(raw) ? raw : [];
  return arr
    .filter((r) => r && typeof r === "object")
    .map((raw0) => {
      const r = raw0 as Record<string, unknown>;
      const mode = inferMode(r);
      return {
        key: nextKey(),
        mode,
        state: String(r.state ?? ""),
        type: String(r.type ?? (mode === "prefix_number" ? "number" : "string")),
        text: String(r.text ?? r.contains ?? ""),
        value: String(r.value ?? ""),
        prefix: String(r.prefix ?? r.after ?? ""),
        pattern: String(r.pattern ?? r.match ?? ""),
        group: Number(r.group ?? 1),
        field: String(r.field ?? ""),
      };
    });
}

// ── Build editable rows → config keys ────────────────────────────────────────

function buildCommandsMap(
  rows: CommandRow[],
  isHttp: boolean
): { map: Record<string, unknown>; names: Record<string, string> } {
  const map: Record<string, unknown> = {};
  const names: Record<string, string> = {};
  const used = new Set<string>();
  for (const r of rows) {
    const base = (r.name || slugify(r.label)).trim();
    if (!base) continue;
    let n = base;
    let i = 2;
    while (used.has(n)) n = `${base}_${i++}`;
    used.add(n);
    names[r.key] = n;
    if (isHttp) {
      map[n] = {
        label: r.label || n,
        method: r.method || "GET",
        path: r.path || "/",
        ...(r.body.trim() ? { body: r.body } : {}),
        ...(r.poll ? { poll: true } : {}),
      };
    } else {
      map[n] = { label: r.label || n, send: r.send, ...(r.poll ? { poll: true } : {}) };
    }
  }
  return { map, names };
}

function buildResponses(rows: ResponseRow[]): unknown[] {
  const out: unknown[] = [];
  for (const r of rows) {
    if (!r.state.trim()) continue;
    if (r.mode === "contains") {
      if (!r.text.trim()) continue;
      out.push({
        mode: "contains",
        text: r.text,
        state: r.state,
        value: r.value,
        type: r.type || "string",
      });
    } else if (r.mode === "prefix_number") {
      if (!r.prefix.trim()) continue;
      out.push({
        mode: "prefix_number",
        prefix: r.prefix,
        state: r.state,
        type: r.type || "number",
      });
    } else if (r.mode === "prefix_text") {
      if (!r.prefix.trim()) continue;
      out.push({
        mode: "prefix_text",
        prefix: r.prefix,
        state: r.state,
        type: "string",
      });
    } else if (r.mode === "json") {
      if (!r.field.trim()) continue;
      out.push({
        mode: "json",
        field: r.field,
        state: r.state,
        type: r.type || "string",
      });
    } else {
      if (!r.pattern.trim()) continue;
      out.push({
        mode: "regex",
        pattern: r.pattern,
        group: r.group ?? 1,
        state: r.state,
        type: r.type || "string",
      });
    }
  }
  return out;
}

// ── Shared styles ────────────────────────────────────────────────────────────

const inputStyle: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-sm)",
  fontSize: "var(--font-size-sm)",
  width: "100%",
  boxSizing: "border-box",
};
const cellLabel: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  marginBottom: 2,
};
const iconBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  padding: "var(--space-xs) var(--space-sm)",
  background: "var(--bg-hover)",
  color: "var(--text-secondary)",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  cursor: "pointer",
  fontSize: "var(--font-size-sm)",
};
const card: React.CSSProperties = {
  background: "var(--bg-surface)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  padding: "var(--space-md)",
  marginBottom: "var(--space-md)",
};

export function InlineProtocolEditor({
  deviceId,
  driverInfo,
  connected,
  onSaved,
}: {
  deviceId: string;
  driverInfo: Record<string, unknown> | undefined;
  connected: boolean;
  onSaved: () => void;
}) {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const deviceConfig = project?.devices.find((d) => d.id === deviceId);
  const savedConfig = useMemo(
    () => (deviceConfig?.config ?? {}) as Record<string, unknown>,
    [deviceConfig]
  );

  const transport = String(driverInfo?.transport ?? "").toLowerCase();
  const isHttp = transport === "http";

  const [commandRows, setCommandRows] = useState<CommandRow[]>([]);
  const [responseRows, setResponseRows] = useState<ResponseRow[]>([]);
  const [delimiter, setDelimiter] = useState<string>("\r\n");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [showPaste, setShowPaste] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [rawText, setRawText] = useState("");
  const [rawSending, setRawSending] = useState(false);
  const [rawResult, setRawResult] = useState<string | null>(null);

  // (Re)load editable rows from saved config when the device changes. Keyed on
  // deviceId only so live-state updates elsewhere never clobber in-progress edits.
  useEffect(() => {
    const cfg = (deviceConfig?.config ?? {}) as Record<string, unknown>;
    setCommandRows(parseCommands(cfg.commands));
    setResponseRows(parseResponses(cfg.responses));
    // Default the displayed line ending to the transport's convention (CR for
    // serial, CRLF otherwise) until the device has a saved delimiter.
    setDelimiter(
      typeof cfg.delimiter === "string"
        ? cfg.delimiter
        : transport === "serial"
          ? "\r"
          : "\r\n"
    );
    setDirty(false);
    setSaveError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId]);

  const touch = useCallback(() => {
    setDirty(true);
    setSaved(false);
  }, []);

  // ── Command row ops ────────────────────────────────────────────────────────
  const addCommand = () => {
    setCommandRows((r) => [
      ...r,
      {
        key: nextKey(),
        name: "",
        label: "",
        send: "",
        method: "GET",
        path: "",
        body: "",
        poll: false,
      },
    ]);
    touch();
  };
  const setCommand = (key: string, patch: Partial<CommandRow>) => {
    setCommandRows((rows) =>
      rows.map((r) => (r.key === key ? { ...r, ...patch } : r))
    );
    touch();
  };
  const removeCommand = (key: string) => {
    setCommandRows((rows) => rows.filter((r) => r.key !== key));
    touch();
  };

  // ── Response row ops ───────────────────────────────────────────────────────
  const addResponse = () => {
    setResponseRows((r) => [
      ...r,
      {
        key: nextKey(),
        mode: "contains",
        state: "",
        type: "string",
        text: "",
        value: "",
        prefix: "",
        pattern: "",
        group: 1,
        field: "",
      },
    ]);
    touch();
  };
  const setResponse = (key: string, patch: Partial<ResponseRow>) => {
    setResponseRows((rows) =>
      rows.map((r) => (r.key === key ? { ...r, ...patch } : r))
    );
    touch();
  };
  const removeResponse = (key: string) => {
    setResponseRows((rows) => rows.filter((r) => r.key !== key));
    touch();
  };

  // ── Paste import: "Label = send" lines (or "Label, send" CSV) ───────────────
  const importPaste = () => {
    const added: CommandRow[] = [];
    for (const line of pasteText.split(/\r?\n/)) {
      const t = line.trim();
      if (!t) continue;
      const sep = t.includes("=") ? "=" : t.includes(",") ? "," : null;
      if (!sep) continue;
      const idx = t.indexOf(sep);
      const label = t.slice(0, idx).trim();
      const send = t.slice(idx + 1).trim();
      if (!label || !send) continue;
      added.push({
        key: nextKey(),
        name: slugify(label),
        label,
        send,
        method: "GET",
        path: "",
        body: "",
        poll: false,
      });
    }
    if (added.length) {
      setCommandRows((r) => [...r, ...added]);
      touch();
    }
    setPasteText("");
    setShowPaste(false);
  };

  // ── Send raw (one-off) ─────────────────────────────────────────────────────
  const sendRaw = async () => {
    if (!rawText.trim()) return;
    setRawSending(true);
    setRawResult(null);
    try {
      await api.sendRaw(deviceId, rawText);
      setRawResult("Sent");
    } catch (e) {
      setRawResult(String(e));
    } finally {
      setRawSending(false);
      setTimeout(() => setRawResult(null), 2500);
    }
  };

  // ── Save ───────────────────────────────────────────────────────────────────
  const handleSave = async () => {
    const { map } = buildCommandsMap(commandRows, isHttp);
    const responses = buildResponses(responseRows);
    const newConfig: Record<string, unknown> = {
      ...savedConfig,
      commands: map,
      responses,
    };
    if (!isHttp) newConfig.delimiter = delimiter;

    setSaving(true);
    setSaveError(null);
    try {
      await api.updateDevice(deviceId, { config: newConfig });
      // Mirror into the project store so the device config stays consistent
      // (config is protocol-only; the PUT already split + persisted).
      const cur = useProjectStore.getState().project;
      if (cur) {
        update({
          devices: cur.devices.map((d) =>
            d.id === deviceId ? { ...d, config: newConfig } : d
          ),
        });
      }
      setDirty(false);
      setSaved(true);
      onSaved(); // refetch device info → Send Command card + Live State update
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      setSaveError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const sectionTitleStyle: React.CSSProperties = {
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
    marginBottom: "var(--space-md)",
    fontWeight: 600,
  };

  return (
    <div style={{ marginBottom: "var(--space-xl)" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "var(--space-md)",
        }}
      >
        <h3 style={{ ...sectionTitleStyle, marginBottom: 0 }}>
          Commands &amp; Responses
        </h3>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
          {saveError && (
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                color: "var(--danger, #d9534f)",
                fontSize: 12,
              }}
            >
              <AlertCircle size={13} /> {saveError}
            </span>
          )}
          {saved && !dirty && (
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                color: "var(--success, #5cb85c)",
                fontSize: 12,
              }}
            >
              <Check size={13} /> Saved
            </span>
          )}
          <button
            onClick={handleSave}
            disabled={!dirty || saving}
            style={{
              ...iconBtn,
              background: dirty ? "var(--accent-bg)" : "var(--bg-hover)",
              color: dirty ? "var(--text-on-accent)" : "var(--text-muted)",
              opacity: saving ? 0.6 : 1,
            }}
            data-testid="inline-protocol-save"
          >
            <Save size={14} /> {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 0, marginBottom: "var(--space-md)" }}>
        Define this device's commands and how its replies map to state — no driver
        file needed. {isHttp
          ? "Commands are HTTP requests (method, path, body)."
          : "The line ending below is added to every command, so you don't type it on each row."}
      </p>

      {/* Commands */}
      <div style={card}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: "var(--space-sm)",
          }}
        >
          <strong style={{ fontSize: "var(--font-size-sm)" }}>Commands</strong>
          <div style={{ display: "flex", gap: "var(--space-sm)" }}>
            {!isHttp && (
              <button
                onClick={() => setShowPaste((s) => !s)}
                style={iconBtn}
                title="Paste a list of commands"
              >
                <ClipboardPaste size={13} /> Paste
              </button>
            )}
            <button onClick={addCommand} style={iconBtn}>
              <Plus size={13} /> Add command
            </button>
          </div>
        </div>

        {!isHttp && showPaste && (
          <div style={{ marginBottom: "var(--space-sm)" }}>
            <textarea
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
              placeholder={"Power On = PWR ON\nPower Off = PWR OFF\nMute = MUTE 1"}
              rows={4}
              style={{ ...inputStyle, fontFamily: "var(--font-mono)", resize: "vertical" }}
            />
            <div style={{ display: "flex", gap: "var(--space-sm)", marginTop: 4 }}>
              <button onClick={importPaste} style={{ ...iconBtn, background: "var(--accent-bg)", color: "var(--text-on-accent)" }}>
                Add to table
              </button>
              <span style={{ fontSize: 11, color: "var(--text-muted)", alignSelf: "center" }}>
                One per line: <code>Label = string to send</code>
              </span>
            </div>
          </div>
        )}

        {commandRows.length === 0 ? (
          <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)", padding: "var(--space-sm) 0" }}>
            No commands yet. Add one, or paste a list.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
            {commandRows.map((r) => (
              <div
                key={r.key}
                style={{
                  display: "grid",
                  gridTemplateColumns: isHttp
                    ? "1.2fr 0.8fr 1.5fr 1.5fr auto auto"
                    : "1fr 1.4fr 1.6fr auto auto",
                  gap: "var(--space-sm)",
                  alignItems: "end",
                }}
              >
                <div>
                  <div style={cellLabel}>Label</div>
                  <input
                    value={r.label}
                    onChange={(e) => setCommand(r.key, { label: e.target.value })}
                    onBlur={() => {
                      if (!r.name && r.label) setCommand(r.key, { name: slugify(r.label) });
                    }}
                    placeholder="Power On"
                    style={inputStyle}
                  />
                </div>
                {isHttp ? (
                  <>
                    <div>
                      <div style={cellLabel}>Method</div>
                      <select
                        value={r.method}
                        onChange={(e) => setCommand(r.key, { method: e.target.value })}
                        style={inputStyle}
                      >
                        {HTTP_METHODS.map((m) => (
                          <option key={m} value={m}>
                            {m}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <div style={cellLabel}>Path</div>
                      <input
                        value={r.path}
                        onChange={(e) => setCommand(r.key, { path: e.target.value })}
                        placeholder="/api/power"
                        style={{ ...inputStyle, fontFamily: "var(--font-mono)" }}
                      />
                    </div>
                    <div>
                      <div style={cellLabel}>Body (optional)</div>
                      <input
                        value={r.body}
                        onChange={(e) => setCommand(r.key, { body: e.target.value })}
                        placeholder={'{"power":"on"}'}
                        style={{ ...inputStyle, fontFamily: "var(--font-mono)" }}
                      />
                    </div>
                  </>
                ) : (
                  <>
                    <div>
                      <div style={cellLabel}>
                        ID{" "}
                        {r.name && (
                          <CopyButton value={r.name} size={10} title="Copy command id (for macros / scripts)" />
                        )}
                      </div>
                      <input
                        value={r.name}
                        onChange={(e) => setCommand(r.key, { name: slugify(e.target.value) })}
                        placeholder="power_on"
                        style={{ ...inputStyle, fontFamily: "var(--font-mono)" }}
                      />
                    </div>
                    <div>
                      <div style={cellLabel}>Send</div>
                      <input
                        value={r.send}
                        onChange={(e) => setCommand(r.key, { send: e.target.value })}
                        placeholder="PWR ON   (use {level} for a value)"
                        title={
                          "Sent as text; the line ending is added automatically. " +
                          "Use {name} for a value to fill in when sending. For a raw " +
                          "byte use \\xHH (e.g. \\x1B for ESC); \\r \\n \\t also work."
                        }
                        style={{ ...inputStyle, fontFamily: "var(--font-mono)" }}
                      />
                    </div>
                  </>
                )}
                <div style={{ textAlign: "center" }}>
                  <div style={cellLabel}>Poll</div>
                  <input
                    type="checkbox"
                    checked={r.poll}
                    onChange={(e) => setCommand(r.key, { poll: e.target.checked })}
                    title="Send this command repeatedly on the device's poll interval (for status queries)"
                    style={{ marginBottom: 8 }}
                  />
                </div>
                <button
                  onClick={() => removeCommand(r.key)}
                  style={{ ...iconBtn, background: "transparent", border: "none", color: "var(--text-muted)" }}
                  title="Remove command"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}

        {commandRows.some((r) => r.poll) && (
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: "var(--space-sm)" }}>
            Polled commands send on the device's <strong>Poll Interval</strong> — set it
            when you add or edit the device (next to the connection settings). If it's 0,
            polling is off.
          </div>
        )}

        {!isHttp && (
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", marginTop: "var(--space-md)" }}>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Line ending</span>
            <select
              value={delimiter}
              onChange={(e) => {
                setDelimiter(e.target.value);
                touch();
              }}
              style={{ ...inputStyle, width: "auto" }}
            >
              <option value={"\r\n"}>CRLF (\r\n)</option>
              <option value={"\r"}>CR (\r)</option>
              <option value={"\n"}>LF (\n)</option>
              <option value={""}>None</option>
            </select>
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              added to each command and used to split replies
            </span>
          </div>
        )}

        {!isHttp && (
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: "var(--space-sm)" }}>
            Commands are sent as text. To include a raw byte, use{" "}
            <code>{"\\xHH"}</code> (e.g. <code>{"\\x1B"}</code> for ESC, <code>{"\\xFF"}</code>{" "}
            for 0xFF); <code>{"\\r \\n \\t"}</code> also work. (For protocols that need a
            computed checksum or CRC, build a driver instead.)
          </div>
        )}
      </div>

      {/* Responses */}
      <div style={card}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: "var(--space-sm)",
          }}
        >
          <strong style={{ fontSize: "var(--font-size-sm)" }}>Responses</strong>
          <button onClick={addResponse} style={iconBtn}>
            <Plus size={13} /> Add response
          </button>
        </div>
        <p style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 0, marginBottom: "var(--space-sm)" }}>
          Turn a reply into a live value. The variable appears on this device and
          is usable in bindings, macros, and triggers as{" "}
          <code>$device.{deviceId}.&lt;name&gt;</code>.
        </p>

        {responseRows.length === 0 ? (
          <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)", padding: "var(--space-sm) 0" }}>
            No responses yet.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
            {responseRows.map((r) => (
              <div
                key={r.key}
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: "var(--space-sm)",
                  alignItems: "end",
                  paddingBottom: "var(--space-sm)",
                  borderBottom: "1px solid var(--border-color)",
                }}
              >
                <div style={{ minWidth: 150 }}>
                  <div style={cellLabel}>When a reply…</div>
                  <select
                    value={r.mode}
                    onChange={(e) => setResponse(r.key, { mode: e.target.value as RespMode })}
                    style={inputStyle}
                  >
                    <option value="contains">contains text</option>
                    <option value="prefix_number">has a number after</option>
                    <option value="prefix_text">has text after</option>
                    <option value="json">has a JSON field</option>
                    <option value="regex">matches (advanced)</option>
                  </select>
                </div>

                {r.mode === "contains" && (
                  <div style={{ flex: "1 1 140px" }}>
                    <div style={cellLabel}>this text</div>
                    <input
                      value={r.text}
                      onChange={(e) => setResponse(r.key, { text: e.target.value })}
                      placeholder="PWR ON"
                      style={{ ...inputStyle, fontFamily: "var(--font-mono)" }}
                    />
                  </div>
                )}
                {(r.mode === "prefix_number" || r.mode === "prefix_text") && (
                  <div style={{ flex: "1 1 140px" }}>
                    <div style={cellLabel}>after this prefix</div>
                    <input
                      value={r.prefix}
                      onChange={(e) => setResponse(r.key, { prefix: e.target.value })}
                      placeholder="VOL="
                      style={{ ...inputStyle, fontFamily: "var(--font-mono)" }}
                    />
                  </div>
                )}
                {r.mode === "regex" && (
                  <>
                    <div style={{ flex: "1 1 160px" }}>
                      <div style={cellLabel}>this pattern (regex)</div>
                      <input
                        value={r.pattern}
                        onChange={(e) => setResponse(r.key, { pattern: e.target.value })}
                        placeholder="PWR (ON|OFF)"
                        style={{ ...inputStyle, fontFamily: "var(--font-mono)" }}
                      />
                    </div>
                    <div style={{ width: 64 }}>
                      <div style={cellLabel}>group</div>
                      <input
                        type="number"
                        min={0}
                        value={r.group}
                        onChange={(e) => setResponse(r.key, { group: Number(e.target.value) })}
                        style={inputStyle}
                      />
                    </div>
                  </>
                )}
                {r.mode === "json" && (
                  <div style={{ flex: "1 1 160px" }}>
                    <div style={cellLabel}>JSON field</div>
                    <input
                      value={r.field}
                      onChange={(e) => setResponse(r.key, { field: e.target.value })}
                      placeholder="status.power"
                      style={{ ...inputStyle, fontFamily: "var(--font-mono)" }}
                    />
                  </div>
                )}

                <div style={{ width: 16, textAlign: "center", color: "var(--text-muted)", paddingBottom: 6 }}>
                  →
                </div>
                <div style={{ flex: "1 1 120px" }}>
                  <div style={cellLabel}>set variable</div>
                  <input
                    value={r.state}
                    onChange={(e) => setResponse(r.key, { state: e.target.value })}
                    placeholder="power"
                    style={{ ...inputStyle, fontFamily: "var(--font-mono)" }}
                  />
                </div>
                {r.mode === "contains" && (
                  <div style={{ flex: "1 1 100px" }}>
                    <div style={cellLabel}>to value</div>
                    <input
                      value={r.value}
                      onChange={(e) => setResponse(r.key, { value: e.target.value })}
                      placeholder="on"
                      style={inputStyle}
                    />
                  </div>
                )}
                {r.mode !== "prefix_text" && (
                  <div style={{ width: 110 }}>
                    <div style={cellLabel}>type</div>
                    <select
                      value={r.type}
                      onChange={(e) => setResponse(r.key, { type: e.target.value })}
                      style={inputStyle}
                    >
                      {VALUE_TYPES.map((t) => (
                        <option key={t} value={t}>
                          {t}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                <button
                  onClick={() => removeResponse(r.key)}
                  style={{ ...iconBtn, background: "transparent", border: "none", color: "var(--text-muted)" }}
                  title="Remove response"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Send raw (byte-stream only) */}
      {!isHttp && (
        <div style={card}>
          <strong style={{ fontSize: "var(--font-size-sm)", display: "block", marginBottom: "var(--space-sm)" }}>
            Send raw
          </strong>
          <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center" }}>
            <input
              value={rawText}
              onChange={(e) => setRawText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") sendRaw();
              }}
              placeholder="Type a string to send right now (diagnostics)"
              disabled={!connected}
              style={{ ...inputStyle, fontFamily: "var(--font-mono)", flex: 1 }}
            />
            <button
              onClick={sendRaw}
              disabled={!connected || rawSending || !rawText.trim()}
              style={{
                ...iconBtn,
                background: connected ? "var(--accent-bg)" : "var(--bg-hover)",
                color: connected ? "var(--text-on-accent)" : "var(--text-muted)",
              }}
            >
              <Send size={13} /> Send
            </button>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
            {!connected
              ? "Connect the device to send."
              : rawResult ?? "The line ending is appended automatically."}
          </div>
        </div>
      )}
    </div>
  );
}
