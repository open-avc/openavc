import type {
  AdapterIdentity,
  ListenMode,
  NetworkInterface,
} from "../../api/systemClient";

/** Settings > Network: the two adapter pickers, Accept connections from and
 *  Reach devices through.
 *
 *  Both list adapters by name and current address and store the adapter's
 *  identity (`{name, mac}`), never its address, so a DHCP renewal does not
 *  break the choice. The server resolves an identity MAC first, then name
 *  (`openavc/listen.py` `match_adapter`); `matchInterface` below asks the same
 *  question so the picker shows the adapter the server will use.
 */

export const LABEL_ANY = "Any network";
export const LABEL_THIS_COMPUTER = "This computer only";
export const LABEL_EVERY_ADAPTER = "Every connected adapter";

/** A MAC with case and separators removed: Windows' `50-EB-F6-...` and
 *  Linux's `50:eb:f6:...` compare equal. */
export function normalizeMac(mac: string | undefined): string {
  return (mac ?? "").toLowerCase().replace(/[^0-9a-f]/g, "");
}

/** The adapter an identity names, as the server finds it: MAC first, then
 *  name; an address-only identity (from the environment) matches the adapter
 *  holding that address. */
export function matchInterface(
  identity: AdapterIdentity | null | undefined,
  interfaces: NetworkInterface[],
): NetworkInterface | undefined {
  if (!identity) return undefined;
  const mac = normalizeMac(identity.mac);
  if (mac) {
    const byMac = interfaces.find((i) => normalizeMac(i.mac) === mac);
    if (byMac) return byMac;
  }
  const name = (identity.name ?? "").trim().toLowerCase();
  if (name) {
    const byName = interfaces.find((i) => i.name.toLowerCase() === name);
    if (byName) return byName;
  }
  if (identity.address && !identity.mac && !identity.name) {
    return interfaces.find((i) => i.addresses.some((a) => a.ip === identity.address));
  }
  return undefined;
}

/** The identity Settings stores for an adapter. */
export function identityOf(iface: NetworkInterface): AdapterIdentity {
  return { name: iface.name, mac: iface.mac };
}

/** Whether the adapter can be reached through now. */
export function isConnected(iface: NetworkInterface): boolean {
  return iface.link !== false && iface.addresses.length > 0;
}

/** One adapter as a list entry: "Ethernet · 10.20.30.5 · 10.20.30.0/24". */
export function interfaceLabel(iface: NetworkInterface): string {
  if (iface.link === false) return `${iface.name} · not connected`;
  if (iface.addresses.length === 0) return `${iface.name} · no address yet`;
  const ips = iface.addresses.map((a) => a.ip).join(", ");
  const subnets = [...new Set(iface.addresses.map((a) => a.subnet).filter(Boolean))].join(", ");
  return subnets ? `${iface.name} · ${ips} · ${subnets}` : `${iface.name} · ${ips}`;
}

export interface AdapterOption {
  /** The `<option>` value: "adapter:" plus a stable key for the identity. */
  value: string;
  label: string;
  identity: AdapterIdentity;
}

function identityKey(identity: AdapterIdentity): string {
  const mac = normalizeMac(identity.mac);
  if (mac) return `adapter:mac:${mac}`;
  if (identity.name) return `adapter:name:${identity.name.toLowerCase()}`;
  return `adapter:address:${identity.address ?? ""}`;
}

/** The adapters to list, and the chosen one when it is no longer present, so
 *  a choice already made stays visible ("Wi-Fi · not connected") instead of
 *  the list silently showing something else. `chosenName` is what the server
 *  last called it, for a chosen adapter this computer no longer has. */
export function adapterOptions(
  interfaces: NetworkInterface[],
  chosen: AdapterIdentity | null | undefined,
  chosenName = "",
): AdapterOption[] {
  const options = interfaces.map((iface) => ({
    value: identityKey(identityOf(iface)),
    label: interfaceLabel(iface),
    identity: identityOf(iface),
  }));
  if (chosen && Object.keys(chosen).length > 0 && !matchInterface(chosen, interfaces)) {
    const name = chosen.name || chosenName || chosen.address || "The chosen adapter";
    const label = chosen.address && !chosen.name && !chosen.mac
      ? `${chosen.address} · not on this computer`
      : `${name} · not connected`;
    options.push({ value: identityKey(chosen), label, identity: chosen });
  }
  return options;
}

