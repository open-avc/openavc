/**
 * The Device Log at the foot of a device's page: what went over the wire
 * (DeviceProtocolLog) and how the device's state changed (DeviceStateLog), one
 * at a time, opening on the protocol log.
 */
import { useState } from "react";
import { DeviceProtocolLog } from "./DeviceProtocolLog";
import { DeviceStateLog } from "./DeviceStateLog";

type DeviceLogTab = "protocol" | "state";

export function DeviceLog({ deviceId }: { deviceId: string }) {
  const [tab, setTab] = useState<DeviceLogTab>("protocol");

  return (
    <div style={{ marginBottom: "var(--space-xl)" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          marginBottom: "var(--space-md)",
        }}
      >
        <h3
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            textTransform: "uppercase",
            letterSpacing: "0.5px",
            fontWeight: 600,
            margin: 0,
          }}
        >
          Device Log
        </h3>
        <div style={{ flex: 1 }} />
        <LogTab label="Protocol" active={tab === "protocol"} onClick={() => setTab("protocol")} />
        <LogTab label="State Changes" active={tab === "state"} onClick={() => setTab("state")} />
      </div>
      {tab === "protocol" ? (
        <DeviceProtocolLog deviceId={deviceId} />
      ) : (
        <DeviceStateLog deviceId={deviceId} />
      )}
    </div>
  );
}

function LogTab({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "2px 8px",
        borderRadius: "var(--border-radius)",
        background: active ? "var(--accent-bg)" : "var(--bg-hover)",
        color: active ? "#fff" : "var(--text-secondary)",
        fontSize: 11,
        fontWeight: active ? 600 : 400,
        border: "none",
        cursor: "pointer",
      }}
    >
      {label}
    </button>
  );
}
