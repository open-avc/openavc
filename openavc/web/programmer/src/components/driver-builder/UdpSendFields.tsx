/** A command's `udp:` block in the Driver Builder: one datagram sent beside
 *  the device's own connection, either a message or a Wake-on-LAN magic
 *  packet, to the device or another host. The runtime half is `send_udp` and
 *  `_side_sender` in the YAML driver runtime.
 *
 *  Exactly one of payload and magic packet is set, chosen up front, because
 *  the runtime refuses a block carrying both. A field cleared is removed from
 *  the block, never written empty. */

import type { CSSProperties } from "react";
import type { DriverDefinition, DriverUdpSend } from "../../api/types";
import { BUILTIN_CONFIG_KEYS } from "./configSchemaHelpers";
import { FIELD_HINT } from "./commandFieldParts";

type Update = (partial: Partial<DriverUdpSend>) => void;

/** The block's captions, smaller than the card's own. */
const UDP_LABEL: CSSProperties = {
  display: "block",
  fontSize: "11px",
  color: "var(--text-muted)",
  marginBottom: 2,
};

/** Where a command's MAC address comes from: a config field (one typed in
 *  under Edit Device, so a device that has never connected can still be
 *  woken). When a state variable of the same name exists, the runtime reads
 *  that first once the device has reported its MAC, and the label says so. */
export function macFieldOptions(
  draft: Pick<DriverDefinition, "state_variables" | "config_schema" | "default_config">,
): { value: string; label: string }[] {
  const stateKeys = new Set(Object.keys(draft.state_variables ?? {}));
  const configKeys = Array.from(
    new Set([
      ...Object.keys(draft.config_schema ?? {}),
      ...Object.keys(draft.default_config ?? {}),
    ]),
  ).filter((k) => !BUILTIN_CONFIG_KEYS.has(k));
  return configKeys.map((k) => ({
    value: k,
    label: stateKeys.has(k)
      ? `${k} (config field, learned from the device once connected)`
      : `${k} (config field)`,
  }));
}

/** The port box's text as the block carries it: digits are a number, anything
 *  else (a `{config_field}`) is kept as text, empty removes it. */
export function udpPort(text: string): number | string | undefined {
  const raw = text.trim();
  if (raw === "") return undefined;
  const n = Number(raw);
  return /^\d+$/.test(raw) && Number.isFinite(n) ? n : raw;
}

type MacOption = { value: string; label: string };

/** A message or a magic packet. Switching drops the other's fields; a magic
 *  packet starts on the first MAC field there is. */
function UdpFormChoice({
  udp,
  macOptions,
  update,
}: {
  udp: DriverUdpSend;
  macOptions: MacOption[];
  update: Update;
}) {
  const isMagic = udp.magic_packet !== undefined;
  return (
    <div
      style={{
        display: "flex",
        gap: "var(--space-md)",
        fontSize: "var(--font-size-sm)",
        color: "var(--text-secondary)",
        marginBottom: "var(--space-sm)",
      }}
    >
      <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
        <input
          type="radio"
          name="udp-form"
          checked={!isMagic}
          onChange={() =>
            update({ magic_packet: undefined, payload: udp.payload ?? "" })
          }
        />
        A message
      </label>
      <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
        <input
          type="radio"
          name="udp-form"
          checked={isMagic}
          onChange={() =>
            update({
              payload: undefined,
              broadcast: undefined,
              magic_packet: udp.magic_packet ?? macOptions[0]?.value ?? "",
            })
          }
        />
        A Wake-on-LAN magic packet
      </label>
    </div>
  );
}

/** The config field a magic packet takes its MAC from; one the driver no
 *  longer declares is still shown, so it is not silently replaced. */
