/** The fields of a command's card about the device being away because of the
 *  command: it runs while the device is offline (`available_offline`), it
 *  restarts the device for a while (`restarts_device_for`), it puts the
 *  device to sleep until another command wakes it (`sleeps_device`).
 *
 *  They are one module because they depend on each other: only a command that
 *  runs while the device is offline can wake it, and the device is reported
 *  waking up for that command's restart window. The sleep field names the
 *  other two by their titles, which are the constants below. */

import { useState } from "react";
import type { DriverCommandDef } from "../../api/types";
import { FIELD_HINT, FIELD_LABEL, FIELD_WARNING, FlagField } from "./commandFieldParts";

const RUNS_OFFLINE = "Runs while the device is offline";
const RESTARTS_FOR = "Restarts the device for";

/** `available_offline`: ticked is `true`, unticked removes it. */
export function OfflineFlag({
  value,
  onChange,
}: {
  value: boolean | undefined;
  onChange: (availableOffline: true | undefined) => void;
}) {
  return (
    <FlagField
      checked={!!value}
      onChange={(checked) => onChange(checked || undefined)}
      title={RUNS_OFFLINE}
      style={{ marginBottom: "var(--space-md)" }}
    >
      Normally a command to a disconnected device is refused.
      Tick this only when the command needs no live connection,
      like a Wake-on-LAN power on, which sends a magic packet
      rather than talking over the dead control link. A Quick
      Action button for it also stays available while the
      device is offline.
    </FlagField>
  );
}

/** What the restart window box holds as seconds: whole, at most 600; empty,
 *  not a number or not positive removes it. */
export function restartSeconds(raw: string): number | undefined {
  const text = raw.trim();
  if (text === "") return undefined;
  const n = Number(text);
  return Number.isFinite(n) && n > 0 ? Math.min(600, Math.round(n)) : undefined;
}

/** `restarts_device_for`, in seconds. */
export function RestartWindowField({
  value,
  onChange,
}: {
  value: number | undefined;
  onChange: (seconds: number | undefined) => void;
}) {
  return (
    <label
      style={{
        display: "block",
        fontSize: "var(--font-size-sm)",
        color: "var(--text-secondary)",
        marginBottom: "var(--space-md)",
      }}
    >
      <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {RESTARTS_FOR}
        <input
          type="number"
          min={1}
          max={600}
          placeholder="seconds"
          value={value ?? ""}
          onChange={(e) => onChange(restartSeconds(e.target.value))}
          style={{ width: 90 }}
        />
        seconds
      </span>
      <div style={FIELD_HINT}>
        Leave this empty unless sending the command restarts the
        device and takes its control channel off the network with
        it. For that long the device is reported as restarting
        instead of as a fault: nothing alerts on it, and a panel
        says it is coming back rather than telling somebody to check
        the network. Measure the gap on real hardware and round up.
        A device that stays reachable in standby, or a projector
        that reports a warming state, needs nothing here.
      </div>
    </label>
  );
}

/** What the sleep field offers and says for command `commandId`: the wake
 *  command named (`current`, "" for none), the commands that can wake the
 *  device (the others that run while it is offline), whether the one named is
 *  among them, and what it still lacks to wake it. */
function wakeChoice(
  commandId: string,
  value: string | undefined,
  commands: Record<string, DriverCommandDef>,
) {
  const current = typeof value === "string" ? value : "";
  const wakers = Object.entries(commands).filter(
    ([id, c]) => id !== commandId && c.available_offline === true,
  );
  const picked = current ? commands[current] : undefined;
  const needs: string[] = [];
  if (picked && !picked.available_offline) needs.push(`"${RUNS_OFFLINE}" ticked`);
  if (picked && !picked.restarts_device_for) needs.push(`"${RESTARTS_FOR}" set`);
  return { current, wakers, listed: wakers.some(([id]) => id === current), picked, needs };
}

type WakeChoice = ReturnType<typeof wakeChoice>;

const optionText = (id: string, c: DriverCommandDef | undefined) =>
  c?.label ? `${c.label} (${id})` : id;

/** What the sleep field says under the picker: which commands it lists, or
 *  that none can wake the device yet, and what the one named lacks. */
function WakeNotes({ choice: { current, wakers, picked, needs } }: { choice: WakeChoice }) {
  return (
    <>
      {wakers.length === 0 ? (
        <div style={FIELD_WARNING}>
          {`No command can wake it yet. On the command that wakes the device, tick "${RUNS_OFFLINE}" and fill in "${RESTARTS_FOR}".`}
        </div>
      ) : (
        <div style={FIELD_HINT}>{`Lists the commands with "${RUNS_OFFLINE}" ticked.`}</div>
      )}
      {current && !picked && (
        <div style={FIELD_WARNING}>
          &quot;{current}&quot; is not a command in this driver.
        </div>
      )}
      {needs.length > 0 && (
        <div style={FIELD_WARNING}>
          {picked?.label || current} also needs {needs.join(" and ")}.
        </div>
      )}
    </>
  );
}

/** The Woken by picker and what it says under it. */
function WakePicker({
  choice,
  onChange,
}: {
  choice: WakeChoice;
  onChange: (sleepsDevice: string | undefined) => void;
}) {
  const { current, wakers, listed, picked } = choice;
  return (
    <div style={{ marginTop: "var(--space-sm)" }}>
      <label style={FIELD_LABEL}>Woken by</label>
      <select
        aria-label="Woken by"
        value={current}
        onChange={(e) => onChange(e.target.value || undefined)}
        style={{ width: "100%" }}
      >
        {!listed && (
          <option value={current}>
            {current ? optionText(current, picked) : "Pick a command"}
          </option>
        )}
        {wakers.map(([id, c]) => (
          <option key={id} value={id}>
            {optionText(id, c)}
          </option>
        ))}
      </select>
      <WakeNotes choice={choice} />
    </div>
  );
}

/** A command's `sleeps_device`: the id of the command that wakes the device
 *  after this one puts it to sleep. Unticked removes the key; ticking picks
 *  the first command that runs while the device is offline, the only kind
 *  that can wake it. A value naming any other command (an imported driver, or
 *  a wake command whose offline box was unticked since) is still shown, so it
 *  is not silently replaced, and what it is missing is said under it. */
export function SleepsDeviceField({
  commandId,
  value,
  commands,
  onChange,
}: {
  commandId: string;
  value: string | undefined;
  commands: Record<string, DriverCommandDef>;
  onChange: (sleepsDevice: string | undefined) => void;
}) {
  const choice = wakeChoice(commandId, value, commands);
  // Ticked with nothing to pick yet: kept here until a wake command exists,
  // because an empty `sleeps_device` is not a value the driver can carry.
  const [ticked, setTicked] = useState(choice.current !== "");
  const enabled = ticked || choice.current !== "";

  return (
    <div style={{ marginBottom: "var(--space-md)" }}>
      <FlagField
        checked={enabled}
        onChange={(checked) => {
          setTicked(checked);
          if (!checked) onChange(undefined);
          else if (choice.wakers.length > 0) onChange(choice.wakers[0][0]);
        }}
        title="Puts the device to sleep"
      >
        Tick this when sending the command takes the device off the network
        until another command wakes it, like a display whose standby closes
        its control port or a computer told to shut down. While it is asleep
        it is not reported as a fault: nothing alerts on it, and its page and
        the panel say which command wakes it. Leave this unticked when the
        device stays reachable in standby.
      </FlagField>
      {enabled && <WakePicker choice={choice} onChange={onChange} />}
    </div>
  );
}
