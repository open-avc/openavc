import type { DeviceAddress, DeviceConnectionTest } from "../../api/types";

// A device's address as a person reads it: "10.0.0.5:4000", "COM3", or
// "10.0.0.7:4999 through Rack iTach" for a device reached through a bridge.
// Empty when the device has no address yet.
export function formatDeviceAddress(
  address: DeviceAddress | undefined,
  bridgeName: (bridgeId: string) => string = (id) => id,
): string {
  if (!address) return "";
  const { transport, host, port, bridge } = address;
  let text: string;
  if (transport === "serial") {
    text = port == null ? "" : String(port);
  } else if (host) {
    text = port == null ? host : `${host}:${port}`;
  } else {
    text = "";
  }
  if (text && bridge) text += ` through ${bridgeName(bridge)}`;
  return text;
}

// The line Test Connection shows. A failure is the server's sentence as it
// is: it names the address when the address is what failed.
export function connectionTestText(
  result: DeviceConnectionTest,
  bridgeName: (bridgeId: string) => string = (id) => id,
): string {
  if (!result.success) return result.error || "The connection test failed.";
  const address = formatDeviceAddress(result.connection, bridgeName);
  return address
    ? `Connected to ${address} (${result.latency_ms} ms)`
    : `Connected (${result.latency_ms} ms)`;
}
