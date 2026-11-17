import type { DriverCommandDef } from "../../api/types";
import type { DriverDraft } from "../../store/driverBuilderStore.helpers";
import { commandRoute, type CommandRoute } from "./validateDriver";

/** The port an http connection dials when its config names none: 443 with
 *  `ssl` on, 80 otherwise. The server's rule is transport/endpoint.py's
 *  implied_port; tests/test_http_endpoint.py holds these two to it. */
export const HTTP_PORT = 80;
export const HTTPS_PORT = 443;

/** The driver's own HTTPS settings, which a raw HTTP request follows: HTTPS
 *  when `ssl` is on, the certificate checked unless `verify_ssl` is false. */
export function driverTls(draft: DriverDraft): { ssl: boolean; verifySsl: boolean } {
  const config = draft.default_config ?? {};
  return { ssl: Boolean(config.ssl), verifySsl: config.verify_ssl !== false };
}

/**
 * What Live Test's port box starts with: the draft's own default port (a
 * device path for serial). With none, an http draft starts where its
 * connection dials anyway (443 with HTTPS on, else 80), so a test goes where
 * the device will; OSC and the other network transports start at a common
 * port for the author to change, since a device of theirs must name one.
 */
export function defaultTestPort(draft: DriverDraft): number | string {
  const transport = draft.transport || "tcp";
  const port = draft.default_config?.port;
  if (transport === "serial") return typeof port === "string" ? port : "";
  if (typeof port === "number") return port;
  if (transport === "http") return driverTls(draft).ssl ? HTTPS_PORT : HTTP_PORT;
  return transport === "osc" ? 8000 : 23;
}

/** The command shape the driver's transport actually sends. */
export function expectedRoute(transport: string): CommandRoute {
  return transport === "osc" ? "osc" : transport === "http" ? "http" : "raw";
}

/**
 * Human message when a command's shape won't send on this transport (the
 * runtime's senders refuse a transport mismatch), else null. The live test
 * panel uses this to block the send and explain why, instead of mirroring
 * the runtime's shape dispatch and failing the same way it would.
 */
export function commandShapeMismatch(
  transport: string,
  command: DriverCommandDef,
): string | null {
  const route = commandRoute(command);
  const expected = expectedRoute(transport);
  if (route === expected) return null;
  // A datagram beside the transport sends on every transport.
  if (route === "udp") return null;
  const tn = (transport || "tcp").toUpperCase();
  if (route === "osc") {
    return `This command has OSC fields (address/args) but the driver transport is ${tn}. The runtime refuses to send it. Remove the OSC fields in Behavior → Commands, or set the transport to OSC.`;
  }
  if (route === "http") {
    return `This command has HTTP fields (method/path) but the driver transport is ${tn}. The runtime refuses to send it. Remove the HTTP fields in Behavior → Commands, or set the transport to HTTP.`;
  }
  return expected === "osc"
    ? "This command has no OSC address, so it can't be sent on the OSC transport. Set the address in Behavior → Commands."
    : "This command has no HTTP method or path, so it can't be sent as an HTTP request. Set them in Behavior → Commands.";
}

// There is deliberately no wire preview here. Building the wire is the
// runtime's job, and doing it a second time in TypeScript is how the preview
// came to disagree with the send about format specs, command_prefix and
// command_suffix framing, escape decoding, and computed send_frame headers.
// The panel asks the server for a dry run instead: the real driver builds the
// command against a transport that records rather than transmits, and the
// panel formats what comes back.
