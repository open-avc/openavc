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

/**
 * Substitute {placeholder} tokens against the param map for the wire preview.
 * Routes by field presence exactly like the runtime (commandRoute), so the
 * preview shows what configurable.py would actually build — the transport
 * doesn't influence the shape, only whether it sends (commandShapeMismatch).
 */
export function previewWire(
  command: DriverCommandDef,
  paramValues: Record<string, string>,
): string {
  const subst = (template: string): string =>
    template.replace(/\{(\w+)\}/g, (m, key) =>
      paramValues[key] !== undefined && paramValues[key] !== ""
        ? paramValues[key]
        : m,
    );

  const route = commandRoute(command);

  if (route === "osc") {
    const addr = subst(command.address ?? "");
    const args = (command.args ?? [])
      .map((a) => `${a.type}=${subst(a.value)}`)
      .join(", ");
    return args ? `${addr} [${args}]` : addr;
  }

  if (route === "http") {
    const method = (command.method || "GET").toUpperCase();
    const path = subst(command.path ?? "/");
    // The runtime passes query_params to the HTTP client, which appends
    // them to the URL — show them the same way so the previewed request
    // matches what actually goes out.
    const qp = command.query_params
      ? Object.entries(command.query_params)
          .map(([k, v]) => `${k}=${subst(v)}`)
          .join("&")
      : "";
    const pathWithQuery = qp
      ? `${path}${path.includes("?") ? "&" : "?"}${qp}`
      : path;
    const headers = command.headers
      ? Object.entries(command.headers)
          .map(([k, v]) => `${k}: ${subst(v)}`)
          .join("\n")
      : "";
    const body = command.body ? subst(command.body) : "";
    return [`${method} ${pathWithQuery}`, headers, body]
      .filter(Boolean)
      .join("\n");
  }

  return subst(command.send ?? "");
}
