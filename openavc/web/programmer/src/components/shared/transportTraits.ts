// What a transport is and can do, read from the driver contract's one table
// (TRANSPORT_TRAITS in types.gen.ts, generated from drivers/spec.py). Ask a
// column here rather than listing transports by hand: a list of its own is
// the copy that misses the next transport.
import { TRANSPORT_TRAITS, type TransportTraits } from "../../api/types";

/** The row for a transport as a driver or a device names it (any case), or
 * undefined for a name that is no transport. */
export function transportTraits(transport: unknown): TransportTraits | undefined {
  const name = String(transport ?? "").toLowerCase();
  return Object.prototype.hasOwnProperty.call(TRANSPORT_TRAITS, name)
    ? TRANSPORT_TRAITS[name]
    : undefined;
}

/** Whether `transport` dials a network address of its own (the device's
 * IP address), as TCP, UDP, OSC, HTTP, SSH, MQTT and SNMP do. */
export function dialsAnAddress(transport: unknown): boolean {
  return transportTraits(transport)?.addressed === true;
}
