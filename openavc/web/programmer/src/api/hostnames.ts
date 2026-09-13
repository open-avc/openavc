/** THE name rule on this side: is a hostname already resolvable?
 *
 *  The server's copy is `openavc/utils/hostnames.py`, and the rule is one
 *  line: `.local` goes on a bare machine name only. The OS hostname's shape
 *  is the platform's business -- bare on an appliance image and on most
 *  Linux boxes (`openavc`), already dotted on macOS
 *  (`Aarons-MacBook-Air.local`), a full domain name on a managed host -- so
 *  appending unconditionally produced `Aarons-MacBook-Air.local.local`,
 *  which resolves nowhere. Two surfaces did that: the Dashboard's Panel
 *  Access card, and the hostname field in Settings > Network, whose own
 *  validator deliberately accepts a dotted name.
 *
 *  It stays a copy rather than an import because both callers run on state
 *  the server has not answered for -- the card prefers the browser's own
 *  address over the reported one, and the settings field is echoing what
 *  somebody is still typing.
 */
export function resolvableHostname(raw: string): string {
  const host = raw.trim().replace(/\.+$/, "");
  if (!host || host.split(".")[0].toLowerCase() === "localhost") return "";
  return host.includes(".") ? host : `${host}.local`;
}
