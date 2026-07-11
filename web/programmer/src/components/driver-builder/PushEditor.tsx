import type { DriverDefinition, DriverPushDef } from "../../api/types";

interface PushEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

/**
 * Edits the `push` block — device-initiated notifications delivered on a
 * channel the platform opens (`type: multicast` joins the device's
 * notification group; `type: sse` holds an event-stream request open on
 * the driver's HTTP session). Frames arriving on the channel feed the same
 * `responses` rules as the control connection, so state updates land
 * instantly instead of waiting for the next poll.
 */
export function PushEditor({ draft, onUpdate }: PushEditorProps) {
  const push = draft.push;
  const enabled = !!push;
  const isHttp = draft.transport === "http";
  const type = push?.type ?? (isHttp ? "sse" : "multicast");

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

  const setEnabled = (next: boolean) => {
    if (next) {
      onUpdate({
        push: isHttp
          ? { type: "sse", path: "" }
          : { type: "multicast", group: "", port: 17000 },
      });
    } else {
      onUpdate({ push: undefined });
    }
  };

  const update = (partial: Partial<DriverPushDef>) => {
    onUpdate({ push: { ...(push ?? {}), ...partial } });
  };

  const setType = (next: string) => {
    // Per-type keys: swap the field set rather than accumulate keys the
    // loader would reject as unknown for the new type.
    if (next === "sse") {
      onUpdate({ push: { type: "sse", path: "" } });
    } else {
      onUpdate({ push: { type: "multicast", group: "", port: 17000 } });
    }
  };

  // The path field edits one path per line; a single path stays a string
  // so simple drivers round-trip without a list wrapper.
  const pathText = Array.isArray(push?.path)
    ? push.path.join("\n")
    : (push?.path ?? "");
  const setPathText = (raw: string) => {
    const lines = raw.split("\n");
    const paths = lines.map((l) => l.trim()).filter((l) => l !== "");
    update({
      path: lines.length > 1 ? paths : (paths[0] ?? ""),
    });
  };

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
        For devices that announce state changes on a separate notification
        channel instead of answering polls. Multicast joins the group the
        device sends to; SSE (Server-Sent Events) holds an event-stream
        request open on the device&apos;s HTTP API. Every frame or event that
        arrives feeds the driver&apos;s response rules — so state updates land
        instantly, without waiting for the next poll. Values can reference
        config fields, which lets one driver match devices whose notification
        target is configurable.
      </p>

      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          fontSize: "var(--font-size-sm)",
          marginBottom: "var(--space-md)",
        }}
      >
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
        />
        Enable push notifications
      </label>

      {enabled && (
        <div
          style={{
            display: "grid",
            gap: "var(--space-md)",
            padding: "var(--space-md)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-surface)",
          }}
        >
          <div>
            <label style={labelStyle}>Type</label>
            <select
              value={type}
              onChange={(e) => setType(e.target.value)}
              style={{ width: "100%" }}
            >
              <option value="multicast">Multicast</option>
              <option value="sse">SSE (Server-Sent Events)</option>
            </select>
            <div style={helpStyle}>
              {type === "sse"
                ? "The driver holds a GET request open with Accept: text/event-stream on its HTTP session, and the device streams updates back. Requires the HTTP transport."
                : "The device sends state-change frames to a multicast group address OpenAVC joins."}
            </div>
          </div>

          {type === "multicast" && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-md)" }}>
              <div>
                <label style={labelStyle}>Multicast Group</label>
                <input
                  value={push?.group ?? ""}
                  onChange={(e) => update({ group: e.target.value })}
                  placeholder="239.0.0.100 or {config_field}"
                  style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                />
                <div style={helpStyle}>
                  An IPv4 multicast address (224.0.0.0 – 239.255.255.255), or a{" "}
                  <code>{"{config_field}"}</code> template resolved from device
                  config.
                </div>
              </div>
              <div>
                <label style={labelStyle}>Port</label>
                <input
                  value={push?.port === undefined ? "" : String(push.port)}
                  onChange={(e) => {
                    const raw = e.target.value;
                    update({
                      port: /^\d+$/.test(raw.trim())
                        ? parseInt(raw.trim(), 10)
                        : raw,
                    });
                  }}
                  placeholder="17000 or {config_field}"
                  style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                />
                <div style={helpStyle}>
                  UDP port the device sends to (1–65535), or a{" "}
                  <code>{"{config_field}"}</code> template.
                </div>
              </div>
            </div>
          )}

          {type === "sse" && (
            <>
              <div>
                <label style={labelStyle}>Event-Stream Path(s)</label>
                <textarea
                  value={pathText}
                  onChange={(e) => setPathText(e.target.value)}
                  placeholder={"/v2/configuration/system/status"}
                  rows={Math.max(2, pathText.split("\n").length)}
                  style={{
                    width: "100%",
                    fontFamily: "var(--font-mono)",
                    resize: "vertical",
                  }}
                />
                <div style={helpStyle}>
                  URL path(s) of the device&apos;s event-stream endpoint(s),
                  one per line. Some devices have a single event feed; others
                  let you subscribe to each resource you also poll.{" "}
                  <code>{"{config_field}"}</code> templates are allowed.
                </div>
              </div>
              <div>
                <label style={labelStyle}>Idle Timeout (s, optional)</label>
                <input
                  value={
                    push?.idle_timeout === undefined
                      ? ""
                      : String(push.idle_timeout)
                  }
                  onChange={(e) => {
                    const raw = e.target.value.trim();
                    if (raw === "") {
                      const next: DriverPushDef = { ...(push ?? {}) };
                      delete next.idle_timeout;
                      onUpdate({ push: next });
                    } else {
                      update({
                        idle_timeout: /^\d+(\.\d+)?$/.test(raw)
                          ? parseFloat(raw)
                          : (raw as unknown as number),
                      });
                    }
                  }}
                  placeholder="e.g. 200"
                  style={{ width: "160px", fontFamily: "var(--font-mono)" }}
                />
                <div style={helpStyle}>
                  Reconnect if the stream is silent for this many seconds. Set
                  it above the device&apos;s keepalive interval so a dead
                  connection is noticed; leave blank to wait indefinitely.
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
