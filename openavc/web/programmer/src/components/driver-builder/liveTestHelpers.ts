import type { DriverCommandDef } from "../../api/types";
import { commandRoute, type CommandRoute } from "./validateDriver";

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
  const tn = (transport || "tcp").toUpperCase();
  if (route === "osc") {
    return `This command has OSC fields (address/args) but the driver transport is ${tn} — the runtime refuses to send it. Remove the OSC fields in Behavior → Commands, or set the transport to OSC.`;
  }
  if (route === "http") {
    return `This command has HTTP fields (method/path) but the driver transport is ${tn} — the runtime refuses to send it. Remove the HTTP fields in Behavior → Commands, or set the transport to HTTP.`;
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
