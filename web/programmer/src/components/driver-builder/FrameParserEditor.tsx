import type { DriverDefinition } from "../../api/types";

interface FrameParserEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

/**
 * Edits the optional `frame_parser` block — used when a driver speaks a
 * binary protocol where messages aren't delimited by a control byte but
 * instead framed by a length prefix or fixed length. Length-prefix is the
 * common shape for industrial/AV binary protocols (Crestron NVX-style,
 * some Biamp variants). Most drivers don't need this — text protocols
 * use the Message Delimiter setting above.
 */
export function FrameParserEditor({ draft, onUpdate }: FrameParserEditorProps) {
  const fp = draft.frame_parser ?? null;
  const enabled = fp !== null;
  const fpType = (fp?.type ?? "length_prefix") as "length_prefix" | "fixed_length";

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
        frame_parser: {
          type: "length_prefix",
          header_size: 2,
          header_offset: 0,
          include_header: false,
        },
      });
    } else {
      onUpdate({ frame_parser: null });
    }
  };

  const update = (partial: Record<string, unknown>) => {
    onUpdate({
      frame_parser: { ...(fp ?? { type: fpType }), ...partial } as DriverDefinition["frame_parser"],
    });
  };

  return (
    <div style={{ marginTop: "var(--space-xl)" }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: "var(--space-md)",
          marginBottom: "var(--space-xs)",
        }}
      >
        <h3 style={{ fontSize: "var(--font-size-md)", margin: 0 }}>
          Frame Parser <span style={{ color: "var(--text-muted)", fontWeight: 400, fontSize: 12 }}>(advanced)</span>
        </h3>
        <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>
          binary protocols only
        </span>
      </div>
      <p
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          marginBottom: "var(--space-md)",
        }}
      >
        For binary protocols where messages aren't delimited by a control
        byte. Text protocols (most AV gear) should use the Message Delimiter
        setting above instead. If you're not sure which you need, leave this
        disabled.
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
        Enable frame parser
      </label>

      {enabled && fp && (
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
            <label style={labelStyle}>Parser Type</label>
            <select
              value={fpType}
              onChange={(e) => {
                const t = e.target.value;
                if (t === "fixed_length") {
                  onUpdate({ frame_parser: { type: "fixed_length", length: 1 } });
                } else {
                  onUpdate({
                    frame_parser: {
                      type: "length_prefix",
                      header_size: 2,
                      header_offset: 0,
                      include_header: false,
                    },
                  });
                }
              }}
              style={{ width: "100%" }}
            >
              <option value="length_prefix">Length-prefix (header → body)</option>
              <option value="fixed_length">Fixed-length (every frame is N bytes)</option>
            </select>
          </div>

          {fpType === "length_prefix" && (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-md)" }}>
                <div>
                  <label style={labelStyle}>Header Size (bytes)</label>
                  <input
                    type="number"
                    value={(fp.header_size as number | undefined) ?? 2}
                    onChange={(e) =>
                      update({ header_size: parseInt(e.target.value) || 2 })
                    }
                    min={1}
                    max={8}
                    style={{ width: "100%" }}
                  />
                  <div style={helpStyle}>
                    Number of bytes that hold the body length, big-endian.
                    Common: 1, 2, or 4.
                  </div>
                </div>
                <div>
                  <label style={labelStyle}>Header Offset (bytes)</label>
                  <input
                    type="number"
                    value={(fp.header_offset as number | undefined) ?? 0}
                    onChange={(e) =>
                      update({ header_offset: parseInt(e.target.value) || 0 })
                    }
                    min={0}
                    max={16}
                    style={{ width: "100%" }}
                  />
                  <div style={helpStyle}>
                    Bytes before the length header (e.g., a sync/magic prefix).
                    Default 0.
                  </div>
                </div>
              </div>
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-sm)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                <input
                  type="checkbox"
                  checked={!!fp.include_header}
                  onChange={(e) =>
                    update({ include_header: e.target.checked })
                  }
                />
                Include header bytes in the parsed frame
              </label>
            </>
          )}

          {fpType === "fixed_length" && (
            <div>
              <label style={labelStyle}>Frame Length (bytes)</label>
              <input
                type="number"
                value={(fp.length as number | undefined) ?? 1}
                onChange={(e) =>
                  update({ length: parseInt(e.target.value) || 1 })
                }
                min={1}
                style={{ width: 160 }}
              />
              <div style={helpStyle}>
                Every frame is exactly this many bytes. The parser hands one
                frame at a time to the response dispatcher.
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
