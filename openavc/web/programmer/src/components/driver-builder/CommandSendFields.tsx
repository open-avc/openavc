/** How a command is sent, as its card in the Driver Builder edits it: the form
 *  for the driver's transport (a line of the device's protocol, an OSC
 *  message, an HTTP request) or, on any transport, a UDP datagram beside it.
 *
 *  The card picks the form by the driver's transport and the udp block, where
 *  the runtime picks by the command's own fields (an address is OSC, a path or
 *  method HTTP), as `commandRoute` in validateDriver.ts does. Ticking Send over
 *  UDP swaps the form rather than adding to it, because the runtime takes a
 *  udp block before it looks at the other shapes. */

import type { DriverCommandDef, DriverDefinition } from "../../api/types";
import { type CommandUpdate } from "./commandEdits";
import { FIELD_HINT, FIELD_LABEL, FlagField } from "./commandFieldParts";
import { HttpCommandFields } from "./HttpCommandFields";
import { OscArgsEditor } from "./OscArgsEditor";
import { UdpSendFields } from "./UdpSendFields";

/** An OSC command: its address and typed arguments. */
function OscSendFields({ cmd, onUpdate }: { cmd: DriverCommandDef; onUpdate: CommandUpdate }) {
  return (
    <div style={{ marginBottom: "var(--space-md)" }}>
      <label style={FIELD_LABEL}>OSC Address</label>
      <input
        value={cmd.address ?? ""}
        onChange={(e) => onUpdate({ address: e.target.value })}
        placeholder='e.g., /ch/01/mix/fader'
        style={{ width: "100%", fontFamily: "var(--font-mono)" }}
      />
      <div style={FIELD_HINT}>
        OSC address path. Use {"{param_name}"} for parameter substitution.
      </div>
      <OscArgsEditor
        args={cmd.args ?? []}
        onChange={(args) => onUpdate({ args })}
      />
    </div>
  );
}

/** A line of the device's protocol, and (when the driver frames its
 *  commands) the per-command opt-out of that framing. */
function LineSendFields({
  cmd,
  framed,
  onUpdate,
}: {
  cmd: DriverCommandDef;
  framed: boolean;
  onUpdate: CommandUpdate;
}) {
  return (
    <div style={{ marginBottom: "var(--space-md)" }}>
      <label style={FIELD_LABEL}>Command String</label>
      <input
        value={cmd.send ?? ""}
        onChange={(e) => onUpdate({ send: e.target.value })}
        placeholder="e.g., %1POWR {value}\r"
        style={{ width: "100%", fontFamily: "var(--font-mono)" }}
      />
      <div style={FIELD_HINT}>
        Use {"{param_name}"} for parameter placeholders. Use \r, \n
        for control characters.
      </div>
      {framed && (
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
            checked={!!cmd.raw}
            onChange={(e) => onUpdate({ raw: e.target.checked || undefined })}
          />
          Send raw: skip the driver&apos;s command framing for this
          command
        </label>
      )}
    </div>
  );
}

/** The fields that say how `cmd` goes out, for the form it is sent in. */
export function CommandSendFields({
  cmd,
  draft,
  onUpdate,
}: {
  cmd: DriverCommandDef;
  draft: DriverDefinition;
  onUpdate: CommandUpdate;
}) {
  if (cmd.udp) {
    return <UdpSendFields udp={cmd.udp} draft={draft} onChange={(udp) => onUpdate({ udp })} />;
  }
  if (draft.transport === "osc") return <OscSendFields cmd={cmd} onUpdate={onUpdate} />;
  if (draft.transport === "http") return <HttpCommandFields cmd={cmd} onUpdate={onUpdate} />;
  return (
    <LineSendFields
      cmd={cmd}
      framed={!!(draft.command_prefix || draft.command_suffix)}
      onUpdate={onUpdate}
    />
  );
}

/** What ticking Send over UDP writes: a udp block in place of every other way
 *  of sending, the command's params kept; unticking puts a send string back. */
function sendOverUdp(on: boolean): Partial<DriverCommandDef> {
  if (!on) return { udp: undefined, send: "" };
  return {
    udp: { payload: "" },
    send: undefined,
    method: undefined,
    path: undefined,
    body: undefined,
    address: undefined,
    args: undefined,
  };
}

/** The Send over UDP box. */
export function UdpToggle({ on, onUpdate }: { on: boolean; onUpdate: CommandUpdate }) {
  return (
    <FlagField
      checked={on}
      onChange={(checked) => onUpdate(sendOverUdp(checked))}
      title="Send over UDP instead of the device connection"
      style={{ marginBottom: "var(--space-md)" }}
    >
      One datagram from a socket opened for the send, with no
      connection needed: a Wake-on-LAN packet to a display in
      standby, or a message to a signage presentation&apos;s UDP
      receiver. Pair it with the offline setting below when the
      device is unreachable at the time.
    </FlagField>
  );
}
