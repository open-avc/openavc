import type { DriverDefinition } from "../../api/types";

interface TransportPickerProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

export function TransportPicker({ draft, onUpdate }: TransportPickerProps) {
  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
  };

  const rowStyle: React.CSSProperties = {
    marginBottom: "var(--space-md)",
  };

  return (
    <div>
      <div style={rowStyle}>
        <label style={labelStyle}>Transport Type</label>
        <select
          value={draft.transport}
          onChange={(e) => onUpdate({ transport: e.target.value })}
          style={{ width: "100%" }}
        >
          <option value="tcp">TCP</option>
          <option value="serial">Serial</option>
        </select>
        <div
          style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            marginTop: "var(--space-xs)",
          }}
        >
          Choose TCP for network devices or Serial for RS-232/RS-485 devices.
        </div>
      </div>

      <div style={rowStyle}>
        <label style={labelStyle}>Message Delimiter</label>
        <input
          value={draft.delimiter}
          onChange={(e) => onUpdate({ delimiter: e.target.value })}
          placeholder="\\r"
          style={{ width: "100%" }}
        />
        <div
          style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            marginTop: "var(--space-xs)",
          }}
        >
          Character(s) that mark the end of a message. Common: \r, \r\n, \n
        </div>
      </div>

      {draft.transport === "tcp" && (
        <>
          <div style={rowStyle}>
            <label style={labelStyle}>Default Port</label>
            <input
              type="number"
              value={
                (draft.default_config.port as number | undefined) ?? 23
              }
              onChange={(e) =>
                onUpdate({
                  default_config: {
                    ...draft.default_config,
                    port: parseInt(e.target.value) || 23,
                  },
                })
              }
              style={{ width: 120 }}
            />
          </div>
        </>
      )}

      {draft.transport === "serial" && (
        <>
          <div style={rowStyle}>
            <label style={labelStyle}>Default Baud Rate</label>
            <select
              value={
                String(
                  (draft.default_config.baudrate as number | undefined) ?? 9600
                )
              }
              onChange={(e) =>
                onUpdate({
                  default_config: {
                    ...draft.default_config,
                    baudrate: parseInt(e.target.value),
                  },
                })
              }
              style={{ width: 160 }}
            >
              {[1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200].map(
                (r) => (
                  <option key={r} value={String(r)}>
                    {r}
                  </option>
                )
              )}
            </select>
          </div>
          <div style={rowStyle}>
            <label style={labelStyle}>Default Parity</label>
            <select
              value={
                (draft.default_config.parity as string | undefined) ?? "N"
              }
              onChange={(e) =>
                onUpdate({
                  default_config: {
                    ...draft.default_config,
                    parity: e.target.value,
                  },
                })
              }
              style={{ width: 120 }}
            >
              <option value="N">None</option>
              <option value="E">Even</option>
              <option value="O">Odd</option>
            </select>
          </div>
        </>
      )}
    </div>
  );
}