/** The option value for a stored identity: the listed adapter it matches, so
 *  a stored `{name, mac}` selects the same entry whatever its spelling. */
export function valueForIdentity(
  identity: AdapterIdentity | null | undefined,
  interfaces: NetworkInterface[],
): string {
  if (!identity || Object.keys(identity).length === 0) return "";
  const match = matchInterface(identity, interfaces);
  return identityKey(match ? identityOf(match) : identity);
}

/** The adapter's name for a sentence: the listed one, else what the server
 *  last called it. */
export function adapterName(
  identity: AdapterIdentity | null | undefined,
  interfaces: NetworkInterface[],
  serverName = "",
): string {
  return matchInterface(identity, interfaces)?.name || identity?.name || serverName || identity?.address || "";
}

// ---------------------------------------------------------------------------
// Accept connections from
// ---------------------------------------------------------------------------

export const LISTEN_ANY = "any";
export const LISTEN_THIS_COMPUTER = "this_computer";

/** The `<select>` value for a listen choice. */
export function listenValue(
  mode: ListenMode | "" | undefined,
  identity: AdapterIdentity | null | undefined,
  interfaces: NetworkInterface[],
): string {
  if (mode === "adapter") return valueForIdentity(identity, interfaces);
  return mode === "any" ? LISTEN_ANY : LISTEN_THIS_COMPUTER;
}

/** A `<select>` value back to the choice it stands for. */
export function listenChoiceFor(
  value: string,
  options: AdapterOption[],
): { mode: ListenMode; adapter: AdapterIdentity } {
  if (value === LISTEN_ANY) return { mode: "any", adapter: {} };
  if (value === LISTEN_THIS_COMPUTER) return { mode: "this_computer", adapter: {} };
  const option = options.find((o) => o.value === value);
  return { mode: "adapter", adapter: option ? { ...option.identity } : {} };
}

/** Under the picker, in one sentence: who can connect with this choice. */
export function listenHelp(mode: ListenMode, name: string): string {
  if (mode === "any") {
    return "Touch panels, phones and computers on any network this computer is connected to can open the panel and the Programmer.";
  }
  if (mode === "adapter") {
    return `Only devices on the ${name || "chosen"} network, and this computer itself, can open the panel and the Programmer.`;
  }
  return "Only this computer's own screen and browser can open the panel and the Programmer.";
}

/** The choice as it reads inside a sentence: "Any network", "Ethernet only". */
export function listenLabel(mode: ListenMode, name: string): string {
  if (mode === "any") return LABEL_ANY;
  if (mode === "this_computer") return LABEL_THIS_COMPUTER;
  return name ? `${name} only` : "One network only";
}

/** Whether two listen choices are the same choice. */
export function sameListenChoice(
  a: { mode: ListenMode | ""; adapter?: AdapterIdentity | null },
  b: { mode: ListenMode | ""; adapter?: AdapterIdentity | null },
): boolean {
  if (a.mode !== b.mode) return false;
  if (a.mode !== "adapter") return true;
  const ka = identityKey(a.adapter ?? {});
  const kb = identityKey(b.adapter ?? {});
  return ka === kb;
}

// ---------------------------------------------------------------------------
// Where the restart dialog waits
// ---------------------------------------------------------------------------

export interface RestartPlan {
  /** The browser cannot reach any address under the new choice from here:
   *  the safety net is armed, and the dialog hands over instead of failing. */
  cutOff: boolean;
  /** The hosts to wait at, best first ("" means the page's own host). */
  hosts: string[];
}

/** Where to wait for the server after a change of who can connect.
 *
 *  The page's own address when the choice keeps it; `127.0.0.1` when the
 *  browser is on this computer at an address the choice leaves out; the new
 *  choice's own addresses when this browser is cut off (it may still reach
 *  one of them, on a computer connected to both networks).
 */
export function restartPlan(preview: {
  serves_this_address: boolean;
  keeps_this_connection: boolean;
  panel_addresses: string[];
} | null): RestartPlan {
  if (!preview || preview.serves_this_address) return { cutOff: false, hosts: [""] };
  if (preview.keeps_this_connection) return { cutOff: false, hosts: ["127.0.0.1"] };
  return { cutOff: true, hosts: [...preview.panel_addresses] };
}
