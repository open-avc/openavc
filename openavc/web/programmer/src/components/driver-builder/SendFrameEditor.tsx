import type { DriverDefinition } from "../../api/types";

interface SendFrameEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

/**
 * Edits the optional `send_frame` block — the send-side twin of the Frame
 * Parser. Used when a driver's commands ride inside a binary packet header
 * whose data-length field is COMPUTED per message, which a static
 * command_prefix can't express. The canonical case is eISCP (Onkyo/Integra/
 * Pioneer receivers over TCP 60128): a 16-byte header of magic + header-size +
 * a 4-byte data length + version/reserved, wrapping the `!1...<CR>` command.
 *
 * On the wire each command becomes:
 *   header + <computed length> + after_length + (command_prefix + send + command_suffix)
 *
 * Most drivers don't need this — text protocols use Command Framing (prefix /
 * suffix) alone. When you enable it, the fields seed with the eISCP layout as a
 * working starting point.
 */
export function SendFrameEditor({ draft, onUpdate }: SendFrameEditorProps) {
  const sf = draft.send_frame ?? null;
  const enabled = sf !== null;

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
      // Seed with the eISCP layout — the overwhelmingly common use — so the
      // section works out of the box and the author edits from a real example.
      onUpdate({
        send_frame: {
          type: "length_prefix",
          header: "ISCP\\x00\\x00\\x00\\x10",
          length_size: 4,
          length_endian: "big",
          after_length: "\\x01\\x00\\x00\\x00",
        },
      });
    } else {
      onUpdate({ send_frame: null });
    }
  };

  const update = (partial: Record<string, unknown>) => {
    onUpdate({
      send_frame: {
        ...(sf ?? { type: "length_prefix" }),
        ...partial,
      } as DriverDefinition["send_frame"],
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
        For binary protocols where each command is wrapped in a packet header
        carrying a computed data-length (e.g. eISCP over TCP). The header is
        emitted before every command the driver sends, with the length filled in
        per message. If you only need a fixed prefix and terminator, use Command
        Framing above instead and leave this off.
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
        Enable send frame
      </label>

      {enabled && sf && (
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
            <label style={labelStyle}>Header (bytes before the length field)</label>
            <input
              type="text"
              value={(sf.header as string | undefined) ?? ""}
              onChange={(e) => update({ header: e.target.value })}
              placeholder="ISCP\x00\x00\x00\x10"
              style={{ width: "100%", fontFamily: "monospace" }}
            />
            <div style={helpStyle}>
              Constant lead-in bytes (magic + fixed header fields). Literal-escape
              text: <code>\r</code>, <code>\n</code>, <code>\xHH</code>. For
              eISCP: the "ISCP" magic plus the 4-byte header-size (16) =
              <code> ISCP\x00\x00\x00\x10</code>.
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-md)" }}>
            <div>
              <label style={labelStyle}>Length Field Size (bytes)</label>
              <select
                value={(sf.length_size as number | undefined) ?? 4}
                onChange={(e) => update({ length_size: parseInt(e.target.value) })}
                style={{ width: "100%" }}
              >
                <option value={1}>1 (uint8)</option>
                <option value={2}>2 (uint16)</option>
                <option value={4}>4 (uint32)</option>
              </select>
              <div style={helpStyle}>
                Width of the computed data-length field. eISCP uses 4.
              </div>
            </div>
            <div>
              <label style={labelStyle}>Length Byte Order</label>
              <select
                value={(sf.length_endian as string | undefined) ?? "big"}
                onChange={(e) => update({ length_endian: e.target.value })}
                style={{ width: "100%" }}
              >
                <option value="big">Big-endian</option>
                <option value="little">Little-endian</option>
              </select>
              <div style={helpStyle}>
                Byte order of the length field. eISCP is big-endian.
              </div>
            </div>
          </div>

          <div>
            <label style={labelStyle}>After Length (bytes before the data)</label>
            <input
              type="text"
              value={(sf.after_length as string | undefined) ?? ""}
              onChange={(e) => update({ after_length: e.target.value })}
              placeholder="\x01\x00\x00\x00"
              style={{ width: "100%", fontFamily: "monospace" }}
            />
            <div style={helpStyle}>
              Constant bytes after the length field, before the command data.
              For eISCP: the version byte (0x01) + 3 reserved bytes =
              <code> \x01\x00\x00\x00</code>. Leave blank if none.
            </div>
          </div>

          <div style={helpStyle}>
            The data whose length is measured is the framed command
            (command_prefix + send + command_suffix), e.g.{" "}
            <code>!1PWR01\r</code>. Set up a matching Frame Parser to read the
            device's replies (for eISCP: length-prefix, 4-byte length at offset
            8, header extra 4).
          </div>
        </div>
      )}
    </div>
  );
}