function MacField({
  magicPacket,
  macOptions,
  update,
}: {
  magicPacket: string;
  macOptions: MacOption[];
  update: Update;
}) {
  return (
    <div style={{ marginBottom: "var(--space-sm)" }}>
      <label style={UDP_LABEL}>MAC address from</label>
      <select
        aria-label="MAC address from"
        value={magicPacket}
        onChange={(e) => update({ magic_packet: e.target.value })}
        style={{ width: "100%" }}
      >
        {!macOptions.some((o) => o.value === magicPacket) && (
          <option value={magicPacket}>{magicPacket ? magicPacket : "Pick a field"}</option>
        )}
        {macOptions.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <div style={FIELD_HINT}>
        The config field that holds the device&apos;s MAC address, so it can
        be typed in under Edit Device. Declare a state variable of the same
        name too and the MAC the device reports on connect is used first.
        The packet goes to the broadcast address and straight to the
        device.
      </div>
    </div>
  );
}

/** The message a datagram carries. */
function MessageField({ payload, update }: { payload: string | undefined; update: Update }) {
  return (
    <div style={{ marginBottom: "var(--space-sm)" }}>
      <label style={UDP_LABEL}>Message</label>
      <input
        aria-label="UDP message"
        value={payload ?? ""}
        onChange={(e) => update({ payload: e.target.value })}
        placeholder="e.g., {variable}:{value}"
        style={{ width: "100%", fontFamily: "var(--font-mono)" }}
      />
      <div style={FIELD_HINT}>
        Sent exactly as written, without the driver&apos;s command framing.
        Use {"{param_name}"} for parameter placeholders and \r, \n or
        \xHH for control characters.
      </div>
    </div>
  );
}

/** Where the datagram goes: a host (the device's own when empty) and a port. */
function UdpTargetFields({
  udp,
  isMagic,
  update,
}: {
  udp: DriverUdpSend;
  isMagic: boolean;
  update: Update;
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: "var(--space-md)",
      }}
    >
      <div>
        <label style={UDP_LABEL}>Host</label>
        <input
          aria-label="UDP host"
          value={udp.host ?? ""}
          onChange={(e) => update({ host: e.target.value || undefined })}
          placeholder="the device's host"
          style={{ width: "100%", fontFamily: "var(--font-mono)" }}
        />
        <div style={FIELD_HINT}>
          Leave empty to send to the device. A {"{config_field}"} works here
          too.
        </div>
      </div>
      <div>
        <label style={UDP_LABEL}>Port</label>
        <input
          aria-label="UDP port"
          value={udp.port === undefined ? "" : String(udp.port)}
          onChange={(e) => update({ port: udpPort(e.target.value) })}
          placeholder={isMagic ? "9" : "e.g., 5000 or {udp_port}"}
          style={{ width: "100%", fontFamily: "var(--font-mono)" }}
        />
        <div style={FIELD_HINT}>
          {isMagic
            ? "Wake-on-LAN listens on 9 unless the device says otherwise."
            : "A number, or a {config_field} holding one, so the port can be set per device."}
        </div>
      </div>
    </div>
  );
}

/** The fields of a command's udp block: what goes out and where. */
export function UdpSendFields({
  udp,
  draft,
  onChange,
}: {
  udp: DriverUdpSend;
  draft: DriverDefinition;
  onChange: (udp: DriverUdpSend) => void;
}) {
  const isMagic = udp.magic_packet !== undefined;
  const macOptions = macFieldOptions(draft);
  const update: Update = (partial) => {
    const next: Record<string, unknown> = { ...udp, ...partial };
    for (const k of Object.keys(next)) {
      if (next[k] === undefined) delete next[k];
    }
    onChange(next as DriverUdpSend);
  };

  return (
    <div
      style={{
        marginBottom: "var(--space-md)",
        padding: "var(--space-sm)",
        border: "1px solid var(--border-color)",
        borderRadius: "var(--border-radius)",
      }}
    >
      <label style={UDP_LABEL}>Send over UDP</label>
      <UdpFormChoice udp={udp} macOptions={macOptions} update={update} />
      {isMagic ? (
        <MacField magicPacket={udp.magic_packet ?? ""} macOptions={macOptions} update={update} />
      ) : (
        <MessageField payload={udp.payload} update={update} />
      )}
      <UdpTargetFields udp={udp} isMagic={isMagic} update={update} />
      {!isMagic && (
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            marginTop: "var(--space-sm)",
          }}
        >
          <input
            type="checkbox"
            checked={!!udp.broadcast}
            onChange={(e) => update({ broadcast: e.target.checked || undefined })}
          />
          Broadcast to the whole network instead of the host
        </label>
      )}
    </div>
  );
}
