import type { DriverDefinition, DriverPushDef } from "../../api/types";

interface PushEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

/**
 * Edits the `push` block — device-initiated notifications delivered on a
 * channel the platform opens (`type: multicast` joins the device's
 * notification group; `type: sse` holds an event-stream request open on
 * the driver's HTTP session; `type: tcp_listener` opens a local port the
 * device dials back to after a registration command; `type: http_listener`
 * accepts the device's own HTTP POSTs on a platform-assigned callback URL).
 * Frames arriving on the channel feed the same `responses` rules as the
 * control connection, so state updates land instantly instead of waiting
 * for the next poll.
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
    } else if (next === "tcp_listener") {
      onUpdate({ push: { type: "tcp_listener", port: 0 } });
    } else if (next === "http_listener") {
      onUpdate({ push: { type: "http_listener" } });
    } else {
      onUpdate({ push: { type: "multicast", group: "", port: 17000 } });
    }
  };

  const commandNames = Object.keys(draft.commands ?? {});

  const setPushCommand = (key: "register" | "unregister", value: string) => {
    const next: DriverPushDef = { ...(push ?? {}) };
    if (value === "") {
      delete next[key];
    } else {
      next[key] = value;
    }
    onUpdate({ push: next });
  };

  const frameType = push?.frame_parser?.type ?? "";
  const setFrameType = (next: string) => {
    const current: DriverPushDef = { ...(push ?? {}) };
    if (next === "") {
      delete current.frame_parser;
    } else if (next === "struct_frame") {
      current.frame_parser = {
        type: "struct_frame",
        header_reserve: 0,
        length_size: 2,
        length_endian: "big",
        length_adjust: 0,
        mid_reserve: 0,
        trailer_reserve: 0,
      };
    } else if (next === "length_prefix") {
      current.frame_parser = {
        type: "length_prefix",
        header_size: 2,
        header_offset: 0,
      };
    } else {
      current.frame_parser = { type: "fixed_length", length: 1 };
    }
    onUpdate({ push: current });
  };

  const updateFrame = (partial: Record<string, unknown>) => {
    update({
      frame_parser: {
        ...(push?.frame_parser ?? { type: frameType || "struct_frame" }),
        ...partial,
      } as DriverPushDef["frame_parser"],
    });
  };

  const frameIntField = (
    key: string,
    label: string,
    help: string,
    fallback: number,
    min = 0,
  ) => (
    <div>
      <label style={labelStyle}>{label}</label>
      <input
        type="number"
        value={(push?.frame_parser?.[key] as number | undefined) ?? fallback}
        onChange={(e) => {
          const n = parseInt(e.target.value, 10);
          updateFrame({ [key]: Number.isFinite(n) ? Math.max(min, n) : fallback });
        }}
        min={min}
        style={{ width: "100%" }}
      />
      <div style={helpStyle}>{help}</div>
    </div>
  );

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
        request open on the device&apos;s HTTP API; TCP Listener opens a local
        port that the device dials back to after a registration command tells
        it where; HTTP Listener accepts the device&apos;s own HTTP POSTs
        (webhooks) on a callback URL OpenAVC assigns. Every frame or event
        that arrives feeds the driver&apos;s
        response rules — so state updates land instantly, without waiting for
        the next poll. Values can reference config fields, which lets one
        driver match devices whose notification target is configurable.
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
              <option value="tcp_listener">TCP Listener (device dials back)</option>
              <option value="http_listener">
                HTTP Listener (device posts to OpenAVC)
              </option>
            </select>
            <div style={helpStyle}>
              {type === "sse"
                ? "The driver holds a GET request open with Accept: text/event-stream on its HTTP session, and the device streams updates back. Requires the HTTP transport."
                : type === "tcp_listener"
                  ? "OpenAVC listens on a local TCP port; a registration command tells the device where to connect, and the device pushes framed notifications to that port."
                  : type === "http_listener"
                    ? "OpenAVC accepts the device's HTTP POSTs on a callback URL it assigns per device. Nothing to configure here — the registration command tells the device where to post."
                    : "The device sends state-change frames to a multicast group address OpenAVC joins."}
            </div>
          </div>

          {type === "http_listener" && (
            <div style={helpStyle}>
              The callback URL is built at connect time from the server
              address the device can reach and the device&apos;s ID. Send it
              to the device in an On Connect registration command — the token{" "}
              <code>{"{push_callback_url}"}</code> substitutes into command
              bodies, paths, and headers. Bodies the device posts back feed
              the response rules whole, exactly like a poll response.
            </div>
          )}

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

          {type === "tcp_listener" && (
            <>
              <div>
                <label style={labelStyle}>Listener Port</label>
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
                  placeholder="0 (automatic), 31004, or {config_field}"
                  style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                />
                <div style={helpStyle}>
                  Local TCP port OpenAVC listens on for the device&apos;s
                  dial-back connections. 0 lets the system pick a free port;
                  a fixed number (or a <code>{"{config_field}"}</code>{" "}
                  template) is easier to allow through a firewall. Reference
                  it as <code>{"{listener_port}"}</code> in the registration
                  command.
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-md)" }}>
                <div>
                  <label style={labelStyle}>Register Command</label>
                  <select
                    value={push?.register ?? ""}
                    onChange={(e) => setPushCommand("register", e.target.value)}
                    style={{ width: "100%" }}
                  >
                    <option value="">(none)</option>
                    {commandNames.map((name) => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                  </select>
                  <div style={helpStyle}>
                    Command that tells the device where to dial back — use{" "}
                    <code>{"{listener_port}"}</code> in its path or send
                    string. Runs when the listener opens, and again on every
                    reconnect.
                  </div>
                </div>
                <div>
                  <label style={labelStyle}>Unregister Command</label>
                  <select
                    value={push?.unregister ?? ""}
                    onChange={(e) =>
                      setPushCommand("unregister", e.target.value)
                    }
                    style={{ width: "100%" }}
                  >
                    <option value="">(none)</option>
                    {commandNames.map((name) => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                  </select>
                  <div style={helpStyle}>
                    Command that cancels the registration. Runs best-effort
                    when the device is disconnected on purpose, freeing the
                    device&apos;s subscriber slot.
                  </div>
                </div>
              </div>

              <div>
                <label style={labelStyle}>Notification Framing</label>
                <select
                  value={frameType}
                  onChange={(e) => setFrameType(e.target.value)}
                  style={{ width: "100%" }}
                >
                  <option value="">None (dispatch raw data)</option>
                  <option value="struct_frame">
                    Struct frame (reserve + length + reserve + payload + reserve)
                  </option>
                  <option value="length_prefix">Length-prefix</option>
                  <option value="fixed_length">Fixed-length</option>
                </select>
                <div style={helpStyle}>
                  How the pushed frames are parsed. Dial-back devices usually
                  wrap each notification in a binary container; struct frame
                  fits the common &quot;reserved header + length field +
                  reserved bytes + payload + reserved trailer&quot; shape.
                </div>
              </div>

              {frameType === "struct_frame" && (
                <>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "var(--space-md)" }}>
                    {frameIntField(
                      "header_reserve",
                      "Header Reserve",
                      "Reserved bytes before the length field.",
                      0,
                    )}
                    {frameIntField(
                      "mid_reserve",
                      "Mid Reserve",
                      "Reserved bytes between the length field and the payload.",
                      0,
                    )}
                    {frameIntField(
                      "trailer_reserve",
                      "Trailer Reserve",
                      "Reserved bytes after the payload.",
                      0,
                    )}
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "var(--space-md)" }}>
                    <div>
                      <label style={labelStyle}>Length Size (bytes)</label>
                      <select
                        value={
                          (push?.frame_parser?.length_size as number | undefined) ?? 2
                        }
                        onChange={(e) =>
                          updateFrame({ length_size: parseInt(e.target.value) })
                        }
                        style={{ width: "100%" }}
                      >
                        <option value={1}>1 (uint8)</option>
                        <option value={2}>2 (uint16)</option>
                        <option value={4}>4 (uint32)</option>
                      </select>
                      <div style={helpStyle}>Size of the length field.</div>
                    </div>
                    {frameIntField(
                      "length_adjust",
                      "Length Adjust",
                      "Added to the length-field value to get the payload byte count (e.g. -8 when the field counts 8 bytes of overhead).",
                      0,
                      -65536,
                    )}
                    <div>
                      <label style={labelStyle}>Length Byte Order</label>
                      <select
                        value={
                          (push?.frame_parser?.length_endian as string | undefined) ??
                          "big"
                        }
                        onChange={(e) =>
                          updateFrame({ length_endian: e.target.value })
                        }
                        style={{ width: "100%" }}
                      >
                        <option value="big">Big-endian</option>
                        <option value="little">Little-endian</option>
                      </select>
                      <div style={helpStyle}>Byte order of the length field.</div>
                    </div>
                  </div>
                </>
              )}

              {frameType === "length_prefix" && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-md)" }}>
                  <div>
                    <label style={labelStyle}>Header Size (bytes)</label>
                    <select
                      value={
                        (push?.frame_parser?.header_size as number | undefined) ?? 2
                      }
                      onChange={(e) =>
                        updateFrame({ header_size: parseInt(e.target.value) })
                      }
                      style={{ width: "100%" }}
                    >
                      <option value={1}>1 (uint8)</option>
                      <option value={2}>2 (uint16)</option>
                      <option value={4}>4 (uint32)</option>
                    </select>
                    <div style={helpStyle}>
                      Bytes that hold the payload length.
                    </div>
                  </div>
                  {frameIntField(
                    "header_offset",
                    "Header Offset",
                    "Added to the decoded length (negative when the length counts the header itself).",
                    0,
                    -65536,
                  )}
                </div>
              )}

              {frameType === "fixed_length" && (
                <div>
                  {frameIntField(
                    "length",
                    "Frame Length (bytes)",
                    "Every pushed frame is exactly this many bytes.",
                    1,
                    1,
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
