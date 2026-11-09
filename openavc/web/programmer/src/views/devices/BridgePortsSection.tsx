/**
 * A bridge's page lists its ports: each one, what is bound to it (by the
 * connections table, `bridgePorts.ts`), and for an IR port the IR tools
 * (`BridgeIrPort`). With a link to the unit's own web interface at its
 * connection host. Drawn only for a device whose driver advertises ports.
 */
import type { BridgePort, DeviceConfig } from "../../api/types";
import { BridgeIrPort } from "./BridgeIrPort";
import { devicesOnBridgePort } from "./bridgePorts";
import { sectionStyle, sectionTitleStyle } from "./deviceSectionStyles";

export function BridgePortsSection({
  deviceId,
  ports,
  devices,
  connections,
  connected,
  onOpenDevice,
}: {
  deviceId: string;
  ports: BridgePort[];
  devices: DeviceConfig[];
  connections: Record<string, Record<string, unknown>> | undefined;
  connected: boolean;
  onOpenDevice?: (deviceId: string) => void;
}) {
  if (ports.length === 0) return null;
  const host = (connections?.[deviceId]?.host as string | undefined) || "";
  return (
    <div style={sectionStyle}>
      <h3 style={sectionTitleStyle}>Bridge Ports</h3>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
        Other devices connect through this bridge's ports. To bind one, open
        that device's Connection settings and choose "Through a bridge".
      </div>
      {host && (
        <div style={{ marginBottom: "var(--space-sm)", fontSize: "var(--font-size-sm)" }}>
          <a
            href={`http://${host}`}
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: "var(--accent-bg)", textDecoration: "underline" }}
          >
            Open this unit's web interface
          </a>
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
        {ports.map((p) => (
          <PortBox
            key={p.id}
            bridgeId={deviceId}
            port={p}
            bound={devicesOnBridgePort(devices, connections, deviceId, p.id)}
            connected={connected}
            onOpenDevice={onOpenDevice}
          />
        ))}
      </div>
    </div>
  );
}

function PortBox({
  bridgeId,
  port,
  bound,
  connected,
  onOpenDevice,
}: {
  bridgeId: string;
  port: BridgePort;
  bound: DeviceConfig[];
  connected: boolean;
  onOpenDevice?: (deviceId: string) => void;
}) {
  return (
    <div
      style={{
        border: "1px solid var(--border-color)",
        borderRadius: "var(--border-radius)",
        padding: "var(--space-sm) var(--space-md)",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: "var(--font-size-sm)" }}>
          {port.label || port.id}
          <span style={{ marginLeft: 6, fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase" }}>
            {port.kind}
          </span>
        </span>
        <code style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
          {port.id}
        </code>
      </div>
      <div style={{ marginTop: 4, fontSize: 11, color: "var(--text-muted)" }}>
        {bindingText(port, bound)}
      </div>
      {port.kind === "ir" && (
        <BridgeIrPort
          bridgeId={bridgeId}
          portId={port.id}
          portLabel={port.label || port.id}
          connected={connected}
          bound={bound}
          onOpenDevice={onOpenDevice}
        />
      )}
    </div>
  );
}

/** What is bound to a port, by name; a serial port with nothing bound says
 *  the TCP port that passes straight through to it. */
function bindingText(port: BridgePort, bound: DeviceConfig[]): string {
  if (bound.length > 0) return `Bound: ${bound.map((d) => d.name || d.id).join(", ")}`;
  const passthrough =
    port.kind === "serial" && port.passthrough_port ? ` (pass-through TCP ${port.passthrough_port})` : "";
  return `Nothing bound${passthrough}`;
}
